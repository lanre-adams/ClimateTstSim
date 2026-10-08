"""Dataset registry: built-in synthetic sites plus anything the user adds.

Two kinds of dataset:

* **weather**: daily rows with date, tmean, tmax, tmin, rain (mm). Can be
  uploaded (NASA POWER CSV or any daily CSV with recognisable column names)
  or generated from a site profile.
* **checks**: one row per goat per check, with observed indicators and a
  0/1 label saying whether the goat needed treatment. Can be uploaded (real
  field records) or saved from a simulation run.

User datasets are stored as normalised CSV files plus one ``registry.json`` in
the data directory (env ``TSTSIM_DATA_DIR``, default ``data/user``).
"""
from __future__ import annotations

import io
import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .climate import SITES, custom_profile, parse_nasa_power_text, synthetic_weather
from .transmission import transmission_potential

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 25 * 1024 * 1024

WEATHER_ALIASES = {
    "date": ["date", "time", "datetime", "day_date", "obs_date"],
    "tmean": ["tmean", "t2m", "tavg", "temp", "temperature", "t_mean", "mean_temp", "tmean_c", "tas"],
    "tmax": ["tmax", "t2m_max", "max_temp", "t_max", "tmax_c", "tasmax"],
    "tmin": ["tmin", "t2m_min", "min_temp", "t_min", "tmin_c", "tasmin"],
    "rain": ["rain", "precip", "precipitation", "prectotcorr", "prcp", "rainfall", "rr", "pr", "rain_mm", "precip_mm"],
}

CHECK_ALIASES = {
    "famacha": ["famacha", "famacha_score", "eye_score"],
    "need": ["need", "needs_treatment", "need_treatment", "treatment_needed", "label", "should_treat"],
    "anaemic": ["anaemic", "anemic"],
    "bcs": ["bcs", "body_condition", "body_condition_score"],
    "bottle_jaw": ["bottle_jaw", "bottlejaw", "submandibular_oedema", "submandibular_edema"],
    "dag": ["dag", "dag_score", "scour", "diarrhoea", "diarrhea"],
    "nasal": ["nasal", "nasal_discharge"],
    "fec": ["fec", "epg", "faecal_egg_count", "fecal_egg_count"],
    "kid": ["kid", "is_kid", "young"],
    "days_since_treat": ["days_since_treat", "days_since_treatment"],
    "tpi": ["tpi", "transmission_index"],
    "farm": ["farm", "farm_id", "household", "household_id", "village"],
    "goat": ["goat", "goat_id", "animal", "animal_id"],
    "date": ["date", "check_date", "visit_date"],
}


class DatasetError(ValueError):
    """A problem with user data, worded so it can be shown to the user."""


@dataclass
class Meta:
    id: str
    name: str
    kind: str  # "weather" | "checks"
    source: str  # "builtin" | "upload" | "synthetic" | "simulated"
    synthetic: bool
    rows: int
    created: str
    lat: float | None = None
    lon: float | None = None
    start: str | None = None
    end: str | None = None
    notes: str = ""
    columns: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    linked_weather: str | None = None
    params: dict = field(default_factory=dict)

    @property
    def deletable(self) -> bool:
        return self.source != "builtin"


def _norm(col: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(col).strip().lower()).strip("_")


def _rename(df: pd.DataFrame, aliases: dict) -> pd.DataFrame:
    cols = {_norm(c): c for c in df.columns}
    mapping = {}
    for target, names in aliases.items():
        for n in names:
            if n in cols and cols[n] not in mapping:
                mapping[cols[n]] = target
                break
    return df.rename(columns=mapping)


def _decode(raw: bytes) -> str:
    if len(raw) > MAX_BYTES:
        raise DatasetError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise DatasetError("Could not read the file as text. Save it as CSV (UTF-8).")


def _read_csv(text: str) -> pd.DataFrame:
    """Read comma-separated text; fall back to delimiter sniffing for ; or tab files."""
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] == 1 and (";" in text.splitlines()[0] or "\t" in text.splitlines()[0]):
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    return df


