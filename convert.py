"""
Converter: Meteo-France 500 m rainfall mosaic (HDF5, ODIM) -> small PNG for the EuroScope plugin.

  * downloads the latest LAME_D_EAU mosaic (5-minute rain accumulation)
  * converts it to rain rate (mm/h) and then to an "equivalent radar intensity" in dBZ
    (Marshall-Palmer: Z = 200 * R^1.6), so the plugin thresholds in dBZ keep working
  * re-projects it from Meteo-France's polar-stereographic grid to the same Web Mercator
    grid (zoom 7) that the plugin already uses
  * writes   out/frames/<time>.png  (pixel value = dBZ, 0 = nothing, 255 = no radar data)
             out/latest.json        (frame time + grid description)
             out/preview.png        (coloured picture for a human check)

Source: Meteo-France. The API key comes from the GitHub secret MF_API_KEY (never printed).
"""
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import h5py
import numpy as np
import requests
from PIL import Image, ImageDraw
from pyproj import CRS, Transformer

BASE = "https://public-api.meteofrance.fr/public/DPRadar/v1"
KEY = os.environ.get("MF_API_KEY", "").strip()

ZOOM = 7                                     # output detail (Web Mercator zoom, ~850 m per pixel)
LAT_MIN, LAT_MAX = 41.0, 51.4                # same area as the plugin
LON_MIN, LON_MAX = -5.6, 10.0
OUT = "out"

# "Publish only when Meteo-France has a new frame"
PAGES_URL = os.environ.get("PAGES_URL", "").strip()          # your site, e.g. https://name.github.io/wx-radar/
FORCE = os.environ.get("FORCE", "").strip().lower() == "true"  # publish even if nothing is new
POLL_SECONDS = 20                                              # how often to ask Meteo-France if a new frame exists
MAX_WAIT_SECONDS = int(os.environ.get("MAX_WAIT_SECONDS", "420"))

# Storm movement / 30-minute outlook
HISTORY_MINUTES = 45                  # keep older frames on the site for this long (used to measure movement)
HORIZONS = (15, 30)                   # outlook times in minutes
WIN, STEP = 192, 96                   # window size / spacing (pixels) for the motion measurement
MIN_ENERGY = 1500.0                   # a window needs this much rain to be used
MIN_PEAK = 0.06                       # minimum quality of the match between two windows
MAX_SHIFT = 48.0                      # ignore movements bigger than this many pixels per measurement


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- Web Mercator helpers
def lon_to_px(lon, z):
    return (lon + 180.0) / 360.0 * (256.0 * (1 << z))


def lat_to_py(lat, z):
    r = math.radians(lat)
    return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * (256.0 * (1 << z))


def px_to_lon(px, z):
    return px / (256.0 * (1 << z)) * 360.0 - 180.0


def py_to_lat(py, z):
    return np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * py / (256.0 * (1 << z))))))


# ---------------------------------------------------------------- download
def download(path="sample.h5"):
    """Download the latest mosaic. Retries a few times because the server is sometimes
    unreachable for a short while (we saw 'connection refused' from GitHub's machines)."""
    if not KEY:
        log("ERROR: the secret MF_API_KEY is missing or empty.")
        sys.exit(1)
    url = BASE + "/mosaiques/METROPOLE/observations/LAME_D_EAU/produit"
    last = ""
    for attempt in range(1, 6):
        t0 = time.time()
        try:
            r = requests.get(url, headers={"apikey": KEY, "accept": "*/*"},
                             params={"maille": 500}, timeout=(15, 90))
        except requests.RequestException as e:
            last = type(e).__name__
            log(f"attempt {attempt}/5: network problem ({last}) after {time.time() - t0:.1f} s")
        else:
            log(f"attempt {attempt}/5: status {r.status_code}, {len(r.content)} bytes, {time.time() - t0:.1f} s")
            if r.status_code == 200:
                with open(path, "wb") as f:
                    f.write(r.content)
                return path
            last = f"HTTP {r.status_code}"
            log(r.text[:300])
            if r.status_code in (401, 403):
                log("The server refuses the key (expired or wrong). Generate a new key on the Meteo-France portal"
                    " and update the MF_API_KEY secret.")
                sys.exit(1)
        if attempt < 5:
            time.sleep(15 * attempt)
    log(f"ERROR: could not download the mosaic ({last}).")
    sys.exit(1)


