"""Downloadable reports for the web interface.

Every analysis in the interface (simulation, trade-offs, indicator study,
triage transfer, climate) can be downloaded as:
  - HTML: a self-contained report with tables and charts, which opens in any
    browser and can be printed or saved as PDF from there;
  - CSV: the main results table;
  - JSON: the full result, for reuse in other tools.
"""
from __future__ import annotations

import base64
import html
import io
import json
import time

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .. import SYNTHETIC_NOTICE, __version__  # noqa: E402
from ..mcda import METHODS, OBJECTIVES, analyse, summary_to_frame  # noqa: E402

C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]
INK, INK2, GRID = "#1a1a1a", "#52514e", "#e4e3df"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.titleweight": "bold", "axes.titlesize": 10,
})

SIM_METRICS = [
    ("deaths_per_100_goat_years", "Deaths per 100 goat-years", 1),
    ("pct_goat_days_anaemic", "Anaemic goat-days, %", 1),
    ("treatments_per_goat_year", "Drug doses per goat-year", 2),
    ("check_minutes_per_goat_year", "Checking minutes per goat-year", 0),
    ("labour_minutes_per_goat_year", "Labour minutes per goat-year (checks + dosing)", 0),
    ("final_resistant_frac", "Resistant share of worms at the end", 2),
]


SHORT_TITLES = {
    "deaths_per_100_goat_years": "Deaths /100 goat-yr",
    "pct_goat_days_anaemic": "Anaemic goat-days, %",
    "treatments_per_goat_year": "Doses /goat-yr",
    "labour_minutes_per_goat_year": "Labour min /goat-yr",
    "final_resistant_frac": "Resistant share",
}


# ------------------------------------------------------------------ helpers
def _png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _f(v, d=2) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "–"
    return f"{float(v):.{d}f}"