# --------------------------------------------------------------------------- weather
def parse_weather(raw: bytes) -> tuple[pd.DataFrame, list[str]]:
    """Parse an uploaded weather CSV into the standard daily schema.

    Returns (df, warnings). Raises DatasetError with a user-facing message.
    """
    text = _decode(raw)
    warnings: list[str] = []
    if text.lstrip().startswith("-BEGIN HEADER-"):
        try:
            df = parse_nasa_power_text(text)
        except Exception as exc:  # noqa: BLE001
            raise DatasetError(f"Looks like a NASA POWER file but could not be read: {exc}") from exc
        df = df[["date", "tmean", "tmax", "tmin", "rain"]]
        warnings.append("Read as a NASA POWER file.")
    else:
        try:
            df = _read_csv(text)
        except Exception as exc:  # noqa: BLE001
            raise DatasetError(f"Could not read the CSV: {exc}") from exc
        df = _rename(df, WEATHER_ALIASES)
        lower = {_norm(c): c for c in df.columns}
        if "date" not in df.columns:
            if {"year", "mo", "dy"} <= set(lower) or {"year", "month", "day"} <= set(lower):
                y, m, d = lower["year"], lower.get("mo", lower.get("month")), lower.get("dy", lower.get("day"))
                df["date"] = pd.to_datetime(dict(year=df[y], month=df[m], day=df[d]), errors="coerce")
            elif {"year", "doy"} <= set(lower):
                df["date"] = pd.to_datetime(df[lower["year"]].astype(int).astype(str), format="%Y") + pd.to_timedelta(
                    df[lower["doy"]].astype(int) - 1, unit="D")
            else:
                raise DatasetError("No date column found. Use a 'date' column (YYYY-MM-DD) or year, month, day columns.")
        if "rain" not in df.columns:
            raise DatasetError("No rainfall column found. Name it 'rain' (mm per day), 'precip' or 'PRECTOTCORR'.")
        if "tmean" not in df.columns:
            if {"tmax", "tmin"} <= set(df.columns):
                df["tmean"] = (pd.to_numeric(df["tmax"], errors="coerce") + pd.to_numeric(df["tmin"], errors="coerce")) / 2
                warnings.append("No mean temperature column; used the average of tmax and tmin.")
            else:
                raise DatasetError("No temperature column found. Name it 'tmean' (°C), 'T2M' or 'temp'.")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        bad_dates = int(df["date"].isna().sum())
        if bad_dates:
            warnings.append(f"Dropped {bad_dates} rows with unreadable dates.")
        df = df.dropna(subset=["date"])
        for c in ("tmean", "tmax", "tmin", "rain"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").replace([-999, -999.0, -99, -9999], np.nan)
        if "tmax" not in df.columns or "tmin" not in df.columns:
            df["tmax"] = df["tmean"] + 5
            df["tmin"] = df["tmean"] - 5
            warnings.append("No tmax/tmin columns; assumed a 10 °C daily range. This affects the evaporation estimate.")
        df = df[["date", "tmean", "tmax", "tmin", "rain"]]

    return _clean_daily(df, warnings)


def _clean_daily(df: pd.DataFrame, warnings: list[str]) -> tuple[pd.DataFrame, list[str]]:
    df = df.sort_values("date")
    dups = int(df["date"].duplicated().sum())
    if dups:
        warnings.append(f"Removed {dups} duplicate dates (kept the first).")
        df = df.drop_duplicates("date")
    df = df.set_index("date")
    full = pd.date_range(df.index.min(), df.index.max(), freq="D")
    missing_days = len(full) - len(df)
    df = df.reindex(full)
    if len(df) < 120:
        raise DatasetError(f"Only {len(df)} days of data. At least 120 days are needed (a year or more is better).")
    for c in ("tmean", "tmax", "tmin"):
        bad = (df[c] < -30) | (df[c] > 55)
        if bad.any():
            warnings.append(f"{int(bad.sum())} implausible {c} values (outside −30 to 55 °C) treated as missing.")
            df.loc[bad, c] = np.nan
    neg = df["rain"] < 0
    if neg.any():
        df.loc[neg, "rain"] = np.nan
    huge = df["rain"] > 500
    if huge.any():
        warnings.append(f"{int(huge.sum())} daily rainfall values above 500 mm treated as missing.")
        df.loc[huge, "rain"] = np.nan
    t_missing = df["tmean"].isna().mean()
    r_missing = df["rain"].isna().mean()
    if t_missing > 0.2 or r_missing > 0.2:
        raise DatasetError(
            f"Too much missing data ({t_missing:.0%} of temperatures, {r_missing:.0%} of rainfall). "
            "Fill gaps or choose a period with fewer gaps.")
    if missing_days:
        warnings.append(f"{missing_days} calendar days were absent and have been filled.")
    if df[["tmean", "tmax", "tmin"]].isna().any().any():
        n = int(df["tmean"].isna().sum())
        warnings.append(f"Interpolated {n} missing temperature days.")
        df[["tmean", "tmax", "tmin"]] = df[["tmean", "tmax", "tmin"]].interpolate(limit_direction="both")
    if df["rain"].isna().any():
        warnings.append(f"Set {int(df['rain'].isna().sum())} missing rainfall days to 0 mm.")
        df["rain"] = df["rain"].fillna(0.0)
    swap = df["tmax"] < df["tmin"]
    if swap.any():
        warnings.append(f"Swapped tmax and tmin on {int(swap.sum())} days where tmax < tmin.")
        df.loc[swap, ["tmax", "tmin"]] = df.loc[swap, ["tmin", "tmax"]].values
    df = df.rename_axis("date").reset_index()
    return df.round({"tmean": 2, "tmax": 2, "tmin": 2, "rain": 2}), warnings


# --------------------------------------------------------------------------- checks
def _binary(series: pd.Series, name: str) -> pd.Series:
    s = series.astype(str).str.strip().str.lower()
    mapped = s.map({"1": 1, "0": 0, "yes": 1, "no": 0, "y": 1, "n": 0, "true": 1, "false": 0, "1.0": 1, "0.0": 0})
    if mapped.isna().mean() > 0.05:
        raise DatasetError(f"Column '{name}' should be 0/1 or yes/no.")
    return mapped


def parse_checks(raw: bytes, weather: pd.DataFrame | None = None, weather_lat: float | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Parse uploaded goat check records. Required: famacha (1-5) and need (0/1).

    If the records have dates and a weather dataset is linked, the transmission
    index for each check date is added as a 'tpi' column.
    """
    text = _decode(raw)
    warnings: list[str] = []
    try:
        df = _read_csv(text)
    except Exception as exc:  # noqa: BLE001
        raise DatasetError(f"Could not read the CSV: {exc}") from exc
    df = _rename(df, CHECK_ALIASES)
    if "famacha" not in df.columns:
        raise DatasetError("A 'famacha' column (scores 1-5) is required.")
    if "need" not in df.columns:
        if "anaemic" in df.columns:
            df["need"] = df["anaemic"]
            warnings.append("No 'need' column; used 'anaemic' as the label.")
        else:
            raise DatasetError("A 'need' column (1 = needed treatment, 0 = did not) is required as the label.")
    out = pd.DataFrame(index=df.index)
    out["famacha"] = pd.to_numeric(df["famacha"], errors="coerce")
    out["need"] = _binary(df["need"], "need")
    drop = out[["famacha", "need"]].isna().any(axis=1) | ~out["famacha"].between(1, 5)
    if drop.any():
        warnings.append(f"Dropped {int(drop.sum())} rows with a missing or out-of-range FAMACHA score or label.")
    for c in ("bottle_jaw", "dag", "nasal", "kid"):
        if c in df.columns:
            out[c] = _binary(df[c], c)
    if "bcs" in df.columns:
        out["bcs"] = pd.to_numeric(df["bcs"], errors="coerce")
        bad = ~out["bcs"].between(1, 5) & out["bcs"].notna()
        if bad.any():
            warnings.append(f"{int(bad.sum())} body condition scores outside 1-5 set to missing.")
            out.loc[bad, "bcs"] = np.nan
    for c in ("fec", "days_since_treat", "tpi"):
        if c in df.columns:
            out[c] = pd.to_numeric(df[c], errors="coerce")
    if "fec" in out.columns and (out["fec"] < 0).any():
        raise DatasetError("Faecal egg counts cannot be negative.")
    if "farm" in df.columns:
        out["farm"] = pd.factorize(df["farm"].astype(str))[0]
    else:
        warnings.append("No farm column; cross-validation will not be grouped by farm, so results may be optimistic.")
    if "goat" in df.columns:
        out["goat"] = df["goat"].astype(str)
    if "date" in df.columns:
        out["date"] = pd.to_datetime(df["date"], errors="coerce")
    out = out[~drop].reset_index(drop=True)
    if len(out) < 30:
        raise DatasetError(f"Only {len(out)} usable rows. At least 30 checks are needed.")
    pos = int(out["need"].sum())
    if pos < 5 or len(out) - pos < 5:
        raise DatasetError(f"Need at least 5 goats that needed treatment and 5 that did not (found {pos} and {len(out) - pos}).")

    if weather is not None and "date" in out.columns and out["date"].notna().any():
        if "tpi" in out.columns:
            warnings.append("Replaced the uploaded 'tpi' column with the index computed from the linked weather.")
        tpi = transmission_potential(weather, weather_lat if weather_lat is not None else 0.0)
        tpi.index = tpi.index.normalize()
        out["tpi"] = out["date"].dt.normalize().map(tpi)
        miss = out["tpi"].isna().mean()
        if miss > 0:
            warnings.append(f"{miss:.0%} of check dates fall outside the linked weather period; their climate index is missing.")
    elif weather is not None:
        warnings.append("A weather dataset was linked, but the records have no 'date' column, so no climate index was added.")

    for c in list(out.columns):
        if c in ("bcs", "fec", "tpi", "days_since_treat") and out[c].isna().mean() > 0.05:
            warnings.append(f"Column '{c}' is more than 5% missing and will be left out of indicator sets.")
    return out, warnings


# --------------------------------------------------------------------------- registry
class Registry:
    def __init__(self, data_dir: str | Path | None = None):
        import os

        self.dir = Path(data_dir or os.environ.get("TSTSIM_DATA_DIR") or ROOT / "data" / "user")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "registry.json"
        self._lock = threading.RLock()
        self._cache: dict[str, pd.DataFrame] = {}

    # ---- builtins
    def _builtins(self) -> list[Meta]:
        out = []
        for key, prof in SITES.items():
            out.append(Meta(
                id=f"builtin-{key}", name=prof.label, kind="weather", source="builtin", synthetic=True,
                rows=int(round(365.25 * 4)), created="", lat=prof.lat, lon=prof.lon,
                start="2021-01-01", end="2024-12-31",
                notes="Built-in synthetic site profile (4 years, seed 1). Illustrative monthly shape, not station data.",
                columns=["date", "tmean", "tmax", "tmin", "rain"], params={"site": key, "years": 4, "seed": 1},
            ))
        return out

    # ---- persistence
    def _read(self) -> list[dict]:
        if not self.file.exists():
            return []
        try:
            return json.loads(self.file.read_text())
        except json.JSONDecodeError:
            return []

    def _write(self, entries: list[dict]):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=2, default=str))
        tmp.replace(self.file)

    def list(self, kind: str | None = None) -> list[Meta]:
        with self._lock:
            metas = self._builtins() + [Meta(**e) for e in self._read()]
        return [m for m in metas if kind is None or m.kind == kind]

    def get(self, ds_id: str) -> Meta:
        for m in self.list():
            if m.id == ds_id:
                return m
        raise DatasetError(f"No dataset with id '{ds_id}'.")

    def path(self, ds_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", ds_id):
            raise DatasetError("Invalid dataset id.")
        return self.dir / f"{ds_id}.csv"

    def load(self, ds_id: str) -> pd.DataFrame:
        if ds_id in self._cache:
            return self._cache[ds_id].copy()
        meta = self.get(ds_id)
        if meta.source == "builtin":
            p = meta.params
            df = synthetic_weather(p["site"], years=p["years"], seed=p["seed"])
        else:
            df = pd.read_csv(self.path(ds_id), parse_dates=["date"] if "date" in meta.columns else False)
        if meta.kind == "weather":
            df["site"] = meta.id
        self._cache[ds_id] = df
        return df.copy()

    def _add(self, df: pd.DataFrame, meta: Meta) -> Meta:
        with self._lock:
            df.to_csv(self.path(meta.id), index=False)
            entries = self._read()
            entries.append(asdict(meta))
            self._write(entries)
        return meta

    def delete(self, ds_id: str):
        meta = self.get(ds_id)
        if not meta.deletable:
            raise DatasetError("Built-in datasets cannot be deleted.")
        with self._lock:
            self._write([e for e in self._read() if e["id"] != ds_id])
            self.path(ds_id).unlink(missing_ok=True)
            self._cache.pop(ds_id, None)

    @staticmethod
    def _new_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ---- adders
    def add_weather_upload(self, raw: bytes, name: str, lat: float, lon: float | None = None, notes: str = "",
                           synthetic: bool = False) -> Meta:
        if not -60 <= lat <= 60:
            raise DatasetError("Latitude must be between −60 and 60 degrees.")
        df, warnings = parse_weather(raw)
        meta = Meta(
            id=self._new_id(), name=name.strip() or "Uploaded weather", kind="weather", source="upload",
            synthetic=bool(synthetic), rows=len(df), created=self._now(), lat=float(lat),
            lon=None if lon is None else float(lon), start=str(df["date"].min().date()),
            end=str(df["date"].max().date()), notes=notes, columns=list(df.columns), warnings=warnings,
        )
        return self._add(df, meta)

    def add_synthetic(self, name: str, years: int = 4, seed: int = 1, warming_c: float = 0.0, rain_scale: float = 1.0,
                      site: str | None = None, profile: dict | None = None) -> Meta:
        if not 1 <= years <= 30:
            raise DatasetError("Years must be between 1 and 30.")
        if profile:
            prof = custom_profile(name=_norm(name) or "custom", label=name, lat=profile["lat"], lon=profile.get("lon", 0.0),
                                  tmean=profile["tmean"], rain_mm=profile["rain_mm"])
            if any(not -10 <= t <= 45 for t in prof.tmean):
                raise DatasetError("Monthly mean temperatures must be between −10 and 45 °C.")
        elif site in SITES:
            prof = SITES[site]
        else:
            raise DatasetError("Choose a preset site or enter 12 monthly values.")
        df = synthetic_weather(prof, years=years, seed=seed, warming_c=warming_c, rain_scale=rain_scale)
        df = df[["date", "tmean", "tmax", "tmin", "rain"]]
        meta = Meta(
            id=self._new_id(), name=name.strip() or prof.label, kind="weather", source="synthetic", synthetic=True,
            rows=len(df), created=self._now(), lat=prof.lat, lon=prof.lon, start=str(df["date"].min().date()),
            end=str(df["date"].max().date()),
            notes=f"Synthetic from {'custom monthly values' if profile else prof.label}; warming {warming_c:+.1f} °C, rain ×{rain_scale:g}.",
            columns=list(df.columns),
            params={"site": None if profile else site, "years": years, "seed": seed, "warming_c": warming_c,
                    "rain_scale": rain_scale, "profile": asdict(prof) if profile else None},
        )
        return self._add(df, meta)

    def add_checks_upload(self, raw: bytes, name: str, linked_weather: str | None = None, notes: str = "",
                          synthetic: bool = False) -> Meta:
        weather, lat = None, None
        if linked_weather:
            wm = self.get(linked_weather)
            if wm.kind != "weather":
                raise DatasetError("The linked dataset must be a weather dataset.")
            weather, lat = self.load(linked_weather), wm.lat
        df, warnings = parse_checks(raw, weather, lat)
        meta = Meta(
            id=self._new_id(), name=name.strip() or "Uploaded check records", kind="checks", source="upload",
            synthetic=bool(synthetic), rows=len(df), created=self._now(), notes=notes, columns=list(df.columns),
            warnings=warnings, linked_weather=linked_weather,
            start=str(df["date"].min().date()) if "date" in df.columns and df["date"].notna().any() else None,
            end=str(df["date"].max().date()) if "date" in df.columns and df["date"].notna().any() else None,
        )
        return self._add(df, meta)

    def add_simulated_checks(self, checks: pd.DataFrame, name: str, weather_id: str, params: dict) -> Meta:
        keep = [c for c in ["date", "farm", "goat", "famacha", "bcs", "bottle_jaw", "dag", "nasal", "fec", "kid",
                            "tpi", "days_since_treat", "need", "anaemic", "true_pcv"] if c in checks.columns]
        df = checks[keep].copy()
        meta = Meta(
            id=self._new_id(), name=name, kind="checks", source="simulated", synthetic=True, rows=len(df),
            created=self._now(), notes="Check records saved from a simulation run (five-point-style policy).",
            columns=list(df.columns), linked_weather=weather_id, params=params,
            start=str(pd.to_datetime(df["date"]).min().date()) if "date" in df.columns else None,
            end=str(pd.to_datetime(df["date"]).max().date()) if "date" in df.columns else None,
        )
        return self._add(df, meta)


def meta_dict(m: Meta) -> dict:
    d = asdict(m)
    d["deletable"] = m.deletable
    return d


WEATHER_TEMPLATE = """date,tmean,tmax,tmin,rain
2024-01-01,23.4,29.1,18.2,12.5
2024-01-02,23.0,28.6,18.0,0.0
2024-01-03,22.8,28.0,17.9,4.2
"""

CHECKS_TEMPLATE = """date,farm,goat,famacha,bcs,bottle_jaw,dag,nasal,fec,need
2024-01-15,F01,G001,2,3.0,0,0,0,350,0
2024-01-15,F01,G002,4,2.0,0,1,0,2850,1
2024-01-15,F02,G014,3,2.5,0,0,1,900,0
"""
