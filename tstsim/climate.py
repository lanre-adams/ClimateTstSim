"""Daily weather: a synthetic generator and a loader for NASA POWER CSV files.

The synthetic site profiles are *shapes* loosely resembling the seasonality of
three places (a single rainy season in central Malawi, a short summer rainy
season in northern Nigeria, a long wet season in south-eastern Nigeria). The
monthly numbers are rounded approximations typed in for demonstration; they
are not station climatologies. Replace them with real data via
`load_nasa_power_csv` (see scripts/fetch_nasa_power.py) before drawing any
conclusion about a real place.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SiteProfile:
    name: str
    label: str
    lat: float
    lon: float
    tmean: tuple  # 12 monthly mean temperatures, deg C (approximate shape)
    rain_mm: tuple  # 12 monthly totals, mm (approximate shape)
    wet_days: tuple  # 12 monthly expected wet-day counts


SITES = {
    "lilongwe_like": SiteProfile(
        name="lilongwe_like",
        label="Central Malawi-like (one rainy season, Nov-Apr)",
        lat=-13.98, lon=33.78,
        tmean=(23, 23, 22, 21, 18, 16, 16, 18, 21, 24, 24, 23),
        rain_mm=(210, 190, 120, 40, 5, 1, 1, 1, 3, 15, 90, 200),
        wet_days=(18, 16, 12, 5, 1, 0.3, 0.3, 0.3, 0.5, 2, 9, 17),
    ),
    "kano_like": SiteProfile(
        name="kano_like",
        label="Northern Nigeria-like (short rainy season, Jun-Sep)",
        lat=12.00, lon=8.52,
        tmean=(22, 25, 29, 31, 31, 29, 27, 26, 27, 27, 25, 22),
        rain_mm=(0, 0, 2, 10, 50, 110, 200, 260, 110, 10, 0, 0),
        wet_days=(0.1, 0.1, 0.3, 1.5, 5, 9, 13, 16, 9, 1.5, 0.1, 0.1),
    ),
    "uyo_like": SiteProfile(
        name="uyo_like",
        label="South-east Nigeria-like (long wet season, Mar-Nov)",
        lat=5.04, lon=7.92,
        tmean=(27, 28, 28, 27, 27, 26, 25, 25, 25, 26, 27, 27),
        rain_mm=(30, 60, 150, 200, 280, 330, 360, 300, 340, 260, 110, 30),
        wet_days=(2, 4, 9, 12, 16, 19, 21, 19, 20, 16, 7, 2),
    ),
}


def synthetic_weather(
    site: str,
    years: int = 4,
    start: str = "2021-01-01",
    seed: int = 0,
    warming_c: float = 0.0,
    rain_scale: float = 1.0,
) -> pd.DataFrame:
    """Generate daily synthetic weather for a site profile.

    Temperature: smooth monthly cycle (interpolated) + AR(1) anomalies.
    Rainfall: daily occurrence with the profile's monthly wet-day probability,
    gamma-distributed amounts rescaled so the expected monthly total matches
    the profile (times ``rain_scale``). ``warming_c`` adds a uniform offset,
    used for simple "what if warmer" scenarios.
    """
    prof = SITES[site]
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=int(round(365.25 * years)), freq="D")
    month = dates.month.values - 1
    doy = dates.dayofyear.values

    # Smooth temperature climatology by periodic interpolation of monthly values
    mid = (np.arange(12) + 0.5) * 365.25 / 12
    xp = np.concatenate([mid - 365.25, mid, mid + 365.25])
    fp = np.tile(np.asarray(prof.tmean, float), 3)
    clim_t = np.interp(doy, xp, fp)

    anom = np.zeros(len(dates))
    eps = rng.normal(0, 1.1, len(dates))
    for i in range(1, len(dates)):
        anom[i] = 0.7 * anom[i - 1] + eps[i]
    tmean = clim_t + anom + warming_c
    dtr = 10.0 - 4.0 * np.clip(np.asarray(prof.rain_mm)[month] / 300, 0, 1)
    tmax = tmean + dtr / 2
    tmin = tmean - dtr / 2

    days_in_month = dates.days_in_month.values
    p_wet = np.clip(np.asarray(prof.wet_days, float)[month] / days_in_month, 0, 0.95)
    wet = rng.random(len(dates)) < p_wet
    shape = 0.8
    mean_amount = np.where(
        np.asarray(prof.wet_days)[month] > 0,
        np.asarray(prof.rain_mm, float)[month] / np.maximum(np.asarray(prof.wet_days, float)[month], 1e-6),
        0.0,
    )
    amount = rng.gamma(shape, 1.0, len(dates)) * mean_amount / shape
    rain = np.where(wet, amount, 0.0) * rain_scale

    return pd.DataFrame(
        {
            "date": dates,
            "tmean": tmean.round(2),
            "tmax": tmax.round(2),
            "tmin": tmin.round(2),
            "rain": rain.round(2),
            "site": site,
            "synthetic": True,
        }
    )


def load_nasa_power_csv(path: str, site: str = "custom") -> pd.DataFrame:
    """Load a NASA POWER daily point CSV (parameters T2M, T2M_MAX, T2M_MIN,
    PRECTOTCORR) into the same schema as `synthetic_weather`.

    Handles the -BEGIN HEADER- / -END HEADER- block and the -999 fill value.
    """
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = 0
    if lines and lines[0].startswith("-BEGIN HEADER-"):
        for i, line in enumerate(lines):
            if line.startswith("-END HEADER-"):
                start = i + 1
                break
    from io import StringIO

    df = pd.read_csv(StringIO("\n".join(lines[start:])))
    df = df.replace(-999, np.nan).replace(-999.0, np.nan)
    if {"YEAR", "MO", "DY"}.issubset(df.columns):
        dates = pd.to_datetime(dict(year=df.YEAR, month=df.MO, day=df.DY))
    elif {"YEAR", "DOY"}.issubset(df.columns):
        dates = pd.to_datetime(df.YEAR.astype(str), format="%Y") + pd.to_timedelta(df.DOY - 1, unit="D")
    else:
        raise ValueError("Unrecognised NASA POWER CSV layout: need YEAR,MO,DY or YEAR,DOY")
    out = pd.DataFrame(
        {
            "date": dates,
            "tmean": df["T2M"].astype(float),
            "tmax": df.get("T2M_MAX", df["T2M"] + 5).astype(float),
            "tmin": df.get("T2M_MIN", df["T2M"] - 5).astype(float),
            "rain": df["PRECTOTCORR"].astype(float).clip(lower=0),
            "site": site,
            "synthetic": False,
        }
    )
    out[["tmean", "tmax", "tmin"]] = out[["tmean", "tmax", "tmin"]].interpolate(limit_direction="both")
    out["rain"] = out["rain"].fillna(0.0)
    return out.reset_index(drop=True)
