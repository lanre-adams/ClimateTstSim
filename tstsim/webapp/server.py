"""FastAPI server for the climate-tst-sim web interface.

Run:  python -m tstsim.webapp            (http://localhost:8000)
      uvicorn tstsim.webapp.server:app --host 0.0.0.0 --port 8000

This is a local research tool with no authentication. Do not expose it to
the public internet.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import SYNTHETIC_NOTICE, __version__
from ..climate import SITES
from ..datasets import CHECKS_TEMPLATE, WEATHER_TEMPLATE, DatasetError, Registry, meta_dict
from ..transmission import risk_band, transmission_potential
from .jobs import ADVANCED, POLICIES, JobManager, clean

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
OUTPUTS = ROOT / "outputs"


class SyntheticIn(BaseModel):
    name: str = ""
    site: str | None = None
    years: int = Field(4, ge=1, le=30)
    seed: int = 1
    warming_c: float = Field(0.0, ge=-3, le=6)
    rain_scale: float = Field(1.0, ge=0.2, le=3)
    lat: float | None = None
    lon: float | None = None
    tmean: list[float] | None = None
    rain_mm: list[float] | None = None


def create_app(data_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="climate-tst-sim", version=__version__, docs_url="/api/docs")
    reg = Registry(data_dir)
    jobs = JobManager(reg)
    app.state.registry, app.state.jobs = reg, jobs

    @app.exception_handler(DatasetError)
    async def _ds_error(_, exc: DatasetError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ------------------------------------------------------------ meta
    @app.get("/api/info")
    def info():
        return {
            "version": __version__, "notice": SYNTHETIC_NOTICE,
            "sites": {k: {"label": v.label, "lat": v.lat, "lon": v.lon, "tmean": v.tmean, "rain_mm": v.rain_mm}
                      for k, v in SITES.items()},
            "policies": {k: v[0] for k, v in POLICIES.items()},
            "advanced": {k: {"min": lo, "max": hi} for k, (lo, hi) in ADVANCED.items()},
            "has_results": (OUTPUTS / "results.json").exists(),
        }

    @app.get("/api/results")
    def results():
        f = OUTPUTS / "results.json"
        if not f.exists():
            raise HTTPException(404, "No full run found. Run `python scripts/run_all.py` first.")
        return json.loads(f.read_text())

    # ------------------------------------------------------------ datasets
    @app.get("/api/datasets")
    def list_datasets(kind: str | None = None):
        return [meta_dict(m) for m in reg.list(kind)]

    @app.get("/api/datasets/{ds_id}")
    def get_dataset(ds_id: str):
        return meta_dict(reg.get(ds_id))

    @app.get("/api/datasets/{ds_id}/preview")
    def preview(ds_id: str, n: int = 12):
        df = reg.load(ds_id).drop(columns=["site", "synthetic"], errors="ignore")
        head = df.head(max(1, min(n, 50))).copy()
        for c in head.columns:
            if np.issubdtype(head[c].dtype, np.datetime64):
                head[c] = head[c].dt.strftime("%Y-%m-%d")
        return clean({"columns": list(head.columns), "rows": head.values.tolist(), "total": len(df)})

    @app.get("/api/datasets/{ds_id}/download")
    def download(ds_id: str):
        meta = reg.get(ds_id)
        df = reg.load(ds_id).drop(columns=["site", "synthetic"], errors="ignore")
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in meta.name)[:60] or ds_id
        return PlainTextResponse(df.to_csv(index=False), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'})

    @app.delete("/api/datasets/{ds_id}")
    def delete(ds_id: str):
        reg.delete(ds_id)
        return {"deleted": ds_id}

    @app.post("/api/datasets/weather")
    async def upload_weather(file: UploadFile = File(...), name: str = Form(""), lat: float = Form(...),
                             lon: float | None = Form(None), notes: str = Form(""), synthetic: bool = Form(False)):
        raw = await file.read()
        meta = reg.add_weather_upload(raw, name or Path(file.filename or "weather").stem, lat, lon, notes, synthetic)
        return meta_dict(meta)

    @app.post("/api/datasets/checks")
    async def upload_checks(file: UploadFile = File(...), name: str = Form(""), linked_weather: str = Form(""),
                            notes: str = Form(""), synthetic: bool = Form(False)):
        raw = await file.read()
        meta = reg.add_checks_upload(raw, name or Path(file.filename or "checks").stem, linked_weather or None, notes,
                                     synthetic)
        return meta_dict(meta)

    @app.post("/api/datasets/synthetic")
    def create_synthetic(body: SyntheticIn):
        profile = None
        if body.tmean or body.rain_mm:
            if body.lat is None:
                raise DatasetError("Enter a latitude for the custom site.")
            profile = {"lat": body.lat, "lon": body.lon or 0.0, "tmean": body.tmean or [], "rain_mm": body.rain_mm or []}
        meta = reg.add_synthetic(body.name, body.years, body.seed, body.warming_c, body.rain_scale, body.site, profile)
        return meta_dict(meta)

    @app.get("/api/templates/{kind}")
    def template(kind: str):
        text = {"weather": WEATHER_TEMPLATE, "checks": CHECKS_TEMPLATE}.get(kind)
        if text is None:
            raise HTTPException(404, "Unknown template")
        return PlainTextResponse(text, media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="template_{kind}.csv"'})

    # ------------------------------------------------------------ climate
    @app.get("/api/climate/{ds_id}")
    def climate(ds_id: str, warming: float = 0.0):
        meta = reg.get(ds_id)
        if meta.kind != "weather":
            raise DatasetError("Climate view needs a weather dataset.")
        if not -3 <= warming <= 6:
            raise DatasetError("Warming must be between −3 and 6 °C.")
        w = reg.load(ds_id)
        if warming:
            w[["tmean", "tmax", "tmin"]] += warming
        lat = meta.lat if meta.lat is not None else 0.0
        tpi = transmission_potential(w, lat)
        w = w.set_index(pd.to_datetime(w["date"]))
        w["tpi"] = tpi.values
        w["band"] = risk_band(tpi.values)
        weekly = w.resample("W-MON", label="left", closed="left").agg({"tpi": "mean", "rain": "sum", "tmean": "mean"})
        monthly = w.resample("MS").agg({"tpi": "mean", "rain": "sum", "tmean": "mean"})
        monthly["m"] = monthly.index.month
        clim = monthly.groupby("m").agg(
            tpi_mean=("tpi", "mean"), tpi_lo=("tpi", lambda s: s.quantile(0.1)), tpi_hi=("tpi", lambda s: s.quantile(0.9)),
            rain=("rain", "mean"), tmean=("tmean", "mean"))
        clim = clim.reindex(range(1, 13))
        years = len(w) / 365.25
        return clean({
            "id": meta.id, "name": meta.name, "synthetic": meta.synthetic, "lat": lat, "warming": warming,
            "summary": {
                "days": len(w), "years": round(years, 1), "start": str(w.index.min().date()), "end": str(w.index.max().date()),
                "annual_rain": float(w["rain"].sum() / years), "mean_temp": float(w["tmean"].mean()),
                "pct_days_high": float(100 * (w["band"] == 2).mean()), "pct_days_low": float(100 * (w["band"] == 0).mean()),
                "high_months": int((clim["tpi_mean"] >= 0.45).sum()),
            },
            "monthly": {k: clim[k].round(4).tolist() for k in ["tpi_mean", "tpi_lo", "tpi_hi", "rain", "tmean"]},
            "weekly": {"dates": [str(d.date()) for d in weekly.index], "tpi": weekly["tpi"].round(4).tolist(),
                       "rain": weekly["rain"].round(1).tolist(), "tmean": weekly["tmean"].round(2).tolist()},
        })

    # ------------------------------------------------------------ jobs
    @app.post("/api/simulate")
    def simulate_job(body: dict):
        return jobs.submit("simulate", jobs.run_simulation, body)

    @app.post("/api/indicators")
    def indicators_job(body: dict):
        return jobs.submit("indicators", jobs.run_indicators, body)

    @app.post("/api/triage")
    def triage_job(body: dict):
        return jobs.submit("triage", jobs.run_triage, body)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Job not found (the server may have restarted).")
        return j

    # ------------------------------------------------------------ static
    if OUTPUTS.exists():
        app.mount("/outputs", StaticFiles(directory=str(OUTPUTS)), name="outputs")
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
