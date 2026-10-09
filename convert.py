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
    if not KEY:
        log("ERROR: the secret MF_API_KEY is missing or empty.")
        sys.exit(1)
    t0 = time.time()
    r = requests.get(BASE + "/mosaiques/METROPOLE/observations/LAME_D_EAU/produit",
                     headers={"apikey": KEY, "accept": "*/*"}, params={"maille": 500}, timeout=180)
    log(f"download: status {r.status_code}, {len(r.content)} bytes, {time.time() - t0:.1f} s")
    if r.status_code != 200:
        log(r.text[:1000])
        sys.exit(1)
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def txt(v):
    return v.decode() if isinstance(v, (bytes, np.bytes_)) else str(v)


def parse_time(date, tm):
    return datetime.strptime(txt(date) + txt(tm), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- main conversion
def main():
    t_start = time.time()
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
    meta = {
        "source": "Meteo-France",
        "frame_time": t_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frame_unix": int(t_end.timestamp()),
        "png": png_rel,
        "zoom": ZOOM, "x0": x_min, "y0": y_min, "width": W, "height": H,
        "encoding": "pixel = dBZ (15-95); 0 = no echo; 255 = no radar data",
        "generated_unix": int(time.time()),
    }
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
    log(f"done in {time.time() - t_start:.1f} s")


main()
