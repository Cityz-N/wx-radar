"""
Discovery script - shows what Meteo-France radar files look like.
It prints NO secrets (the API key is read from the GitHub secret MF_API_KEY and is
only sent in the request header).
"""
import gzip
import io
import os
import sys
import tarfile
import traceback
import zipfile

import requests

BASE = "https://public-api.meteofrance.fr/public/DPRadar/v1"
KEY = os.environ.get("MF_API_KEY", "").strip()
if not KEY:
    print("ERROR: the secret MF_API_KEY is missing or empty.")
    sys.exit(1)
HEAD = {"apikey": KEY, "accept": "*/*"}


def title(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70, flush=True)


def call(path, params=None):
    url = BASE + path
    print("GET", url, "params=", params, flush=True)
    r = requests.get(url, headers=HEAD, params=params, timeout=180)
    print("status:", r.status_code)
    for h in ("content-type", "content-disposition", "content-length", "last-modified", "date"):
        if h in r.headers:
            print(h + ":", r.headers[h])
    return r


def show_text(r, limit=3500):
    try:
        txt = r.text
    except Exception:
        txt = repr(r.content[:limit])
    print(txt[:limit] + ("\n...[cut]" if len(txt) > limit else ""))


def analyse_bytes(data, label):
    print(f"\n--- analysing {label}: {len(data)} bytes")
    print("first 16 bytes (hex):", data[:16].hex(" "))
    print("first 16 bytes (text):", repr(data[:16]))
    if data[:2] == b"\x1f\x8b":
        print("-> gzip, decompressing")
        analyse_bytes(gzip.decompress(data), label + " (ungzipped)")
    elif data[:4] == b"PK\x03\x04":
        print("-> zip archive")
        z = zipfile.ZipFile(io.BytesIO(data))
        for n in z.namelist()[:40]:
            print("   ", n)
        for n in z.namelist()[:2]:
            analyse_bytes(z.read(n), label + ":" + n)
    elif data[257:262] == b"ustar":
        print("-> tar archive")
        t = tarfile.open(fileobj=io.BytesIO(data))
        names = t.getnames()
        for n in names[:40]:
            print("   ", n)
        for m in t.getmembers()[:2]:
            if m.isfile():
                analyse_bytes(t.extractfile(m).read(), label + ":" + m.name)
    elif data[:4] == b"BUFR":
        print("-> BUFR file")
        analyse_bufr(data)
    elif data[:4] == b"\x89HDF":
        print("-> HDF5 file")
        analyse_hdf5(data)
    elif data[:4] == b"GRIB":
        print("-> GRIB file")
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        print("-> TIFF / GeoTIFF file")
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        print("-> PNG image")
    else:
        print("-> unknown format; first 300 bytes:", repr(data[:300]))


def analyse_bufr(data):
    try:
        import numpy as np
        from eccodes import (codes_bufr_new_from_file, codes_set, codes_bufr_keys_iterator_new,
                             codes_bufr_keys_iterator_next, codes_bufr_keys_iterator_get_name,
                             codes_get_size, codes_get, codes_get_array, codes_release)
    except Exception:
        print("eccodes not available:")
        traceback.print_exc()
        return
    path = "sample.bufr"
    with open(path, "wb") as f:
        f.write(data)
    with open(path, "rb") as f:
        n = 0
        while True:
            h = codes_bufr_new_from_file(f)
            if h is None:
                break
            n += 1
            print(f"\nBUFR message {n}")
            try:
                codes_set(h, "unpack", 1)
                it = codes_bufr_keys_iterator_new(h)
                count = 0
                while codes_bufr_keys_iterator_next(it) and count < 150:
                    k = codes_bufr_keys_iterator_get_name(it)
                    count += 1
                    try:
                        size = codes_get_size(h, k)
                        if size > 1:
                            arr = np.array(codes_get_array(h, k))
                            try:
                                print(f"  {k}: array size={size} min={arr.min()} max={arr.max()} first={arr[:5].tolist()}")
                            except Exception:
                                print(f"  {k}: array size={size} first={arr[:5].tolist()}")
                        else:
                            print(f"  {k}: {codes_get(h, k)}")
                    except Exception as e:
                        print(f"  {k}: <unreadable: {e}>")
            except Exception:
                traceback.print_exc()
            codes_release(h)
            if n >= 3:
                print("(more messages not shown)")
                break
    if n == 0:
        print("eccodes could not find any BUFR message in this file")


def analyse_hdf5(data):
    try:
        import h5py
    except Exception:
        print("h5py not available:")
        traceback.print_exc()
        return
    with open("sample.h5", "wb") as f:
        f.write(data)
    with h5py.File("sample.h5", "r") as f:
        print("root attrs:", dict(f.attrs))
        lines = []

        def visit(name, obj):
            if isinstance(obj, h5py.Dataset):
                lines.append(f"DATASET {name} shape={obj.shape} dtype={obj.dtype} attrs={dict(obj.attrs)}")
            else:
                lines.append(f"GROUP   {name} attrs={dict(obj.attrs)}")

        f.visititems(visit)
        for ln in lines[:80]:
            print(ln[:600])
        if len(lines) > 80:
            print(f"... {len(lines) - 80} more entries")


def main():
    title("A. What the API offers")
    for p in ("/mosaiques", "/mosaiques/METROPOLE", "/mosaiques/METROPOLE/observations"):
        try:
            show_text(call(p))
        except Exception:
            traceback.print_exc()
        print()

    for obs in ("REFLECTIVITE", "LAME_D_EAU"):
        title(f"B. Latest mosaic for METROPOLE: {obs}")
        try:
            r = call(f"/mosaiques/METROPOLE/observations/{obs}/produit")
            if r.status_code == 200:
                analyse_bytes(r.content, obs)
            else:
                print("The request failed; reply from the server:")
                show_text(r)
        except Exception:
            traceback.print_exc()

    print("\nDONE")


main()
