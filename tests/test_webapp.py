import io
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from tstsim.climate import synthetic_weather
from tstsim.datasets import DatasetError, Registry, parse_checks, parse_weather
from tstsim.webapp.server import create_app


def _weather_csv(days=400, **rename):
    w = synthetic_weather("lilongwe_like", years=days / 365.25, seed=3)[["date", "tmean", "tmax", "tmin", "rain"]]
    w = w.rename(columns=rename)
    return w.to_csv(index=False).encode()


def _checks_csv(n=200, seed=0, with_farm=True, with_date=True):
    rng = np.random.default_rng(seed)
    fam = rng.integers(1, 6, n)
    need = (fam >= 4).astype(int)
    flip = rng.random(n) < 0.1
    need[flip] = 1 - need[flip]
    df = pd.DataFrame({"famacha": fam, "bcs": rng.choice([1.5, 2, 2.5, 3, 3.5], n), "dag": rng.integers(0, 2, n), "need": need})
    if with_farm:
        df["farm"] = [f"F{i % 12}" for i in range(n)]
    if with_date:
        df["date"] = pd.date_range("2021-03-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return df.to_csv(index=False).encode()


# ---------------------------------------------------------------- parsing
def test_parse_weather_generic_and_aliases():
    df, warns = parse_weather(_weather_csv(tmean="T2M", rain="precip"))
    assert list(df.columns) == ["date", "tmean", "tmax", "tmin", "rain"]
    assert len(df) >= 399 and not df.isna().any().any()


def test_parse_weather_derives_tmean_and_fills_gaps():
    raw = _weather_csv()
    df = pd.read_csv(io.BytesIO(raw)).drop(columns=["tmean"])
    df = df.drop(index=range(50, 55))  # remove 5 calendar days
    df.loc[10, "tmax"] = 99  # implausible
    out, warns = parse_weather(df.to_csv(index=False).encode())
    assert out["date"].diff().dt.days.dropna().eq(1).all()
    assert any("average of tmax and tmin" in w for w in warns)
    assert any("absent" in w for w in warns)
    assert out["tmax"].max() < 55


def test_parse_weather_rejects_bad_files():
    with pytest.raises(DatasetError, match="rainfall"):
        parse_weather(b"date,tmean\n2021-01-01,20\n")
    with pytest.raises(DatasetError, match="120 days"):
        parse_weather(_weather_csv(days=60))
    with pytest.raises(DatasetError, match="date"):
        parse_weather(b"x,tmean,rain\n1,20,0\n")


def test_parse_weather_nasa_power_header():
    raw = _weather_csv(days=200)
    df = pd.read_csv(io.BytesIO(raw))
    d = pd.to_datetime(df["date"])
    power = pd.DataFrame({"YEAR": d.dt.year, "MO": d.dt.month, "DY": d.dt.day, "T2M": df.tmean,
                          "T2M_MAX": df.tmax, "T2M_MIN": df.tmin, "PRECTOTCORR": df.rain})
    text = "-BEGIN HEADER-\nNASA/POWER\n-END HEADER-\n" + power.to_csv(index=False)
    out, warns = parse_weather(text.encode())
    assert len(out) == 200 and any("NASA POWER" in w for w in warns)


def test_parse_checks_requirements_and_tpi_link():
    w = synthetic_weather("lilongwe_like", years=2, seed=1)
    df, warns = parse_checks(_checks_csv(), w, -13.98)
    assert {"famacha", "need", "bcs", "dag", "farm", "tpi"} <= set(df.columns)
    assert df["tpi"].notna().all()
    with pytest.raises(DatasetError, match="famacha"):
        parse_checks(b"bcs,need\n" + b"3,0\n" * 40)
    with pytest.raises(DatasetError, match="need"):
        parse_checks(b"famacha\n" + b"3\n" * 40)
    with pytest.raises(DatasetError, match="at least 5"):
        parse_checks(b"famacha,need\n" + b"2,0\n" * 40)


def test_parse_checks_without_farm_warns():
    _, warns = parse_checks(_checks_csv(with_farm=False))
    assert any("No farm column" in w for w in warns)


# ---------------------------------------------------------------- registry
def test_registry_roundtrip(tmp_path):
    reg = Registry(tmp_path)
    assert len(reg.list("weather")) == 3  # builtins
    m = reg.add_weather_upload(_weather_csv(), "My site", lat=-13.9)
    assert reg.get(m.id).rows == m.rows and not m.synthetic
    assert len(reg.load(m.id)) == m.rows
    s = reg.add_synthetic("Custom", years=2, profile={"lat": 5.0, "tmean": [27] * 12, "rain_mm": [200] * 12})
    assert reg.get(s.id).synthetic
    c = reg.add_checks_upload(_checks_csv(), "Field", linked_weather=m.id)
    assert "tpi" in reg.get(c.id).columns
    reg.delete(c.id)
    with pytest.raises(DatasetError):
        reg.get(c.id)
    with pytest.raises(DatasetError, match="Built-in"):
        reg.delete("builtin-kano_like")
    # survives a reload from disk
    assert {x.id for x in Registry(tmp_path).list()} >= {m.id, s.id}


def test_registry_rejects_path_tricks(tmp_path):
    reg = Registry(tmp_path)
    with pytest.raises(DatasetError):
        reg.path("../../etc/passwd")


# ---------------------------------------------------------------- API
@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _wait(client, job, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.3)
    raise AssertionError("job timed out")


def test_index_and_info(client):
    assert client.get("/").status_code == 200
    assert "Chart" in client.get("/static/vendor/chart.umd.js").text[:2000]
    info = client.get("/api/info").json()
    assert set(info["policies"]) == {"none", "whole60", "famacha14", "fpc14", "gated"}


def test_upload_preview_download_delete(client):
    r = client.post("/api/datasets/weather", data={"name": "Up", "lat": "-13.9"},
                    files={"file": ("w.csv", _weather_csv(), "text/csv")})
    assert r.status_code == 200, r.text
    ds = r.json()
    assert not ds["synthetic"] and ds["deletable"]
    pv = client.get(f"/api/datasets/{ds['id']}/preview?n=5").json()
    assert pv["columns"][:2] == ["date", "tmean"] and len(pv["rows"]) == 5
    dl = client.get(f"/api/datasets/{ds['id']}/download")
    assert dl.headers["content-type"].startswith("text/csv") and dl.text.startswith("date,")
    assert client.delete(f"/api/datasets/{ds['id']}").status_code == 200


def test_upload_flags_synthetic(client):
    r = client.post("/api/datasets/weather", data={"name": "Fake", "lat": "1", "synthetic": "true"},
                    files={"file": ("w.csv", _weather_csv(), "text/csv")})
    assert r.json()["synthetic"] is True


def test_bad_upload_returns_readable_error(client):
    r = client.post("/api/datasets/weather", data={"lat": "1"}, files={"file": ("w.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 400 and "date" in r.json()["detail"].lower()


def test_synthetic_endpoint_and_climate(client):
    r = client.post("/api/datasets/synthetic", json={"name": "Hot", "site": "uyo_like", "years": 2, "warming_c": 2})
    assert r.status_code == 200, r.text
    c = client.get(f"/api/climate/{r.json()['id']}").json()
    assert len(c["monthly"]["tpi_mean"]) == 12 and c["summary"]["days"] > 700
    c2 = client.get("/api/climate/builtin-kano_like?warming=2").json()
    assert c2["warming"] == 2


def test_simulation_job_saves_checks_then_indicators_and_triage(client):
    job = client.post("/api/simulate", json={"weather_id": "builtin-uyo_like", "policies": ["none", "fpc14"],
                                             "replicates": 1, "n_farms": 10, "save_checks": True}).json()
    j = _wait(client, job)
    assert j["status"] == "done", j.get("error")
    res = j["result"]
    assert [s["key"] for s in res["summary"]] == ["none", "fpc14"]
    cid = res["saved_checks_id"]
    assert cid and client.get(f"/api/datasets/{cid}").json()["kind"] == "checks"

    ind = _wait(client, client.post("/api/indicators", json={"dataset_id": cid}).json())
    assert ind["status"] == "done", ind.get("error")
    assert any(r["indicator_set"] == "FAMACHA only" for r in ind["result"]["rows"])

    up = client.post("/api/datasets/checks", data={"name": "Field"},
                     files={"file": ("c.csv", _checks_csv(), "text/csv")}).json()
    tri = _wait(client, client.post("/api/triage", json={"train_id": cid, "test_ids": [up["id"]]}).json())
    assert tri["status"] == "done", tri.get("error")
    # only shared columns are used
    assert set(tri["result"]["features"]) <= {"famacha", "bcs", "dag"}
    assert len(tri["result"]["rows"]) == 2


def test_simulation_validation_error(client):
    j = _wait(client, client.post("/api/simulate", json={"weather_id": "builtin-uyo_like", "policies": []}).json())
    assert j["status"] == "error" and "policy" in j["error"]


def test_parse_semicolon_separated_weather():
    raw = _weather_csv(days=150).decode().replace(",", ";").encode()
    df, _ = parse_weather(raw)
    assert len(df) == 150