def txt(v):
    return v.decode() if isinstance(v, (bytes, np.bytes_)) else str(v)


def parse_time(date, tm):
    return datetime.strptime(txt(date) + txt(tm), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- "is there a new frame?"
def set_output(published):
    """Tell the GitHub workflow whether there is something to publish."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"published={'true' if published else 'false'}\n")


def read_published():
    """The latest.json that your site currently serves (a dict), or None if it cannot be read."""
    if not PAGES_URL:
        return None
    try:
        url = PAGES_URL.rstrip("/") + f"/latest.json?t={int(time.time() * 1000)}"
        r = requests.get(url, timeout=20, headers={"Cache-Control": "no-cache"})
        if r.status_code == 200:
            return r.json()
        log(f"published frame: site answered {r.status_code}")
    except Exception as e:
        log(f"published frame: could not read the site ({type(e).__name__})")
    return None


def published_frame_time(pub=None):
    """Unix time of the frame currently shown on your site (0 if unknown)."""
    if pub is None:
        pub = read_published()
    try:
        return int(pub.get("frame_unix", 0)) if pub else 0
    except Exception:
        return 0


def newest_frame_time():
    """Unix time of the newest 500 m mosaic that Meteo-France offers right now (None if unknown)."""
    r = requests.get(BASE + "/mosaiques/METROPOLE/observations/LAME_D_EAU",
                     headers={"apikey": KEY, "accept": "application/json"}, timeout=(10, 30))
    if r.status_code in (401, 403):
        log("The server refuses the key (expired or wrong). Generate a new key on the Meteo-France portal"
            " and update the MF_API_KEY secret.")
        sys.exit(1)
    r.raise_for_status()
    best = None
    for link in r.json().get("links", []):
        vt = link.get("validity_time")
        if vt and "maille=500" in link.get("href", ""):
            best = vt
    if best is None:
        for link in r.json().get("links", []):
            if link.get("validity_time"):
                best = link["validity_time"]
    if best is None:
        return None
    return int(datetime.strptime(best, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def wait_for_new_frame(published):
    """Check every POLL_SECONDS whether Meteo-France has a frame newer than the published one.
    Returns True as soon as there is one, False if none appears within MAX_WAIT_SECONDS."""
    deadline = time.time() + MAX_WAIT_SECONDS
    n = 0
    while True:
        n += 1
        newest = None
        try:
            newest = newest_frame_time()
        except SystemExit:
            raise
        except Exception as e:
            log(f"check {n}: could not ask Meteo-France ({type(e).__name__})")
        else:
            def hhmm(t):
                return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%MZ") if t else "none"
            log(f"check {n}: published {hhmm(published)}, Meteo-France newest {hhmm(newest)}")
        if newest and newest > published:
            return True
        if time.time() + POLL_SECONDS > deadline:
            return False
        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- storm movement and outlook
def intensity(a):
    """dBZ picture (0 = nothing, 255 = no radar) -> 0..45 'how much rain' picture."""
    x = a.astype(np.float32)
    x[a == 255] = 0.0
    return np.clip(x - 15.0, 0.0, 45.0)


def _peak(r):
    """Position of the highest value of a correlation picture, with sub-pixel precision."""
    n0, n1 = r.shape
    py, px = np.unravel_index(int(np.argmax(r)), r.shape)
    val = float(r[py, px])

    def sub(m, c, p):
        den = m - 2.0 * c + p
        return 0.0 if abs(den) < 1e-9 else 0.5 * (m - p) / den

    dy = py + sub(r[(py - 1) % n0, px], r[py, px], r[(py + 1) % n0, px])
    dx = px + sub(r[py, (px - 1) % n1], r[py, px], r[py, (px + 1) % n1])
    if dy > n0 / 2:
        dy -= n0
    if dx > n1 / 2:
        dx -= n1
    return dy, dx, val


def estimate_flow(prev, cur):
    """How far did the rain move between two frames? Returns (flow_y, flow_x, info) where the
    flow is in pixels per measurement (full-size pictures), or None if there is not enough rain."""
    a, b = intensity(prev), intensity(cur)
    H, W = a.shape
    xs = list(range(0, W - WIN + 1, STEP))
    ys = list(range(0, H - WIN + 1, STEP))
    if xs[-1] != W - WIN:
        xs.append(W - WIN)
    if ys[-1] != H - WIN:
        ys.append(H - WIN)
    han = np.hanning(WIN).astype(np.float32)
    win = np.outer(han, han)
    gy = np.full((len(ys), len(xs)), np.nan, dtype=np.float64)
    gx = np.full_like(gy, np.nan)
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            pa, pb = a[y:y + WIN, x:x + WIN], b[y:y + WIN, x:x + WIN]
            if pa.sum() < MIN_ENERGY or pb.sum() < MIN_ENERGY:
                continue
            fa = np.fft.rfft2((pa - pa.mean()) * win)
            fb = np.fft.rfft2((pb - pb.mean()) * win)
            r = fb * np.conj(fa)
            r /= (np.abs(r) + 1e-6)
            corr = np.fft.irfft2(r, s=(WIN, WIN))
            dy, dx, val = _peak(corr)
            if val < MIN_PEAK or (dy * dy + dx * dx) ** 0.5 > MAX_SHIFT:
                continue
            gy[j, i], gx[j, i] = dy, dx
    ok = ~np.isnan(gx)
    if ok.sum() < 3:
        return None
    my, mx = float(np.median(gy[ok])), float(np.median(gx[ok]))
    far = ok & (np.hypot(gy - my, gx - mx) > 25.0)       # drop windows that disagree wildly
    gy[far] = np.nan
    gx[far] = np.nan
    ok = ~np.isnan(gx)
    if ok.sum() < 3:
        return None
    my, mx = float(np.median(gy[ok])), float(np.median(gx[ok]))

    # fill the windows without rain with a weighted average of their neighbours
    cj, ci = np.mgrid[0:len(ys), 0:len(xs)]
    vj, vi = cj[ok], ci[ok]
    vy, vx = gy[ok], gx[ok]
    for j in range(len(ys)):
        for i in range(len(xs)):
            if ok[j, i]:
                continue
            wgt = np.exp(-((vj - j) ** 2 + (vi - i) ** 2) / (2 * 2.5 ** 2))
            sw = wgt.sum()
            if sw < 1e-3:
                gy[j, i], gx[j, i] = my, mx
            else:
                gy[j, i], gx[j, i] = (wgt * vy).sum() / sw, (wgt * vx).sum() / sw

    # smooth interpolation from the window grid to every pixel
    cxs = np.array(xs, dtype=np.float64) + WIN / 2.0
    cys = np.array(ys, dtype=np.float64) + WIN / 2.0
    px_ = np.arange(W, dtype=np.float64)
    py_ = np.arange(H, dtype=np.float64)

    def full(g):
        tmp = np.empty((len(ys), W), dtype=np.float32)
        for j in range(len(ys)):
            tmp[j] = np.interp(px_, cxs, g[j])
        out = np.empty((H, W), dtype=np.float32)
        for x in range(W):
            out[:, x] = np.interp(py_, cys, tmp[:, x])
        return out

    return full(gy), full(gx), {"median_dy": my, "median_dx": mx, "windows": int(ok.sum())}


def advect(cur, fy, fx, k):
    """Move the rain forward: the picture k measurements into the future (k can be 2.0 etc.)."""
    H, W = cur.shape
    yy, xx = np.mgrid[0:H, 0:W]
    sy = np.rint(yy - k * fy).astype(np.int32)
    sx = np.rint(xx - k * fx).astype(np.int32)
    ok = (sy >= 0) & (sy < H) & (sx >= 0) & (sx < W)
    base = np.where(cur == 255, 0, cur)
    out = np.zeros_like(cur)
    out[ok] = base[sy[ok], sx[ok]]
    out[cur == 255] = 255            # keep the "no radar here" areas as they are
    return out


def collect_history(pub, cur_unix):
    """Older frames that are already on your site (newest last)."""
    items = []
    if not PAGES_URL or not pub:
        return items
    entries = list(pub.get("history", []))
    if pub.get("frame_unix") and pub.get("png"):
        entries.append({"frame_unix": pub["frame_unix"], "png": pub["png"]})
    seen = set()
    for e in entries:
        try:
            u, name = int(e.get("frame_unix", 0)), str(e.get("png", ""))
        except Exception:
            continue
        if not u or not name or u in seen or u >= cur_unix or cur_unix - u > HISTORY_MINUTES * 60:
            continue
        seen.add(u)
        try:
            r = requests.get(PAGES_URL.rstrip("/") + "/" + name, timeout=30)
            r.raise_for_status()
            items.append({"unix": u, "png": name, "data": r.content})
        except Exception as ex:
            log(f"history: could not get {name} ({type(ex).__name__})")
    items.sort(key=lambda d: d["unix"])
    return items


# ---------------------------------------------------------------- main conversion
def main():
    t_start = time.time()
    published = 0
    pub = read_published()
    if FORCE:
        log("FORCE is on: publishing without waiting for a new frame")
    else:
        published = published_frame_time(pub)
        if not wait_for_new_frame(published):
            log("No new Meteo-France frame appeared in time. Nothing to publish this time.")
            set_output(False)
            return
    path = download()

    with h5py.File(path, "r") as f:
        raw = f["dataset1/data1/data"][...]
        w = f["dataset1/data1/what"].attrs
        gain, offset = float(w["gain"]), float(w["offset"])
        nodata, undetect = int(w["nodata"]), int(w["undetect"])
        wh = f["where"].attrs
        projdef = txt(wh["projdef"])
        ul_lat, ul_lon = float(wh["UL_lat"]), float(wh["UL_lon"])
        xscale, yscale = float(wh["xscale"]), float(wh["yscale"])
        xsize, ysize = int(wh["xsize"]), int(wh["ysize"])
        corners = {k: float(wh[k]) for k in ("LL_lat", "LL_lon", "LR_lat", "LR_lon", "UR_lat", "UR_lon")}
        dw = f["dataset1/what"].attrs
        t_end = parse_time(dw["enddate"], dw["endtime"])
        t_begin = parse_time(dw["startdate"], dw["starttime"])

    if not FORCE and int(t_end.timestamp()) <= published:
        log("The downloaded frame is not newer than the published one. Nothing to publish.")
        set_output(False)
        return

    minutes = (t_end - t_begin).total_seconds() / 60.0
    if minutes <= 0:
        log("ERROR: strange accumulation period", minutes)
        sys.exit(1)
    log(f"frame: {t_begin:%H:%M} -> {t_end:%H:%M} UTC ({minutes:.0f} min accumulation), grid {xsize}x{ysize}, {xscale:.0f} m")

    # ---- projection + self-check of the georeferencing
    crs_src = CRS.from_proj4(projdef)
    fwd = Transformer.from_crs(CRS.from_epsg(4326), crs_src, always_xy=True)
    inv = Transformer.from_crs(crs_src, CRS.from_epsg(4326), always_xy=True)
    ulx, uly = fwd.transform(ul_lon, ul_lat)
    right, bottom = ulx + xsize * xscale, uly - ysize * yscale
    worst = 0.0
    for name, (x, y) in {"LL": (ulx, bottom), "LR": (right, bottom), "UR": (right, uly)}.items():
        lon, lat = inv.transform(x, y)
        dlat, dlon = abs(lat - corners[name + "_lat"]), abs(lon - corners[name + "_lon"])
        worst = max(worst, dlat, dlon)
        log(f"corner check {name}: computed ({lat:.4f}, {lon:.4f}) vs file ({corners[name + '_lat']:.4f}, {corners[name + '_lon']:.4f})")
    if worst > 0.05:
        log(f"ERROR: georeferencing does not match the file's corners (off by {worst:.3f} degrees). Not publishing.")
        sys.exit(1)
    log(f"georeferencing OK (worst corner difference {worst:.5f} deg)")

    # ---- raw value -> dBZ (uint8). 0 = nothing, 255 = no radar data
    lut = np.zeros(65536, dtype=np.uint8)
    v = np.arange(65536, dtype=np.float64)
    rate = (v * gain + offset) * (60.0 / minutes)                 # mm/h
    with np.errstate(divide="ignore", invalid="ignore"):
        dbz = 10.0 * np.log10(200.0 * np.power(rate, 1.6))
    ok = (rate > 0) & np.isfinite(dbz) & (dbz >= 15.0)
    lut[ok] = np.clip(np.round(dbz[ok]), 15, 95).astype(np.uint8)
    lut[undetect] = 0
    lut[nodata] = 255
    src = lut[raw]

    # ---- 2x2 max-pooling (500 m -> 1 km) so that small intense cells do not vanish
    assert ysize % 2 == 0 and xsize % 2 == 0
    nd = src == 255
    val = np.where(nd, 0, src)
    pv = val.reshape(ysize // 2, 2, xsize // 2, 2).max(axis=(1, 3))
    pnd = nd.reshape(ysize // 2, 2, xsize // 2, 2).all(axis=(1, 3))
    pooled = np.where(pnd, 255, pv).astype(np.uint8)
    pscale = xscale * 2.0
    ph, pw = pooled.shape

    # ---- target grid: same Web Mercator tile block as the plugin
    x_min = int(math.floor(lon_to_px(LON_MIN, ZOOM) / 256.0))
    x_max = int(math.floor(lon_to_px(LON_MAX, ZOOM) / 256.0))
    y_min = int(math.floor(lat_to_py(LAT_MAX, ZOOM) / 256.0))
    y_max = int(math.floor(lat_to_py(LAT_MIN, ZOOM) / 256.0))
    W, H = (x_max - x_min + 1) * 256, (y_max - y_min + 1) * 256
    cols = px_to_lon(x_min * 256 + np.arange(W) + 0.5, ZOOM)
    rows = py_to_lat(y_min * 256 + np.arange(H) + 0.5, ZOOM)
    LON, LAT = np.meshgrid(cols, rows)
    X, Y = fwd.transform(LON, LAT)
    ci = np.floor((X - ulx) / pscale).astype(np.int64)
    ri = np.floor((uly - Y) / pscale).astype(np.int64)
    inside = (ci >= 0) & (ci < pw) & (ri >= 0) & (ri < ph)
    out = np.full((H, W), 255, dtype=np.uint8)
    out[inside] = pooled[ri[inside], ci[inside]]
    log(f"output grid: {W}x{H} pixels, tiles x {x_min}-{x_max}, y {y_min}-{y_max} at zoom {ZOOM}")

    # ---- statistics
    covered = out != 255
    log(f"radar coverage inside the France box: {100.0 * covered.mean():.1f}%")
    for thr in (30, 35, 40, 45, 50):
        log(f"  pixels >= {thr} dBZ: {int((out[covered] >= thr).sum())}")
    log(f"  maximum: {int(out[covered].max()) if covered.any() else 0} dBZ")

    # ---- write files
    os.makedirs(os.path.join(OUT, "frames"), exist_ok=True)
    stamp = t_end.strftime("%Y%m%dT%H%MZ")
    png_rel = f"frames/{stamp}.png"
    Image.fromarray(out, mode="L").save(os.path.join(OUT, png_rel), optimize=True)

    # ---- storm movement and the 15 / 30 minute outlook (never allowed to stop the publishing)
    extra = {}
    kept = []
    try:
        cur_unix = int(t_end.timestamp())
        hist = collect_history(pub, cur_unix)
        for h in hist:                                   # keep them on the new site for the next run
            dst = os.path.join(OUT, h["png"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(h["data"])
            kept.append({"frame_unix": h["unix"], "png": h["png"]})
        cand = [h for h in hist if 300 <= cur_unix - h["unix"] <= 1500]
        if cand:
            ref = min(cand, key=lambda h: abs((cur_unix - h["unix"]) - 900))
            base_min = (cur_unix - ref["unix"]) / 60.0
            prev = np.asarray(Image.open(io.BytesIO(ref["data"])).convert("L"))
            if prev.shape == out.shape:
                res = estimate_flow(prev, out)
                if res is None:
                    log("movement: not enough rain to measure movement, no outlook this time")
                else:
                    fy, fx, info = res
                    my, mx = info["median_dy"], info["median_dx"]
                    mpp = 156543.03392 * math.cos(math.radians(46.5)) / (1 << ZOOM)     # metres per pixel
                    ms = math.hypot(mx, my) * mpp / (base_min * 60.0)
                    kt = ms * 1.94384
                    toward = (math.degrees(math.atan2(mx, -my)) + 360.0) % 360.0         # image up = north
                    log(f"movement: {kt:.0f} kt towards {toward:.0f} deg (measured over {base_min:.0f} min, {info['windows']} windows)")
                    for hmin in HORIZONS:
                        fc = advect(out, fy, fx, hmin / base_min)
                        name = f"frames/{stamp}_f{hmin}.png"
                        Image.fromarray(fc, mode="L").save(os.path.join(OUT, name), optimize=True)
                        extra[f"f{hmin}"] = name
                    extra["motion_kt"] = int(round(kt))
                    extra["motion_to_deg"] = int(round(toward)) % 360
                    extra["baseline_min"] = int(round(base_min))
        else:
            log("movement: no earlier frame of 5-25 minutes ago yet, no outlook this time")
    except Exception as ex:
        log(f"movement: skipped because of an error ({type(ex).__name__}: {ex})")
        extra = {}

    meta = {
        "source": "Meteo-France",
        "frame_time": t_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frame_unix": int(t_end.timestamp()),
        "png": png_rel,
        "zoom": ZOOM, "x0": x_min, "y0": y_min, "width": W, "height": H,
        "encoding": "pixel = dBZ (15-95); 0 = no echo; 255 = no radar data",
        "generated_unix": int(time.time()),
    }
    meta.update(extra)
    meta["history"] = kept
    with open(os.path.join(OUT, "latest.json"), "w") as f:
        json.dump(meta, f, indent=1)
    log("png size:", os.path.getsize(os.path.join(OUT, png_rel)), "bytes")

    # ---- preview picture for a human check (colours = plugin defaults)
    pal = np.zeros((256, 3), dtype=np.uint8)
    pal[0] = (0, 0, 0)
    pal[255] = (45, 45, 55)
    for lo, col in ((15, (40, 80, 130)), (30, (64, 160, 255)), (35, (255, 225, 0)),
                    (40, (255, 140, 0)), (45, (230, 20, 20)), (50, (200, 0, 200))):
        pal[lo:255] = col
    img = Image.fromarray(pal[out], mode="RGB")
    d = ImageDraw.Draw(img)
    for lon in range(-5, 11, 1):
        x = lon_to_px(lon, ZOOM) - x_min * 256
        d.line([(x, 0), (x, H)], fill=(70, 70, 70))
    for lat in range(42, 52, 1):
        y = lat_to_py(lat, ZOOM) - y_min * 256
        d.line([(0, y), (W, y)], fill=(70, 70, 70))
    for name, lat, lon in (("Paris", 48.857, 2.352), ("Lille", 50.629, 3.057), ("Brest", 48.390, -4.486),
                           ("Strasbourg", 48.573, 7.752), ("Lyon", 45.764, 4.836), ("Marseille", 43.297, 5.370),
                           ("Toulouse", 43.605, 1.444), ("Bordeaux", 44.838, -0.579), ("Nice", 43.710, 7.262),
                           ("Nantes", 47.218, -1.554)):
        x = lon_to_px(lon, ZOOM) - x_min * 256
        y = lat_to_py(lat, ZOOM) - y_min * 256
        d.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(255, 255, 255), width=2)
        d.text((x + 7, y - 6), name, fill=(255, 255, 255))
    d.text((8, 8), f"Meteo-France rain mosaic {t_end:%Y-%m-%d %H:%MZ}  (grey = no radar data, grid = 1 degree)",
           fill=(255, 255, 255))
    img.save(os.path.join(OUT, "preview.png"))
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset=utf-8><title>Radar frame</title>"
                "<body style='background:#111;color:#ddd;font-family:sans-serif'>"
                f"<p>Latest frame: {t_end:%Y-%m-%d %H:%M} UTC. Source: M&eacute;t&eacute;o-France.</p>"
                "<img src='preview.png' style='max-width:100%'></body>")
    set_output(True)
    log(f"done in {time.time() - t_start:.1f} s")


main()
