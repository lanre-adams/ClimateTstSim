"""Monthly transmission-potential index from a real NASA POWER CSV.

    python scripts/real_weather_tpi.py data/real/lilongwe_power.csv --lat -13.98

Prints a month x year table and writes <csv>_tpi_monthly.csv.
The index uses the same illustrative parameters as the synthetic runs, so
results show climatic *pattern*, not validated risk.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tstsim.climate import load_nasa_power_csv  # noqa: E402
from tstsim.transmission import transmission_potential  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--lat", type=float, required=True)
    a = ap.parse_args()
    w = load_nasa_power_csv(a.csv, site=Path(a.csv).stem)
    tpi = transmission_potential(w, a.lat)
    table = tpi.groupby([tpi.index.year, tpi.index.month]).mean().unstack().round(2)
    print(table.to_string())
    out = Path(a.csv).with_name(Path(a.csv).stem + "_tpi_monthly.csv")
    table.to_csv(out)
    print("saved", out)


if __name__ == "__main__":
    main()
