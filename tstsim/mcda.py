"""Multi-criteria trade-off analysis of simulated policy outcomes.

The simulator scores each management policy on several outcomes that pull in
different directions. This module asks the questions a sustainability
assessment has to answer before it can recommend anything:

1. Normalisation: how are outcomes in different units put on one scale?
2. Dominance: which policies are genuine trade-offs (the Pareto set)?
3. Weighting: how robust is the "best" policy to how much each outcome matters?
4. Explanation: why is policy A ranked above policy B, and what would reverse it?

Every outcome here is minimised. Inputs are policy means from the synthetic
simulator, so outputs illustrate the method, not real farm advice.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Objective:
    key: str        # column in the policy summary (mean values)
    label: str      # plain-language name
    unit: str
    theme: str      # sustainability dimension it stands for
    target: float   # illustrative target for target-based scaling
    digits: int = 2


OBJECTIVES = [
    Objective("deaths_per_100_goat_years", "Deaths", "per 100 goat-years", "Animal health", 2.0, 1),
    Objective("pct_goat_days_anaemic", "Anaemic goat-days", "% of goat-days", "Welfare", 1.5, 1),
    Objective("treatments_per_goat_year", "Drug doses", "per goat-year", "Drug use", 1.0, 2),
    Objective("labour_minutes_per_goat_year", "Labour", "minutes per goat-year", "Cost to the farmer", 30.0, 0),
    Objective("final_resistant_frac", "Drug resistance", "share of worms resistant at the end", "Long-term sustainability", 0.5, 2),
]
OBJ_KEYS = [o.key for o in OBJECTIVES]
DEFAULT_REFERENCE = "Whole flock every 60 days"

METHODS = {
    "minmax": "Min–max: worst option = 0, best = 1",
    "reference": "Reference: compared with current practice (0.5 = same as the reference policy)",
    "target": "Target: share of an illustrative target met (1 = target reached)",
    "rank": "Rank: order only, ignoring the size of differences",
}


def _matrix(df: pd.DataFrame, objectives=OBJECTIVES) -> np.ndarray:
    return df[[o.key for o in objectives]].astype(float).values


def goodness(df: pd.DataFrame, method: str = "minmax", reference: str | None = None,
             targets: dict | None = None, objectives=OBJECTIVES) -> np.ndarray:
    """Return an (options x objectives) array of 0..1 scores where 1 is best."""
    X = _matrix(df, objectives)
    n, m = X.shape
    G = np.zeros_like(X)
    if method == "minmax":
        lo, hi = X.min(0), X.max(0)
        span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
        G = np.where(hi - lo > 1e-12, (hi - X) / span, 1.0)
    elif method == "reference":
        ref_name = reference or DEFAULT_REFERENCE
        names = list(df["policy"])
        if ref_name not in names:
            raise ValueError(f"Reference policy '{ref_name}' is not among the options.")
        r = X[names.index(ref_name)]
        r = np.where(r > 1e-12, r, np.maximum(X.max(0), 1e-12) / 2)  # avoid dividing by a zero reference
        G = 1.0 / (1.0 + X / r)
    elif method == "target":
        t = np.array([(targets or {}).get(o.key, o.target) for o in objectives], float)
        G = np.where(X <= t, 1.0, t / np.maximum(X, 1e-12))
    elif method == "rank":
        ranks = pd.DataFrame(X).rank(method="average").values  # 1 = smallest = best
        G = (n - ranks) / max(n - 1, 1)
    else:
        raise ValueError(f"Unknown normalisation '{method}'.")
    return np.clip(G, 0.0, 1.0)


def norm_weights(w) -> np.ndarray:
    w = np.clip(np.asarray(w, float), 0, None)
    s = w.sum()
    if s <= 0:
        raise ValueError("At least one weight must be above zero.")
    return w / s


def scores(G: np.ndarray, w) -> np.ndarray:
    return G @ norm_weights(w)


def dominates(a: np.ndarray, b: np.ndarray, tol: float = 1e-9) -> bool:
    """True if a is no worse than b on every (minimised) objective and better on one."""
    return bool(np.all(a <= b + tol) and np.any(a < b - tol))


def pareto_mask(df: pd.DataFrame, objectives=OBJECTIVES) -> np.ndarray:
    X = _matrix(df, objectives)
    return np.array([not any(dominates(X[j], X[i]) for j in range(len(X)) if j != i) for i in range(len(X))])


def dominated_by(df: pd.DataFrame, objectives=OBJECTIVES) -> dict:
    X = _matrix(df, objectives)
    names = list(df["policy"])
    return {names[i]: [names[j] for j in range(len(X)) if j != i and dominates(X[j], X[i])] for i in range(len(X))}


def acceptability(G: np.ndarray, samples: int = 4000, seed: int = 42) -> np.ndarray:
    """Share of random weightings (uniform over the simplex) under which each option ranks first."""
    rng = np.random.default_rng(seed)
    W = rng.dirichlet(np.ones(G.shape[1]), samples)
    S = W @ G.T
    best = S.argmax(1)
    return np.bincount(best, minlength=G.shape[0]) / samples


def explain(G: np.ndarray, w, a: int, b: int, objectives=OBJECTIVES) -> dict:
    """Argument-style comparison of option a against option b under weights w.

    For each objective that favours the lower-ranked option, report the weight
    share it would need for the ranking to flip, moving weight onto it from the
    other objectives in proportion.
    """
    wn = norm_weights(w)
    contrib = wn * (G[a] - G[b])
    margin = float(contrib.sum())
    if margin < 0:
        a, b, contrib, margin = b, a, -contrib, -margin
    pros, cons = [], []
    for k, o in enumerate(objectives):
        d = float(G[a, k] - G[b, k])
        item = {"objective": o.label, "theme": o.theme, "contribution": float(contrib[k]), "gap": d}
        if d > 1e-9:
            pros.append(item)
        elif d < -1e-9:
            lam = margin / (margin + (-d))
            item["flip_weight_share"] = float(wn[k] * (1 - lam) + lam)
            cons.append(item)
    pros.sort(key=lambda x: -x["contribution"])
    cons.sort(key=lambda x: x["contribution"])
    return {"winner": a, "loser": b, "margin": margin, "for": pros, "against": cons}


def explanation_text(exp: dict, names: list) -> str:
    a, b = names[exp["winner"]], names[exp["loser"]]
    if exp["margin"] < 1e-9:
        return f"{a} and {b} score the same under these weights."
    parts = [f"{a} is ranked above {b} (score margin {exp['margin']:.3f})."]
    if exp["for"]:
        parts.append("Main reasons: " + ", ".join(f"better on {x['objective'].lower()}" for x in exp["for"][:3]) + ".")
    if exp["against"]:
        c = exp["against"][0]
        parts.append(f"Strongest objection: {b} is better on {c['objective'].lower()}.")
        flips = [f"{x['objective'].lower()} got at least {100 * x['flip_weight_share']:.0f}% of the total weight"
                 for x in exp["against"]]
        parts.append(f"The ranking would reverse if {' or '.join(flips)}.")
    else:
        parts.append(f"{b} is not better on any outcome, so no weighting reverses this.")
    return " ".join(parts)


def apply_limits(df: pd.DataFrame, limits: dict | None) -> tuple[pd.DataFrame, list]:
    """Drop options that break a hard limit (an epsilon-constraint), e.g. deaths <= current practice.

    limits maps an objective key to a maximum value. Returns (kept rows, [(policy, reason), ...]).
    """
    if not limits:
        return df, []
    keep = np.ones(len(df), bool)
    excluded = []
    by_key = {o.key: o for o in OBJECTIVES}
    for i, r in df.iterrows():
        for k, lim in limits.items():
            if lim is None or k not in by_key:
                continue
            if float(r[k]) > float(lim) + 1e-9:
                keep[i] = False
                o = by_key[k]
                excluded.append({"policy": r["policy"],
                                 "reason": f"{o.label} {float(r[k]):.{o.digits}f} is above the limit of {float(lim):.{o.digits}f}"})
                break
    return df[keep].reset_index(drop=True), excluded


def analyse(df: pd.DataFrame, method: str = "minmax", weights=None, reference: str | None = None,
            targets: dict | None = None, samples: int = 4000, seed: int = 42, compare: tuple | None = None,
            limits: dict | None = None) -> dict:
    """Full trade-off analysis for a table of policy means (one row per policy).

    limits (optional) removes options that break a hard limit before ranking, but they
    are still shown in the Pareto check so the cost of the limit stays visible.
    """
    df = df.reset_index(drop=True)
    all_names = list(df["policy"])
    all_pareto = pareto_mask(df).tolist()
    df, excluded = apply_limits(df, limits)
    names = list(df["policy"])
    if len(names) < 2:
        raise ValueError("Fewer than two policies meet the limits; relax a limit or add policies.")
    w = norm_weights(weights if weights is not None else np.ones(len(OBJECTIVES)))
    ref = reference if reference in names else (DEFAULT_REFERENCE if DEFAULT_REFERENCE in names else names[0])
    G = goodness(df, method, ref, targets)
    s = scores(G, w)
    order = list(np.argsort(-s, kind="stable"))
    pm = pareto_mask(df)
    by_method = {}
    for mth in METHODS:
        Gm = goodness(df, mth, ref, targets)
        sm = scores(Gm, w)
        by_method[mth] = {
            "first": names[int(np.argmax(sm))],
            "scores": sm.tolist(),
            "acceptability": acceptability(Gm, samples, seed).tolist(),
        }
    if compare is None:
        compare = (order[0], order[1])
    exp = explain(G, w, int(compare[0]), int(compare[1]))
    return {
        "method": method,
        "reference": ref,
        "weights": dict(zip(OBJ_KEYS, w.tolist())),
        "objectives": [o.__dict__ for o in OBJECTIVES],
        "policies": names,
        "values": _matrix(df).tolist(),
        "goodness": G.tolist(),
        "scores": s.tolist(),
        "ranking": [names[i] for i in order],
        "pareto": pm.tolist(),
        "dominated_by": dominated_by(df),
        "limits": {k: v for k, v in (limits or {}).items() if v is not None},
        "excluded": excluded,
        "all_policies": all_names,
        "all_pareto": all_pareto,
        "by_method": by_method,
        "explanation": {**exp, "winner_name": names[exp["winner"]], "loser_name": names[exp["loser"]],
                        "text": explanation_text(exp, names)},
    }


def summary_to_frame(summary: list) -> pd.DataFrame:
    """Turn the web app's summary rows ({metric: {mean,...}}) into a frame of means."""
    rows = []
    for r in summary:
        row = {"policy": r["policy"]}
        for k in OBJ_KEYS:
            v = r.get(k)
            row[k] = v["mean"] if isinstance(v, dict) else v
        rows.append(row)
    return pd.DataFrame(rows)
