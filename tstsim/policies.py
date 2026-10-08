"""Management policies compared in the demonstrator.

Check times are illustrative minutes per goat (docs/PARAMETERS.md).
The FPC rule is a simplified stand-in for a Five Point Check decision chart;
it is not the published chart.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .flock import Policy

MINUTES = {"famacha": 1.0, "bcs": 0.5, "bottle_jaw": 0.3, "dag": 0.3, "nasal": 0.2}
FPC_MINUTES = sum(MINUTES.values())


class NoTreatment(Policy):
    name = "No treatment"


class WholeFlockInterval(Policy):
    name = "Whole flock every 60 days"

    def __init__(self, interval: int = 60):
        self.interval = interval

    def whole_flock_today(self, day: int) -> bool:
        return day > 0 and day % self.interval == 0


class _Fortnightly(Policy):
    interval = 14

    def is_check_day(self, day, farm_offset, band_today):
        return (day + farm_offset) % self.interval == 0


class FamachaTST(_Fortnightly):
    name = "FAMACHA only, fortnightly (treat 4-5)"
    check_minutes = MINUTES["famacha"]

    def treat(self, obs: pd.DataFrame) -> np.ndarray:
        return (obs.famacha >= 4).values


def fpc_rule(obs: pd.DataFrame) -> np.ndarray:
    return (
        (obs.famacha >= 4)
        | (obs.bottle_jaw == 1)
        | (obs.bcs <= 1.5)
        | ((obs.famacha == 3) & (obs.bcs <= 2.0))
    ).values


class FPCTST(_Fortnightly):
    name = "Five-point-style check, fortnightly"
    check_minutes = FPC_MINUTES

    def treat(self, obs):
        return fpc_rule(obs)


class ClimateGatedFPC(Policy):
    """Check fortnightly when the climate index is moderate/high, monthly when low."""

    name = "Five-point-style check, climate-gated"
    check_minutes = FPC_MINUTES

    def is_check_day(self, day, farm_offset, band_today):
        fortnight = (day + farm_offset) % 14 == 0
        monthly = (day + farm_offset) % 28 == 0
        return np.where(band_today >= 1, fortnight, monthly)

    def treat(self, obs):
        return fpc_rule(obs)


class ModelTriage(Policy):
    """Treat when a fitted model's predicted probability of need exceeds a threshold.

    Uses the same cheap indicators as the five-point-style check plus the climate
    index, checked on the climate-gated schedule.
    """

    name = "Model triage (cheap checks + climate), climate-gated"
    check_minutes = FPC_MINUTES

    def __init__(self, model, features, threshold: float):
        self.model, self.features, self.threshold = model, features, threshold

    def is_check_day(self, day, farm_offset, band_today):
        return ClimateGatedFPC.is_check_day(self, day, farm_offset, band_today)

    def treat(self, obs):
        p = self.model.predict_proba(obs[self.features].values)[:, 1]
        return p >= self.threshold
