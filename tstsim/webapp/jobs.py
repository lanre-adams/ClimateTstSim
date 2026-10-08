"""Background jobs for the web app: simulation, indicator study, triage transfer."""
from __future__ import annotations

import math
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd

from ..analysis import (
    FEATURE_SETS, available_feature_sets, evaluate_model, fit_triage, indicator_study, triage_features_for, _prep,
)
from ..datasets import DatasetError, Registry
from ..flock import FlockParams, simulate
from ..policies import ClimateGatedFPC, FamachaTST, FPCTST, NoTreatment, WholeFlockInterval
from ..transmission import transmission_potential

POLICIES = {
    "none": ("No treatment", NoTreatment),
    "whole60": ("Whole flock every 60 days", WholeFlockInterval),
    "famacha14": ("FAMACHA only, every 14 days", FamachaTST),
    "fpc14": ("Five-point-style check, every 14 days", FPCTST),
    "gated": ("Five-point-style check, climate-gated", ClimateGatedFPC),
}

ADVANCED = {
    "egg_to_pool": (1e-4, 1e-2),
    "initial_resistant_fraction": (0.0, 0.5),
    "efficacy_resistant": (0.0, 0.9),
    "famacha_error": (0.0, 0.6),
}


def clean(o):
    """Make results JSON-safe (NaN/inf -> None, numpy -> python)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return str(o.date())
    return o


class JobManager:
    def __init__(self, registry: Registry):
        self.reg = registry
        self.jobs: dict[str, dict] = {}
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()

    def submit(self, kind: str, fn, payload: dict) -> dict:
        job = {"id": uuid.uuid4().hex[:10], "kind": kind, "status": "queued", "progress": 0.0,
               "message": "Waiting to start", "result": None, "error": None, "created": time.time()}
        with self.lock:
            self.jobs[job["id"]] = job
            if len(self.jobs) > 50:
                for k in sorted(self.jobs, key=lambda k: self.jobs[k]["created"])[:-50]:
                    self.jobs.pop(k, None)

        def run():
            job["status"] = "running"
            job["message"] = "Running"
            try:
                job["result"] = clean(fn(payload, job))
                job["status"] = "done"
                job["progress"] = 1.0
                job["message"] = "Finished"
            except (DatasetError, ValueError) as exc:
                job["status"] = "error"
                job["error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                job["status"] = "error"
                job["error"] = f"Unexpected error: {exc}"
                traceback.print_exc()

        self.pool.submit(run)
        return job

    def get(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    # ------------------------------------------------------------------ simulation
    def run_simulation(self, p: dict, job: dict) -> dict:
        meta = self.reg.get(p["weather_id"])
        if meta.kind != "weather":
            raise DatasetError("Choose a weather dataset for the simulation.")
        keys = [k for k in p.get("policies", []) if k in POLICIES]
        if not keys:
            raise ValueError("Choose at least one policy.")
        reps = int(p.get("replicates", 3))
        if not 1 <= reps <= 10:
            raise ValueError("Replicates must be between 1 and 10.")
        n_farms = int(p.get("n_farms", 60))
        if not 5 <= n_farms <= 200:
            raise ValueError("Farms must be between 5 and 200.")
        warming = float(p.get("warming_c", 0.0))

        params = FlockParams(n_farms=n_farms)
        adv = {}
        for k, (lo, hi) in ADVANCED.items():
            if p.get(k) is not None and p.get(k) != "":
                v = float(p[k])
                if not lo <= v <= hi:
                    raise ValueError(f"{k} must be between {lo} and {hi}.")
                adv[k] = v
        params = replace(params, **adv)

        w = self.reg.load(meta.id)
        if warming:
            w[["tmean", "tmax", "tmin"]] += warming
        burn_in = int(min(180, len(w) // 4))
        if len(w) - burn_in < 180:
            raise DatasetError("The weather record is too short to simulate; at least about 8 months are needed.")
        lat = meta.lat if meta.lat is not None else 0.0

        want_checks = bool(p.get("save_checks"))
        run_keys = list(keys) + (["fpc14"] if want_checks and "fpc14" not in keys else [])
        total = len(run_keys) * reps
        done = 0
        rows, daily = [], []
        saved = None
        for r in range(reps):
            for k in run_keys:
                label, cls = POLICIES[k]
                job["message"] = f"Replicate {r + 1} of {reps}: {label}"
                res = simulate(w, cls(), params=params, seed=1000 + r, lat=lat, burn_in_days=burn_in)
                if k in keys:
                    s = dict(res.summary)
                    s.update({"key": k, "policy": label, "replicate": r})
                    rows.append(s)
                    dd = res.daily[["date", "worm_resistant_frac", "frac_need", "mean_pcv"]].copy()
                    dd["key"], dd["replicate"] = k, r
                    daily.append(dd)
                if want_checks and k == "fpc14" and r == 0:
                    c = res.checks.copy()
                    c["date"] = pd.to_datetime(w["date"].values[c["day"].values])
                    name = p.get("checks_name") or f"Simulated checks: {meta.name}"
                    saved = self.reg.add_simulated_checks(
                        c, name, meta.id,
                        {"n_farms": n_farms, "warming_c": warming, "seed": 1000, **adv},
                    ).id
                done += 1
                job["progress"] = done / total

        runs = pd.DataFrame(rows)
        metrics = ["deaths_per_100_goat_years", "treatments_per_goat_year", "check_minutes_per_goat_year",
                   "pct_goat_days_anaemic", "final_resistant_frac"]
        summary = []
        for k in keys:
            sub = runs[runs.key == k]
            row = {"key": k, "policy": POLICIES[k][0]}
            for m in metrics:
                row[m] = {"mean": sub[m].mean(), "p05": sub[m].quantile(0.05), "p95": sub[m].quantile(0.95)}
            summary.append(row)

        dly = pd.concat(daily, ignore_index=True)
        dly["date"] = pd.to_datetime(dly["date"])
        dly["week"] = dly["date"].dt.to_period("W").dt.start_time
        wk = dly.groupby(["key", "week"])[["worm_resistant_frac", "frac_need", "mean_pcv"]].mean().reset_index()
        series = {
            k: {
                "dates": [str(d.date()) for d in wk[wk.key == k]["week"]],
                "resistant": wk[wk.key == k]["worm_resistant_frac"].round(4).tolist(),
                "need": (100 * wk[wk.key == k]["frac_need"]).round(3).tolist(),
            }
            for k in keys
        }
        tpi = transmission_potential(w, lat)
        tpi_w = tpi.iloc[burn_in:].resample("W-MON", label="left", closed="left").mean()
        return {
            "weather": {"id": meta.id, "name": meta.name, "synthetic": meta.synthetic},
            "settings": {"replicates": reps, "n_farms": n_farms, "warming_c": warming, "burn_in_days": burn_in,
                         "recorded_days": len(w) - burn_in, **adv},
            "summary": summary,
            "series": series,
            "tpi": {"dates": [str(d.date()) for d in tpi_w.index], "values": tpi_w.round(4).tolist()},
            "saved_checks_id": saved,
        }

    # ------------------------------------------------------------------ indicator study
    def run_indicators(self, p: dict, job: dict) -> dict:
        meta = self.reg.get(p["dataset_id"])
        if meta.kind != "checks":
            raise DatasetError("Choose a check-records dataset.")
        df = _prep(self.reg.load(meta.id))
        sets = available_feature_sets(df)
        job["message"] = f"Cross-validating {len(sets)} indicator sets"
        res = indicator_study(df, feature_sets=sets)
        return {
            "dataset": {"id": meta.id, "name": meta.name, "source": meta.source, "synthetic": meta.synthetic},
            "n_checks": len(df), "prevalence": float(df["need"].mean()),
            "skipped": [k for k in FEATURE_SETS if k not in sets],
            "rows": res.to_dict(orient="records"),
        }

    # ------------------------------------------------------------------ triage transfer
    def run_triage(self, p: dict, job: dict) -> dict:
        train_meta = self.reg.get(p["train_id"])
        test_ids = [t for t in p.get("test_ids", []) if t and t != p["train_id"]]
        metas = [train_meta] + [self.reg.get(t) for t in test_ids]
        if any(m.kind != "checks" for m in metas):
            raise DatasetError("Training and test datasets must all be check records.")
        dfs = [_prep(self.reg.load(m.id)) for m in metas]
        feats = triage_features_for(*dfs)
        if "famacha" not in feats:
            raise DatasetError("Every dataset needs a complete 'famacha' column.")
        target = float(p.get("target_sensitivity", 0.9))
        if not 0.5 <= target <= 0.99:
            raise ValueError("Target sensitivity must be between 0.5 and 0.99.")
        job["message"] = "Fitting the triage model"
        dfs = [d.dropna(subset=feats + ["need"]) for d in dfs]
        model, thr, coefs = fit_triage(dfs[0], target_sens=target, features=feats)
        rows = []
        for m, d in zip(metas, dfs):
            label = f"{m.name} (training, in-sample)" if m.id == train_meta.id else m.name
            r = evaluate_model(model, d, thr, label, features=feats)
            r["dataset_id"], r["synthetic"], r["source"] = m.id, m.synthetic, m.source
            rows.append(r)
        return {"features": feats, "threshold": thr, "target_sensitivity": target,
                "coefficients": coefs, "rows": rows}
