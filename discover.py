"""
Discovery script, round 2: look closely at the 500 m rainfall (LAME_D_EAU) HDF5 mosaic.
Prints NO secrets (the API key comes from the GitHub secret MF_API_KEY).
"""
import os
import sys
import time
import traceback

import numpy as np
import requests

BASE = "https://public-api.meteofrance.fr/public/DPRadar/v1"
KEY = os.environ.get("MF_API_KEY", "").strip()
if not KEY:
    print("ERROR: the secret MF_API_KEY is missing or empty.")
    sys.exit(1)
HEAD = {"apikey": KEY, "accept": "*/*"}


def title(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70, flush=True)


def fmt(attrs):
    return {k: (v.decode() if isinstance(v, (bytes, np.bytes_)) else v) for k, v in dict(attrs).items()}


def main():
    import h5py

    title("1. Download")
    t0 = time.time()
    r = requests.get(BASE + "/mosaiques/METROPOLE/observations/LAME_D_EAU/produit",
                     headers=HEAD, params={"maille": 500}, timeout=180)
    print("status:", r.status_code, " bytes:", len(r.content), " seconds:", round(time.time() - t0, 1))
    if r.status_code != 200:
        print(r.text[:1500])
        return
    with open("sample.h5", "wb") as f:
        f.write(r.content)

    with h5py.File("sample.h5", "r") as f:
        title("2. Structure (without the per-radar details)")
        print("top level:", list(f.keys()))
        print("root attrs:", fmt(f.attrs))

        def visit(name, obj):
            if name.startswith("how/node"):
                return
            kind = "DATASET" if isinstance(obj, h5py.Dataset) else "GROUP"
            extra = f" shape={obj.shape} dtype={obj.dtype}" if kind == "DATASET" else ""
            print(f"{kind} {name}{extra} attrs={fmt(obj.attrs)}"[:900])

        f.visititems(visit)

        title("3. Georeferencing (the important part)")
        for name in ("where", "what", "dataset1/where", "dataset1/what", "dataset1/data1/what"):
            if name in f:
                print(name, "->", fmt(f[name].attrs))
            else:
                print(name, "-> not present")
        if "how" in f:
            print("how (general attrs) ->", {k: v for k, v in fmt(f["how"].attrs).items() if k not in ("nodes", "missing_nodes")})

        title("4. The numbers")
        d = f["dataset1/data1/data"][...]
        w = f["dataset1/data1/what"].attrs
        gain, offset = float(w["gain"]), float(w["offset"])
        nodata, undetect = float(w["nodata"]), float(w["undetect"])
        print("shape:", d.shape, "dtype:", d.dtype, "gain:", gain, "offset:", offset)
        is_nodata = d == nodata
        is_und = d == undetect
        valid = ~(is_nodata | is_und)
        total = d.size
        print(f"nodata cells (outside radar coverage): {is_nodata.sum()} ({100 * is_nodata.mean():.1f}%)")
        print(f"undetect cells (no rain): {is_und.sum()} ({100 * is_und.mean():.1f}%)")
        print(f"cells with a measured value: {valid.sum()} ({100 * valid.mean():.1f}%)")
        vals = d[valid].astype(np.float64) * gain + offset      # mm accumulated over 5 min
        if vals.size:
            print("5-min accumulation in mm: min", vals.min(), "max", vals.max())
            for p in (50, 90, 99, 99.9):
                print(f"  percentile {p}: {np.percentile(vals, p):.3f} mm")
            rate = vals * 12.0                                    # mm per hour
            print("cells by rain rate (mm/h):")
            for thr in (0.1, 1, 5, 10, 20, 40, 80):
                print(f"  >= {thr:>5} mm/h : {(rate >= thr).sum()}")
        # where is the data? bounding box of the measured cells (row/col)
        rows = np.where(valid.any(axis=1))[0]
        cols = np.where(valid.any(axis=0))[0]
        if rows.size and cols.size:
            print("measured cells span rows", rows.min(), "-", rows.max(), " cols", cols.min(), "-", cols.max())
        print("corner values (top-left, top-right, bottom-left, bottom-right):",
              d[0, 0], d[0, -1], d[-1, 0], d[-1, -1])

        q = "dataset1/data1/quality1/data"
        if q in f:
            qd = f[q][...]
            qw = f["dataset1/data1/quality1/what"].attrs
            print("quality layer: min", qd.min(), "max", qd.max(), "gain", float(qw["gain"]))

    print("\nDONE")


try:
    main()
except Exception:
    traceback.print_exc()
