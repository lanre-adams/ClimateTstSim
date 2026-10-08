"""Run every experiment and write outputs/ (figures, CSVs, results.json, report.md).

Usage:  python scripts/run_all.py [--quick]
--quick uses fewer seeds (for CI / smoke tests).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tstsim import SYNTHETIC_NOTICE, __version__  # noqa: E402
from tstsim.analysis import (  # noqa: E402
    collect_checks, evaluate_model, fit_triage, indicator_study,
    policy_comparison, summarise_policies,
)
from tstsim.climate import SITES, synthetic_weather  # noqa: E402
from tstsim.transmission import transmission_potential  # noqa: E402

# Reference categorical palette (light mode), fixed order
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "figure.dpi": 150, "savefig.bbox": "tight", "axes.titleweight": "bold", "axes.titlesize": 10,
})

MONTHS = list("JFMAMJJASOND")


def stamp(fig):
    fig.text(0.99, 0.005, "SYNTHETIC - illustrative parameters", ha="right", va="bottom", fontsize=7, color=INK2)


def fig_tpi(out: Path) -> pd.DataFrame:
    rows = []
    for site in SITES:
        for warm in (0.0, 2.0):
            vals = []
            for s in range(5):
                w = synthetic_weather(site, years=3, seed=40 + s, warming_c=warm)
                tpi = transmission_potential(w, SITES[site].lat)
                vals.append(tpi.groupby(tpi.index.month).mean())
            m = pd.concat(vals, axis=1).mean(axis=1)
            for month, v in m.items():
                rows.append({"site": site, "warming_c": warm, "month": month, "tpi": v})
    df = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    for i, site in enumerate(SITES):
        base = df[(df.site == site) & (df.warming_c == 0)]
        warm = df[(df.site == site) & (df.warming_c == 2)]
        ax.plot(base.month, base.tpi, color=C[i], lw=2, label=SITES[site].label)
        ax.plot(warm.month, warm.tpi, color=C[i], lw=1.2, ls="--")
    ax.set_xticks(range(1, 13), MONTHS)
    ax.set_ylabel("Transmission potential index\n(1 = 25 °C, moist)")
    ax.set_ylim(0, None)
    ax.set_title("Climatic suitability for free-living larvae by month")
    leg = ax.legend(frameon=False, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=1)
    ax.text(0.99, 0.97, "solid = baseline   dashed = +2 °C", transform=ax.transAxes, ha="right", va="top", fontsize=7.5, color=INK2)
    stamp(fig)
    fig.savefig(out / "fig1_transmission_index_by_site.png")
    plt.close(fig)
    return df


def fig_indicators(ind: pd.DataFrame, out: Path):
    d = ind.sort_values("minutes_per_check").reset_index(drop=True)
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.4), sharey=True, gridspec_kw={"width_ratios": [3, 2]})
    y = np.arange(len(d))[::-1]
    labels = [f"{r.indicator_set}  ({r.minutes_per_check:g} min)" for r in d.itertuples()]
    axes[0].hlines(y, 0.5, d.auroc, color=GRID, lw=2)
    axes[0].scatter(d.auroc, y, s=40, color=C[0], edgecolor="white", linewidth=1.5, zorder=3)
    for yi, a in zip(y, d.auroc):
        axes[0].text(a + 0.008, yi, f"{a:.3f}", va="center", fontsize=7, color=INK)
    axes[0].set_xlim(0.5, 1.06)
    axes[0].set_yticks(y, labels, fontsize=7.5)
    axes[0].set_title("Discrimination (AUROC)", fontsize=8.5)
    axes[0].grid(axis="y", visible=False)
    axes[1].hlines(y, 0, d.sens_at_spec80, color=GRID, lw=2)
    axes[1].scatter(d.sens_at_spec80, y, s=40, color=C[0], edgecolor="white", linewidth=1.5, zorder=3)
    axes[1].set_xlim(0, 1.05)
    axes[1].set_title("Sensitivity at 80% specificity", fontsize=8.5)
    axes[1].grid(axis="y", visible=False)
    fig.suptitle("What each set of checks buys (sorted by effort per goat; lab FEC counted as 20 min)",
                 fontsize=8.5, color=INK2, y=1.02)
    stamp(fig)
    fig.savefig(out / "fig2_indicator_value_for_effort.png")
    plt.close(fig)


SHORT = {
    "No treatment": "No treatment",
    "Whole flock every 60 days": "Whole flock / 60 d",
    "FAMACHA only, fortnightly (treat 4-5)": "FAMACHA only, 14 d",
    "Five-point-style check, fortnightly": "5-point, 14 d",
    "Five-point-style check, climate-gated": "5-point, climate-gated",
    "Model triage (cheap checks + climate), climate-gated": "Model triage, climate-gated",
}


def fig_policies(summ: pd.DataFrame, out: Path, site_label: str, fname: str):
    panels = [
        ("deaths_per_100_goat_years", "Deaths /100 goat-yr"),
        ("treatments_per_goat_year", "Doses /goat-yr"),
        ("check_minutes_per_goat_year", "Check minutes /goat-yr"),
        ("final_resistant_frac", "Resistant share (end)"),
    ]
    labels = [SHORT.get(p, p) for p in summ.policy]
    fig, axes = plt.subplots(1, 4, figsize=(8.6, 3.0), sharey=True)
    ypos = np.arange(len(labels))[::-1]
    for ax, (m, title) in zip(axes, panels):
        mean = summ[f"{m}_mean"].values
        lo, hi = summ[f"{m}_p05"].values, summ[f"{m}_p95"].values
        ax.barh(ypos, mean, color=C[0], height=0.55)
        ax.errorbar(mean, ypos, xerr=[mean - lo, hi - mean], fmt="none", ecolor=INK2, elinewidth=1, capsize=2)
        ax.set_title(title, fontsize=8.5)
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(ypos, labels, fontsize=7.5)
    fig.suptitle(f"Policy trade-offs ({site_label}); bars = mean, whiskers = 5th-95th pct over replicates",
                 fontsize=8.5, color=INK2, y=1.02)
    stamp(fig)
    fig.savefig(out / fname)
    plt.close(fig)


def fig_resistance(daily: pd.DataFrame, out: Path):
    d = daily.copy()
    d["t"] = d.groupby(["policy", "seed"]).cumcount() / 365.25
    m = d.groupby(["policy", "t"]).worm_resistant_frac.mean().reset_index()
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    for i, pol in enumerate(m.policy.unique()):
        s = m[m.policy == pol]
        ax.plot(s.t, s.worm_resistant_frac, color=C[i], lw=1.8, label=SHORT.get(pol, pol))
    ax.set_xlabel("Years after burn-in")
    ax.set_ylabel("Resistant share of adult worms")
    ax.set_ylim(0, 1.02)
    ax.set_title("How fast each policy selects for drug resistance")
    ax.legend(frameon=False, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3)
    stamp(fig)
    fig.savefig(out / "fig4_resistance_over_time.png")
    plt.close(fig)


def fig_shift(shift: pd.DataFrame, out: Path):
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 2.8), sharey=True)
    y = np.arange(len(shift))[::-1]
    axes[0].scatter(shift.sensitivity, y, color=C[0], s=36, label="Sensitivity", zorder=3)
    axes[0].scatter(shift.specificity, y, color=C[1], marker="D", s=30, label="Specificity", zorder=3)
    axes[0].set_xlim(0.6, 1.02)
    axes[0].set_yticks(y, shift.test_set, fontsize=7.5)
    axes[0].set_title("At the threshold chosen on training farms", fontsize=8.5)
    axes[0].legend(frameon=False, fontsize=7, loc="upper left")
    axes[0].grid(axis="y", visible=False)
    axes[1].scatter(shift.observed_rate, y, color=C[0], s=36, label="Observed rate", zorder=3)
    axes[1].scatter(shift.mean_predicted, y, color=C[1], marker="D", s=30, label="Mean predicted", zorder=3)
    for yi, a, b in zip(y, shift.observed_rate, shift.mean_predicted):
        axes[1].plot([a, b], [yi, yi], color=INK2, lw=0.8)
    axes[1].set_title("Calibration-in-the-large", fontsize=8.5)
    axes[1].legend(frameon=False, fontsize=7, loc="lower right")
    axes[1].grid(axis="y", visible=False)
    fig.suptitle("Triage model trained on Malawi-like farms, tested elsewhere", fontsize=9, y=1.03)
    stamp(fig)
    fig.savefig(out / "fig5_shift_test.png")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "outputs"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    n_train, n_test, n_pol = (2, 1, 2) if a.quick else (8, 4, 10)
    train_seeds = list(range(n_train))
    test_seeds = list(range(100, 100 + n_test))
    pol_seeds = list(range(200, 200 + n_pol))

    tpi_df = fig_tpi(out)
    tpi_df.to_csv(out / "transmission_index_monthly.csv", index=False)

    train = collect_checks("lilongwe_like", train_seeds)
    ind = indicator_study(train)
    ind.to_csv(out / "indicator_study.csv", index=False)
    fig_indicators(ind, out)

    model, thr, coefs = fit_triage(train)
    tests = {
        "Malawi-like, new farms (in-distribution)": collect_checks("lilongwe_like", test_seeds),
        "Malawi-like, +2 °C": collect_checks("lilongwe_like", test_seeds, warming_c=2.0),
        "SE Nigeria-like (long wet season)": collect_checks("uyo_like", test_seeds),
        "N Nigeria-like (short wet season)": collect_checks("kano_like", test_seeds),
    }
    shift = pd.DataFrame([evaluate_model(model, df, thr, k) for k, df in tests.items()])
    shift.to_csv(out / "shift_test.csv", index=False)
    fig_shift(shift, out)

    pol_raw, daily = policy_comparison("lilongwe_like", pol_seeds, triage=(model, thr))
    pol_raw.to_csv(out / "policy_runs_malawi_like.csv", index=False)
    summ = summarise_policies(pol_raw)
    summ.to_csv(out / "policy_summary_malawi_like.csv", index=False)
    fig_policies(summ, out, "Malawi-like site", "fig3_policy_tradeoffs.png")
    fig_resistance(daily, out)

    pol_raw2, _ = policy_comparison("uyo_like", pol_seeds, triage=(model, thr))
    summ2 = summarise_policies(pol_raw2)
    summ2.to_csv(out / "policy_summary_se_nigeria_like.csv", index=False)
    fig_policies(summ2, out, "SE Nigeria-like site", "fig3b_policy_tradeoffs_se_nigeria_like.png")

    results = {
        "notice": SYNTHETIC_NOTICE,
        "version": __version__,
        "quick": a.quick,
        "seeds": {"train": train_seeds, "test": test_seeds, "policy": pol_seeds},
        "runtime_s": round(time.time() - t0, 1),
        "training_checks": int(len(train)),
        "training_prevalence": float(train.need.mean()),
        "triage_threshold": thr,
        "triage_coefficients_standardised": coefs,
        "indicator_study": ind.to_dict(orient="records"),
        "shift_test": shift.to_dict(orient="records"),
        "policy_summary_malawi_like": summ.to_dict(orient="records"),
        "policy_summary_se_nigeria_like": summ2.to_dict(orient="records"),
    }
    (out / "results.json").write_text(json.dumps(results, indent=2, default=float))
    print(json.dumps({k: results[k] for k in ("runtime_s", "training_checks", "training_prevalence", "triage_threshold")}, indent=2))
    print(ind.round(3).to_string())
    print(shift.round(3).to_string())
    print(summ.round(2).to_string())
    print(summ2.round(2).to_string())


if __name__ == "__main__":
    main()
