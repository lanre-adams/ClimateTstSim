"""Experiments: policy comparison, indicator value-for-effort, and shift tests.

Every number produced here comes from the synthetic simulator.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .climate import synthetic_weather
from .flock import simulate
from .policies import (
    MINUTES, ClimateGatedFPC, FamachaTST, FPCTST, ModelTriage, NoTreatment,
    WholeFlockInterval,
)

FEC_MINUTES_EQUIV = 20.0  # illustrative: sample collection + transport + lab time

FEATURE_SETS = {
    "Nasal discharge only": ["nasal"],
    "BCS only": ["bcs"],
    "FAMACHA only": ["famacha"],
    "FAMACHA + BCS": ["famacha", "bcs"],
    "Five-point-style (5 checks)": ["famacha", "bcs", "bottle_jaw", "dag", "nasal"],
    "FAMACHA + BCS + climate index": ["famacha", "bcs", "tpi"],
    "Five-point + climate + history": ["famacha", "bcs", "bottle_jaw", "dag", "nasal", "tpi", "kid", "days_since_treat"],
    "FEC only (lab)": ["log_fec"],
    "FAMACHA + FEC (lab)": ["famacha", "log_fec"],
}
TRIAGE_FEATURES = FEATURE_SETS["Five-point + climate + history"]


def feature_cost(cols) -> float:
    cost = 0.0
    for c in cols:
        if c in MINUTES:
            cost += MINUTES[c]
        elif c == "log_fec":
            cost += FEC_MINUTES_EQUIV
    return cost


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["log_fec"] = np.log10(df["fec"] + 50)
    return df


def make_model():
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000))


def collect_checks(site: str, seeds, years: int = 4, warming_c: float = 0.0, weather_seed_offset: int = 100):
    """Run the fortnightly five-point-style policy and return the check log
    (indicators + ground truth), pooled over seeds. Farms are made unique per seed."""
    logs = []
    for s in seeds:
        w = synthetic_weather(site, years=years, seed=s + weather_seed_offset, warming_c=warming_c)
        res = simulate(w, FPCTST(), seed=s)
        c = res.checks
        c["farm"] = c["farm"] + 1000 * s
        c["seed"] = s
        logs.append(c)
    return _prep(pd.concat(logs, ignore_index=True))


def sens_at_spec(y, p, spec=0.8):
    neg = np.sort(p[y == 0])
    if len(neg) == 0:
        return np.nan
    thr = neg[int(np.ceil(spec * len(neg))) - 1]
    return float(np.mean(p[y == 1] > thr)) if (y == 1).any() else np.nan


def indicator_study(train: pd.DataFrame, n_splits: int = 5) -> pd.DataFrame:
    """Farm-grouped cross-validation of each indicator set."""
    rows = []
    y = train["need"].values
    groups = train["farm"].values
    for name, cols in FEATURE_SETS.items():
        X = train[cols].values
        oof = np.zeros(len(y))
        for tr, te in GroupKFold(n_splits).split(X, y, groups):
            m = make_model().fit(X[tr], y[tr])
            oof[te] = m.predict_proba(X[te])[:, 1]
        rows.append(
            {
                "indicator_set": name,
                "minutes_per_check": feature_cost(cols),
                "auroc": roc_auc_score(y, oof),
                "sens_at_spec80": sens_at_spec(y, oof, 0.8),
                "brier": brier_score_loss(y, oof),
                "n_checks": len(y),
                "prevalence": float(y.mean()),
            }
        )
    out = pd.DataFrame(rows).sort_values("minutes_per_check").reset_index(drop=True)
    # Pareto frontier: no cheaper set achieves equal or higher AUROC
    best = -np.inf
    frontier = []
    for a in out["auroc"]:
        frontier.append(a > best + 1e-9)
        best = max(best, a)
    out["pareto"] = frontier
    return out


def fit_triage(train: pd.DataFrame, target_sens: float = 0.9):
    """Fit the triage model and pick the threshold reaching target sensitivity
    on out-of-fold predictions (farm-grouped)."""
    y = train["need"].values
    X = train[TRIAGE_FEATURES].values
    oof = np.zeros(len(y))
    for tr, te in GroupKFold(5).split(X, y, train["farm"].values):
        oof[te] = make_model().fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    pos = np.sort(oof[y == 1])
    thr = float(pos[int(np.floor((1 - target_sens) * len(pos)))]) if len(pos) else 0.5
    model = make_model().fit(X, y)
    coefs = dict(zip(TRIAGE_FEATURES, model[-1].coef_[0].round(3)))
    return model, thr, coefs


def evaluate_model(model, df: pd.DataFrame, threshold: float, label: str) -> dict:
    y = df["need"].values
    p = model.predict_proba(df[TRIAGE_FEATURES].values)[:, 1]
    pred = p >= threshold
    return {
        "test_set": label,
        "n_checks": int(len(y)),
        "prevalence": float(y.mean()),
        "auroc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else np.nan,
        "sensitivity": float(pred[y == 1].mean()) if y.sum() else np.nan,
        "specificity": float((~pred[y == 0]).mean()) if (y == 0).any() else np.nan,
        "brier": float(brier_score_loss(y, p)),
        "mean_predicted": float(p.mean()),
        "observed_rate": float(y.mean()),
    }


def policy_comparison(site: str, seeds, triage=None, years: int = 4, warming_c: float = 0.0):
    rows, daily = [], []
    for s in seeds:
        w = synthetic_weather(site, years=years, seed=s + 500, warming_c=warming_c)
        pols = [NoTreatment(), WholeFlockInterval(), FamachaTST(), FPCTST(), ClimateGatedFPC()]
        if triage is not None:
            model, thr = triage
            pols.append(ModelTriage(model, TRIAGE_FEATURES, thr))
        for pol in pols:
            res = simulate(w, pol, seed=s + 900)
            rows.append(res.summary)
            d = res.daily[["date", "worm_resistant_frac", "mean_pcv", "frac_need", "tpi"]].copy()
            d["policy"], d["seed"] = pol.name, s
            daily.append(d)
    return pd.DataFrame(rows), pd.concat(daily, ignore_index=True)


def summarise_policies(df: pd.DataFrame) -> pd.DataFrame:
    metrics = [
        "deaths_per_100_goat_years", "treatments_per_goat_year", "check_minutes_per_goat_year",
        "pct_goat_days_anaemic", "final_resistant_frac",
    ]
    g = df.groupby("policy", sort=False)[metrics]
    mean = g.mean().add_suffix("_mean")
    lo = g.quantile(0.05).add_suffix("_p05")
    hi = g.quantile(0.95).add_suffix("_p95")
    return pd.concat([mean, lo, hi], axis=1).reset_index()