def _table(headers, rows, num_from=1) -> str:
    th = "".join(f'<th class="{"num" if i >= num_from else ""}">{_e(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if i >= num_from else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>"
        for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>"


def _page(title: str, subtitle: str, sections: list[str], synthetic: bool = True) -> str:
    stamp = time.strftime("%d %B %Y, %H:%M")
    notice = (f'<div class="notice"><strong>Read before using.</strong> {_e(SYNTHETIC_NOTICE)}</div>'
              if synthetic else
              '<div class="notice"><strong>Read before using.</strong> Real weather or records were analysed with an '
              'illustrative transmission and flock model. Use the results to design studies, not to advise farmers.</div>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>
:root {{ --ink:#1a1a1a; --muted:#52514e; --rule:#d6d5d0; --tint:#f3f6f2; --accent:#2f6b4f; --warn:#fff4dc; }}
body {{ font-family: "Segoe UI", system-ui, -apple-system, Arial, sans-serif; color: var(--ink); background:#fff;
       max-width: 980px; margin: 0 auto; padding: 32px 20px 60px; line-height: 1.5; font-size: 15px; }}
h1 {{ font-family: Georgia, serif; font-size: 30px; margin: 0 0 4px; }}
h2 {{ font-family: Georgia, serif; font-size: 22px; margin: 32px 0 8px; border-bottom: 2px solid var(--accent); padding-bottom: 4px; }}
h3 {{ font-size: 16px; margin: 20px 0 6px; }}
.sub {{ color: var(--muted); margin: 0 0 16px; }}
.meta {{ color: var(--muted); font-size: 13px; }}
.notice {{ background: var(--warn); border-left: 4px solid #c98a00; padding: 10px 14px; margin: 14px 0; font-size: 14px; }}
.callout {{ background: var(--tint); border-left: 4px solid var(--accent); padding: 10px 14px; margin: 12px 0; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; margin: 8px 0 14px; }}
th, td {{ border-bottom: 1px solid var(--rule); padding: 6px 8px; text-align: left; vertical-align: top; }}
th {{ background: #f6f6f4; font-weight: 600; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.small {{ font-size: 12.5px; color: var(--muted); }}
img {{ max-width: 100%; height: auto; border: 1px solid var(--rule); border-radius: 6px; margin: 6px 0 4px; }}
ul {{ margin: 6px 0 10px; }}
@media print {{ body {{ padding: 0; font-size: 12px; }} h2 {{ break-after: avoid; }} img, table {{ break-inside: avoid; }} }}
</style></head><body>
<p class="meta">climate-tst-sim {_e(__version__)} · report generated {_e(stamp)}</p>
<h1>{_e(title)}</h1><p class="sub">{_e(subtitle)}</p>
{notice}
{''.join(sections)}
<p class="small" style="margin-top:40px">To save as PDF, use your browser's Print command and choose "Save as PDF".
The simulator code was written with substantial AI assistance; the author is responsible for its design and interpretation.</p>
</body></html>"""


# ------------------------------------------------------------------ trade-offs
def tradeoff_section(mc: dict) -> str:
    objs = mc["objectives"]
    names = mc["policies"]
    w = mc["weights"]
    meth_label = METHODS[mc["method"]]
    rows = []
    for i, p in enumerate(names):
        rows.append([_e(p)] + [_f(mc["values"][i][j], o["digits"]) for j, o in enumerate(objs)]
                    + [_f(mc["scores"][i], 3), "Yes" if mc["pareto"][i] else "No"])
    ranking = "".join(f"<li>{_e(p)}</li>" for p in mc["ranking"])
    meth_rows = [[_e(METHODS[m].split(":")[0]), _e(v["first"])] +
                 [_f(100 * a, 0) + "%" for a in v["acceptability"]] for m, v in mc["by_method"].items()]
    excl = ""
    if mc.get("excluded"):
        excl = ('<div class="callout"><strong>Removed by a hard limit before ranking:</strong><ul>' +
                "".join(f"<li>{_e(x['policy'])}: {_e(x['reason'])}</li>" for x in mc["excluded"]) + "</ul></div>")
    exp = mc["explanation"]
    pro = "".join(f"<li>Better on {_e(x['objective'].lower())} ({_e(x['theme'])})</li>" for x in exp["for"]) or "<li>None</li>"
    con = "".join(
        f"<li>{_e(exp['loser_name'])} is better on {_e(x['objective'].lower())}; the ranking reverses if this outcome gets at least "
        f"{_f(100 * x['flip_weight_share'], 0)}% of the total weight</li>" for x in exp["against"]) or "<li>None</li>"

    # chart: min-max heat map + first-place share
    G = np.array(mc["goodness"])
    fig, axes = plt.subplots(1, 2, figsize=(10, 0.6 + 0.48 * len(names)), gridspec_kw={"width_ratios": [3, 2]})
    ax = axes[0]
    ax.imshow(G, cmap="Greens", vmin=0, vmax=1.15, aspect="auto")
    ax.set_xticks(range(len(objs)), [o["label"] for o in objs], fontsize=7.5)
    ax.set_yticks(range(len(names)), [n[:34] for n in names], fontsize=7.5)
    V = np.array(mc["values"])
    for i in range(G.shape[0]):
        for j, o in enumerate(objs):
            ax.text(j, i, f"{V[i, j]:.{o['digits']}f}", ha="center", va="center", fontsize=7,
                    color="white" if G[i, j] > 0.7 else INK)
    ax.grid(False)
    ax.set_title(f"Scaled scores ({mc['method']}); darker = better", fontsize=9)
    ax2 = axes[1]
    y = np.arange(len(names))[::-1]
    h = 0.8 / len(METHODS)
    for k, m in enumerate(METHODS):
        ax2.barh(y + (len(METHODS) / 2 - k - 0.5) * h, mc["by_method"][m]["acceptability"], height=h, color=C[k], label=m)
    ax2.set_yticks(y, [""] * len(names))
    ax2.set_xlim(0, 1)
    ax2.set_title("Share of random weightings ranked first", fontsize=9)
    ax2.legend(frameon=False, fontsize=7, loc="lower right")
    ax2.grid(axis="y", visible=False)
    img = _png(fig)

    weights_txt = ", ".join(f"{o['label']} {_f(100 * w[o['key']], 0)}%" for o in objs)
    limits_txt = ""
    if mc.get("limits"):
        by = {o["key"]: o for o in objs}
        limits_txt = "<p><strong>Hard limits:</strong> " + ", ".join(
            f"{_e(by[k]['label'])} at most {_f(v, by[k]['digits'])}" for k, v in mc["limits"].items()) + "</p>"
    return f"""
<h2>Multi-criteria trade-offs</h2>
<p>Each policy is treated as an option and each outcome as a separate objective to minimise:
{', '.join(f"<strong>{_e(o['label'].lower())}</strong> ({_e(o['theme'].lower())})" for o in objs)}.</p>
<p><strong>Scaling:</strong> {_e(meth_label)}. <strong>Weights:</strong> {weights_txt}.
{('<strong>Reference policy:</strong> ' + _e(mc['reference']) + '.') if mc['method'] == 'reference' else ''}</p>
{limits_txt}{excl}
{_table(["Policy"] + [f"{o['label']} ({o['unit']})" for o in objs] + ["Weighted score", "Pareto-optimal"], rows)}
<h3>Ranking under these settings</h3><ol>{ranking}</ol>
<img src="{img}" alt="Scaled scores and first-place share by scaling method">
<h3>Does the scaling method change the answer?</h3>
<p class="small">Columns after "Ranked first" give the share of 4,000 random weightings under which each policy comes first.</p>
{_table(["Scaling method", "Ranked first (these weights)"] + [n[:28] for n in names], meth_rows, num_from=2)}
<h3>Why {_e(exp['winner_name'])} over {_e(exp['loser_name'])}?</h3>
<div class="callout">{_e(exp['text'])}</div>
<p><strong>For {_e(exp['winner_name'])}</strong></p><ul>{pro}</ul>
<p><strong>Against</strong></p><ul>{con}</ul>
<p class="small">Pareto-optimal means no other policy is at least as good on every outcome and better on one.
Dominated policies: {_e(', '.join(f"{k} (by {', '.join(v)})" for k, v in mc['dominated_by'].items() if v) or 'none')}.</p>
"""


def tradeoff_settings(q: dict) -> dict:
    """Parse trade-off settings from request query parameters."""
    out = {}
    if q.get("method"):
        out["method"] = q["method"]
    if q.get("weights"):
        out["weights"] = [float(x) for x in str(q["weights"]).split(",")]
    if q.get("reference"):
        out["reference"] = q["reference"]
    lim = {}
    for o in OBJECTIVES:
        v = q.get(f"limit_{o.key}")
        if v not in (None, ""):
            lim[o.key] = float(v)
    if lim:
        out["limits"] = lim
    return out


# ------------------------------------------------------------------ simulation
def simulation_report(res: dict, fmt: str, tradeoff: dict | None = None):
    summary = res["summary"]
    tradeoff = tradeoff or {}
    mc = None
    if len(summary) >= 2:
        try:
            mc = analyse(summary_to_frame(summary), **tradeoff)
        except ValueError:
            mc = None
    if fmt == "json":
        return json.dumps({"simulation": res, "tradeoffs": mc}, indent=2, default=float), "application/json", "json"
    rows = []
    for r in summary:
        row = {"policy": r["policy"]}
        for k, label, _ in SIM_METRICS:
            v = r.get(k) or {}
            row[f"{k}_mean"], row[f"{k}_p05"], row[f"{k}_p95"] = v.get("mean"), v.get("p05"), v.get("p95")
        if mc and r["policy"] in mc["policies"]:
            i = mc["policies"].index(r["policy"])
            row["weighted_score"], row["pareto_optimal"] = mc["scores"][i], mc["pareto"][i]
        rows.append(row)
    if fmt == "csv":
        return pd.DataFrame(rows).to_csv(index=False), "text/csv", "csv"

    st = res["settings"]
    reps = st["replicates"]
    tbl = _table(["Policy"] + [lab for _, lab, _ in SIM_METRICS], [
        [_e(r["policy"])] + [
            _f((r.get(k) or {}).get("mean"), d) + (f'<div class="small">{_f(r[k]["p05"], d)}–{_f(r[k]["p95"], d)}</div>'
                                                  if reps > 1 and r.get(k) else "")
            for k, _, d in SIM_METRICS]
        for r in summary])
    # bar chart panel
    fig, axes = plt.subplots(1, 5, figsize=(11, 0.7 + 0.42 * len(summary)), sharey=True)
    labels = [r["policy"][:30] for r in summary]
    ypos = np.arange(len(labels))[::-1]
    panel = [m for m in SIM_METRICS if m[0] != "check_minutes_per_goat_year"]
    for ax, (k, lab, _) in zip(axes, panel):
        mean = np.array([(r.get(k) or {}).get("mean") or 0 for r in summary], float)
        lo = np.array([(r.get(k) or {}).get("p05") or 0 for r in summary], float)
        hi = np.array([(r.get(k) or {}).get("p95") or 0 for r in summary], float)
        ax.barh(ypos, mean, color=C[0], height=0.55)
        if reps > 1:
            ax.errorbar(mean, ypos, xerr=[np.clip(mean - lo, 0, None), np.clip(hi - mean, 0, None)], fmt="none",
                        ecolor=INK2, elinewidth=1, capsize=2)
        ax.set_title(SHORT_TITLES.get(k, lab), fontsize=8)
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(ypos, labels, fontsize=7.5)
    img1 = _png(fig)
    # resistance over time
    fig, ax = plt.subplots(figsize=(9, 3.0))
    for i, r in enumerate(summary):
        sr = res["series"].get(r["key"])
        if sr:
            ax.plot(pd.to_datetime(sr["dates"]), sr["resistant"], color=C[i % len(C)], lw=1.8, label=r["policy"][:34])
    ax.set_ylim(0, 1.02)
    ax.set_ylabel("Resistant share of worms")
    ax.legend(frameon=False, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.15), ncol=3)
    img2 = _png(fig)

    sim = f"""
<h2>Settings</h2>
{_table(["Setting", "Value"], [
        ["Weather", _e(res['weather']['name']) + (" (synthetic)" if res['weather']['synthetic'] else " (real weather)")],
        ["Replicates", reps], ["Farms (3–8 goats each)", st['n_farms']],
        ["Extra warming", f"{st['warming_c']} °C"], ["Burn-in before management", f"{st['burn_in_days']} days"],
        ["Days recorded", st['recorded_days']],
    ] + [[_e(k), _e(v)] for k, v in st.items() if k not in ('replicates', 'n_farms', 'warming_c', 'burn_in_days', 'recorded_days')], num_from=9)}
<h2>Policy outcomes</h2>
<p>Means across replicates{', with 5th–95th percentiles underneath' if reps > 1 else ''}. Labour counts checking time plus about 2 minutes per dose.</p>
{tbl}
<img src="{img1}" alt="Policy outcomes">
<h3>Drug resistance over time</h3>
<img src="{img2}" alt="Resistant share of worms over time">
"""
    sections = [sim] + ([tradeoff_section(mc)] if mc else [])
    sections.append("""
<h2>How to read this</h2>
<ul>
<li>No policy is best on every outcome. Treating everyone cuts deaths but uses the most drugs and selects hardest for resistance; targeted checks save drugs but cost labour.</li>
<li>The multi-criteria ranking depends on how outcomes are scaled and weighted. The table above shows how often each policy comes first across many weightings and scaling methods.</li>
<li>A hard limit (for example "no more deaths than current practice") stops an option that is cheap but harmful from winning on the other outcomes.</li>
<li>All parameters are illustrative. The numbers test the method; they are not evidence about real farms.</li>
</ul>""")
    html_out = _page("Simulation report: goat worm-control policies",
                     f"{res['weather']['name']} · {reps} replicate(s) · {len(summary)} policies", sections,
                     synthetic=True)
    return html_out, "text/html", "html"


# ------------------------------------------------------------------ indicators and triage
def indicators_report(res: dict, fmt: str):
    if fmt == "json":
        return json.dumps(res, indent=2, default=float), "application/json", "json"
    df = pd.DataFrame(res["rows"])
    if fmt == "csv":
        return df.to_csv(index=False), "text/csv", "csv"
    fig, ax = plt.subplots(figsize=(8, 0.6 + 0.38 * len(df)))
    y = np.arange(len(df))[::-1]
    ax.barh(y, df["auroc"], color=C[0], height=0.55)
    ax.set_yticks(y, [f"{r.indicator_set} ({r.minutes_per_check:g} min)" for r in df.itertuples()], fontsize=8)
    ax.set_xlim(0.4, 1)
    ax.set_xlabel("AUROC (0.5 = chance, 1 = perfect)")
    ax.grid(axis="y", visible=False)
    img = _png(fig)
    rows = [[_e(r["indicator_set"]), _f(r["minutes_per_check"], 1), _f(r["auroc"], 3), _f(r["sens_at_spec80"], 2),
             _f(r["brier"], 3), "Yes" if r["pareto"] else ""] for r in res["rows"]]
    ds = res["dataset"]
    body = f"""
<h2>Indicator value for effort</h2>
<p>{_e(ds['name'])}: {res['n_checks']:,} checks, {_f(100 * res['prevalence'], 1)}% needed treatment. {_e(res['rows'][0].get('cv', ''))}.</p>
{_table(["Indicator set", "Minutes per goat", "AUROC", "Sensitivity at 80% specificity", "Brier score", "Best value for effort"], rows)}
<img src="{img}" alt="AUROC by indicator set">
{('<p class="small">Not scored (columns missing): ' + _e(', '.join(res['skipped'])) + '</p>') if res.get('skipped') else ''}
{'<div class="callout">These records come from the simulator, where need is defined mostly by anaemia, so FAMACHA is favoured by construction.</div>' if ds.get('source') == 'simulated' else ''}
"""
    return _page("Indicator report: which checks are worth it?", ds["name"], [body], ds.get("synthetic", True)), "text/html", "html"


def triage_report(res: dict, fmt: str):
    if fmt == "json":
        return json.dumps(res, indent=2, default=float), "application/json", "json"
    df = pd.DataFrame(res["rows"])
    if fmt == "csv":
        return df.to_csv(index=False), "text/csv", "csv"
    rows = [[_e(r["test_set"]), f"{r['n_checks']:,}", _f(100 * r["observed_rate"], 1) + "%",
             _f(100 * r["mean_predicted"], 1) + "%", _f(r["auroc"], 3), _f(r["sensitivity"], 2), _f(r["specificity"], 2)]
            for r in res["rows"]]
    fig, ax = plt.subplots(figsize=(8, 0.8 + 0.5 * len(df)))
    y = np.arange(len(df))[::-1]
    ax.barh(y + 0.18, df["sensitivity"], height=0.34, color=C[0], label="Sensitivity")
    ax.barh(y - 0.18, df["specificity"], height=0.34, color=C[1], label="Specificity")
    ax.set_yticks(y, [s[:40] for s in df["test_set"]], fontsize=8)
    ax.set_xlim(0, 1)
    ax.legend(frameon=False, fontsize=8)
    ax.grid(axis="y", visible=False)
    img = _png(fig)
    coefs = _table(["Indicator", "Standardised coefficient"], [[_e(k), _f(v, 3)] for k, v in res["coefficients"].items()])
    body = f"""
<h2>Does the triage rule transfer?</h2>
<p>Indicators: {_e(', '.join(res['features']))}. Threshold {_f(res['threshold'], 3)}, chosen for {_f(100 * res['target_sensitivity'], 0)}% sensitivity on the training records, then applied unchanged to each test set.</p>
{_table(["Dataset", "Checks", "Observed need", "Mean predicted", "AUROC", "Sensitivity", "Specificity"], rows)}
<img src="{img}" alt="Sensitivity and specificity by dataset">
<h3>Model coefficients</h3>{coefs}
<p class="small">A drop in sensitivity, or a gap between observed and predicted need, on a new site signals that the rule needs local recalibration.</p>
"""
    synthetic = all(r.get("synthetic", True) for r in res["rows"])
    return _page("Triage transfer report", "Trained on one dataset, tested on others", [body], synthetic), "text/html", "html"


def climate_report(data: dict, fmt: str):
    if fmt == "json":
        return json.dumps(data, indent=2, default=float), "application/json", "json"
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    m = data["monthly"]
    df = pd.DataFrame({"month": months, "tpi_mean": m["tpi_mean"], "tpi_p10": m["tpi_lo"], "tpi_p90": m["tpi_hi"],
                       "rain_mm": m["rain"], "tmean_c": m["tmean"]})
    if fmt == "csv":
        return df.to_csv(index=False), "text/csv", "csv"
    fig, ax = plt.subplots(figsize=(8, 3))
    x = np.arange(12)
    ax.fill_between(x, [v or 0 for v in m["tpi_lo"]], [v or 0 for v in m["tpi_hi"]], color=C[0], alpha=0.15)
    ax.plot(x, [v or 0 for v in m["tpi_mean"]], color=C[0], lw=2)
    ax.axhline(0.45, color=INK2, ls="--", lw=0.8)
    ax.axhline(0.15, color=INK2, ls=":", lw=0.8)
    ax.set_xticks(x, months)
    ax.set_ylabel("Transmission index")
    ax.set_ylim(0, None)
    img = _png(fig)
    s = data["summary"]
    rows = [[mo, _f(a, 2), _f(r, 0), _f(t, 1)] for mo, a, r, t in zip(months, m["tpi_mean"], m["rain"], m["tmean"])]
    body = f"""
<h2>Summary</h2>
{_table(["Item", "Value"], [["Period", f"{_e(s['start'])} to {_e(s['end'])} ({s['years']} years)"],
                            ["Latitude", _f(data['lat'], 2)], ["Extra warming applied", f"{data['warming']} °C"],
                            ["Annual rainfall", f"{_f(s['annual_rain'], 0)} mm"], ["Mean temperature", f"{_f(s['mean_temp'], 1)} °C"],
                            ["Months at high risk", f"{s['high_months']} of 12"],
                            ["Days high / low risk", f"{_f(s['pct_days_high'], 0)}% / {_f(s['pct_days_low'], 0)}%"]], num_from=9)}
<h2>Transmission index by month</h2>
<img src="{img}" alt="Monthly transmission index">
<p class="small">Line = mean across years; band = 10th–90th percentile. Dashed = high (0.45), dotted = moderate (0.15) thresholds used for climate-gated checking.
The index describes climatic suitability for larvae, not infection.</p>
{_table(["Month", "Transmission index", "Rainfall, mm", "Mean temp, °C"], rows)}
"""
    return _page(f"Climate report: {data['name']}", "Climatic suitability for worm larvae on pasture", [body],
                 data.get("synthetic", True)), "text/html", "html"
