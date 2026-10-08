"""Download daily weather for a point from NASA POWER (no API key needed).

Example:
    python scripts/fetch_nasa_power.py --lat -13.98 --lon 33.78 --start 2015 --end 2024 \
        --out data/real/lilongwe_power.csv

Then compute the transmission index from it:
    python scripts/real_weather_tpi.py data/real/lilongwe_power.csv --lat -13.98

NOTE: not run in the build environment (no outbound network there). Check
NASA POWER's terms and citation guidance before publishing results.
"""
from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

BASE = "https://power.larc.nasa.gov/api/temporal/daily/point"


def build_url(lat: float, lon: float, start: int, end: int) -> str:
    params = "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR"
    return (
        f"{BASE}?parameters={params}&community=AG&longitude={lon}&latitude={lat}"
        f"&start={start}0101&end={end}1231&format=CSV"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--end", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    url = build_url(a.lat, a.lon, a.start, a.end)
    print("GET", url)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r:
        Path(a.out).write_bytes(r.read())
    print("saved", a.out)


if __name__ == "__main__":
    main()
