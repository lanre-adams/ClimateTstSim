"""Climate-driven free-living stage dynamics for a Haemonchus-like nematode.

Structure (not parameter values) follows the logic of process-based models such
as Q0 and GLOWORM-FL: egg-to-L3 development depends on temperature and is
gated by moisture; larvae must migrate from faeces onto herbage, which needs
moisture; larvae die faster when hot and dry. The functional forms and numbers
below are ILLUSTRATIVE PLACEHOLDERS for a demonstrator. A real study would
replace them with published, experimentally derived parameters.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---- illustrative parameters (documented in docs/PARAMETERS.md) -------------
T_DEV_MIN = 10.0      # deg C, no development below this
DEV_SLOPE = 0.01      # per day per degree above T_DEV_MIN (25 C -> 0.15/day)
T_DEV_DECLINE = 35.0  # development success declines above this
T_DEV_MAX = 40.0      # and reaches zero here
MOIST_PET_RATIO = 1.0  # 7-day rain/PET ratio at which moisture stops limiting


def extraterrestrial_radiation(doy: np.ndarray, lat_deg: float) -> np.ndarray:
    """FAO-56 eq. 21: extraterrestrial radiation Ra in MJ m-2 day-1."""
    phi = np.deg2rad(lat_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    delta = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1, 1))
    gsc = 0.0820
    return (24 * 60 / np.pi) * gsc * dr * (
        ws * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(ws)
    )


def hargreaves_pet(weather: pd.DataFrame, lat_deg: float) -> np.ndarray:
    """Hargreaves reference evapotranspiration (mm/day), FAO-56 eq. 52."""
    doy = pd.to_datetime(weather["date"]).dt.dayofyear.values
    ra = extraterrestrial_radiation(doy, lat_deg)
    dtr = np.clip(weather["tmax"].values - weather["tmin"].values, 0.1, None)
    pet = 0.0023 * 0.408 * ra * (weather["tmean"].values + 17.8) * np.sqrt(dtr)
    return np.clip(pet, 0.05, None)


def moisture_factor(weather: pd.DataFrame, lat_deg: float, window: int = 7) -> np.ndarray:
    """0..1 moisture availability from the trailing-window rain/PET ratio."""
    rain = weather["rain"].rolling(window, min_periods=1).sum().values
    pet = pd.Series(hargreaves_pet(weather, lat_deg)).rolling(window, min_periods=1).sum().values
    return np.clip((rain / pet) / MOIST_PET_RATIO, 0.0, 1.0)


def development_rate(t: np.ndarray) -> np.ndarray:
    """Daily egg->L3 development rate (fraction of the cohort completing per day)."""
    rate = np.clip(DEV_SLOPE * (t - T_DEV_MIN), 0.0, None)
    decline = np.clip(1 - (t - T_DEV_DECLINE) / (T_DEV_MAX - T_DEV_DECLINE), 0.0, 1.0)
    return rate * decline


def egg_mortality(t: np.ndarray, m: np.ndarray) -> np.ndarray:
    return 0.08 + 0.0015 * (t - 22.0) ** 2 + 0.10 * (1 - m)


def faecal_l3_mortality(t: np.ndarray, m: np.ndarray) -> np.ndarray:
    return 0.02 + 0.0008 * np.clip(t - 15.0, 0, None) ** 1.5 + 0.02 * (1 - m)


def migration_rate(m: np.ndarray) -> np.ndarray:
    return 0.01 + 0.25 * m


def herbage_l3_mortality(t: np.ndarray, m: np.ndarray) -> np.ndarray:
    return 0.03 + 0.0012 * np.clip(t - 15.0, 0, None) ** 1.5 + 0.04 * (1 - m)


def daily_rates(weather: pd.DataFrame, lat_deg: float) -> pd.DataFrame:
    t = weather["tmean"].values
    m = moisture_factor(weather, lat_deg)
    return pd.DataFrame(
        {
            "date": weather["date"].values,
            "moisture": m,
            "dev": development_rate(t) * m,
            "mu_e": egg_mortality(t, m),
            "mig": migration_rate(m),
            "mu_f": faecal_l3_mortality(t, m),
            "mu_h": herbage_l3_mortality(t, m),
        }
    )


def transmission_potential(weather: pd.DataFrame, lat_deg: float, smooth_days: int = 14) -> pd.Series:
    """Q0-style transmission potential index for each day.

    Expected herbage-L3 days produced per egg deposited, if that day's
    conditions persisted: P(develop) x P(migrate) x mean herbage-L3 lifetime.
    Scaled so that 25 C with no moisture limitation = 1.0, then smoothed.
    This is an index of climatic suitability, not a prediction of infection.
    """
    r = daily_rates(weather, lat_deg)
    raw = (r.dev / (r.dev + r.mu_e)) * (r.mig / (r.mig + r.mu_f)) / r.mu_h
    t_ref, m_ref = np.array([25.0]), np.array([1.0])
    dev_ref = development_rate(t_ref) * m_ref
    ref = (dev_ref / (dev_ref + egg_mortality(t_ref, m_ref))) * (
        migration_rate(m_ref) / (migration_rate(m_ref) + faecal_l3_mortality(t_ref, m_ref))
    ) / herbage_l3_mortality(t_ref, m_ref)
    idx = pd.Series(np.asarray(raw) / float(ref[0]), index=pd.to_datetime(weather["date"]))
    return idx.rolling(smooth_days, min_periods=1).mean().rename("tpi")


def risk_band(tpi: np.ndarray, low: float = 0.15, high: float = 0.45) -> np.ndarray:
    """Map the index to an illustrative 3-level band: 0 low, 1 moderate, 2 high."""
    tpi = np.asarray(tpi)
    return np.where(tpi >= high, 2, np.where(tpi >= low, 1, 0))
