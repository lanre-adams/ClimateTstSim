import numpy as np
import pandas as pd
import pytest

from tstsim.analysis import FEATURE_SETS, collect_checks, feature_cost, indicator_study, sens_at_spec
from tstsim.climate import SITES, load_nasa_power_csv, synthetic_weather
from tstsim.flock import FlockParams, famacha_from_pcv, simulate
from tstsim.policies import ClimateGatedFPC, FamachaTST, FPCTST, NoTreatment, WholeFlockInterval, fpc_rule
from tstsim.transmission import (
    development_rate, extraterrestrial_radiation, hargreaves_pet, moisture_factor,
    risk_band, transmission_potential,
)

SMALL = FlockParams(n_farms=8)


# ---------------- climate ----------------
def test_synthetic_weather_shape_and_flags():
    w = synthetic_weather("lilongwe_like", years=1, seed=1)
    assert len(w) == 365 and w["synthetic"].all()
    assert (w.rain >= 0).all() and (w.tmax > w.tmin).all()


def test_seasonality_follows_profile():
    w = synthetic_weather("lilongwe_like", years=3, seed=2)
    by_m = w.groupby(w.date.dt.month).rain.mean()
    assert by_m.loc[[1, 2, 12]].mean() > 10 * by_m.loc[[6, 7, 8]].mean()


def test_warming_offset_is_exact():
    a = synthetic_weather("kano_like", years=1, seed=3)
    b = synthetic_weather("kano_like", years=1, seed=3, warming_c=2.0)
    assert np.allclose(b.tmean - a.tmean, 2.0, atol=0.011)
    assert np.allclose(a.rain, b.rain)


def test_nasa_power_loader(tmp_path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        "-BEGIN HEADER-\nNASA/POWER sample\n-END HEADER-\n"
        "YEAR,MO,DY,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR\n"
        "2020,1,1,24.1,30.2,18.0,5.2\n2020,1,2,-999,31.0,19.0,-999\n2020,1,3,23.9,29.5,18.4,0.0\n"
    )
    df = load_nasa_power_csv(str(csv), site="test")
    assert list(df.columns[:5]) == ["date", "tmean", "tmax", "tmin", "rain"]
    assert not df.isna().any().any()
    assert df.rain.iloc[1] == 0.0 and abs(df.tmean.iloc[1] - 24.0) < 1e-9
    assert not df.synthetic.any()


# ---------------- transmission ----------------
def test_development_rate_thresholds():
    assert development_rate(np.array([9.0]))[0] == 0
    assert development_rate(np.array([40.0]))[0] == 0
    assert development_rate(np.array([25.0]))[0] == pytest.approx(0.15)


def test_ra_and_pet_reasonable():
    ra = extraterrestrial_radiation(np.array([1, 182]), 0.0)
    assert 30 < ra.mean() < 40  # equatorial Ra ~ 35-38 MJ/m2/day
    w = synthetic_weather("uyo_like", years=1, seed=1)
    pet = hargreaves_pet(w, SITES["uyo_like"].lat)
    assert 2 < pet.mean() < 7


def test_moisture_bounded_and_dry_season_low():
    w = synthetic_weather("kano_like", years=1, seed=1)
    m = moisture_factor(w, SITES["kano_like"].lat)
    assert m.min() >= 0 and m.max() <= 1
    assert m[w.date.dt.month.isin([1, 2]).values].mean() < 0.05


def test_tpi_reference_and_season():
    w = synthetic_weather("kano_like", years=2, seed=5)
    tpi = transmission_potential(w, SITES["kano_like"].lat)
    by_m = tpi.groupby(tpi.index.month).mean()
    assert by_m.loc[8] > 5 * by_m.loc[2]
    assert tpi.max() < 2.0


def test_risk_band():
    assert list(risk_band(np.array([0.0, 0.2, 0.9]))) == [0, 1, 2]


# ---------------- flock ----------------
def test_famacha_mapping():
    assert list(famacha_from_pcv(np.array([30, 25, 20, 15, 10]))) == [1, 2, 3, 4, 5]
    assert famacha_from_pcv(np.array([17.6]))[0] == 3
    assert famacha_from_pcv(np.array([17.4]))[0] == 4


def test_simulation_reproducible():
    w = synthetic_weather("lilongwe_like", years=1.5, seed=1)
    a = simulate(w, FPCTST(), params=SMALL, seed=4)
    b = simulate(w, FPCTST(), params=SMALL, seed=4)
    assert a.summary == b.summary


def test_no_management_during_burn_in():
    w = synthetic_weather("lilongwe_like", years=1.5, seed=1)
    r = simulate(w, FPCTST(), params=SMALL, seed=4, burn_in_days=180)
    assert r.checks.day.min() >= 180


def test_no_treatment_never_selects_resistance():
    w = synthetic_weather("uyo_like", years=1.5, seed=1)
    r = simulate(w, NoTreatment(), params=SMALL, seed=2)
    assert r.summary["treatments_per_goat_year"] == 0
    assert r.summary["final_resistant_frac"] == pytest.approx(SMALL.initial_resistant_fraction, abs=1e-6)


def test_whole_flock_selects_resistance_fastest():
    w = synthetic_weather("uyo_like", years=2.5, seed=1)
    wf = simulate(w, WholeFlockInterval(), params=SMALL, seed=2).summary
    tst = simulate(w, FamachaTST(), params=SMALL, seed=2).summary
    assert wf["final_resistant_frac"] > tst["final_resistant_frac"]
    assert wf["treatments_per_goat_year"] > tst["treatments_per_goat_year"]


def test_treatment_reduces_deaths_in_high_challenge():
    w = synthetic_weather("uyo_like", years=2.5, seed=1)
    none = simulate(w, NoTreatment(), params=SMALL, seed=2).summary
    tst = simulate(w, FPCTST(), params=SMALL, seed=2).summary
    assert tst["deaths_per_100_goat_years"] < none["deaths_per_100_goat_years"]


def test_climate_gating_reduces_checks():
    w = synthetic_weather("kano_like", years=2, seed=1)
    full = simulate(w, FPCTST(), params=SMALL, seed=2).summary
    gated = simulate(w, ClimateGatedFPC(), params=SMALL, seed=2).summary
    assert gated["checks_per_goat_year"] < 0.8 * full["checks_per_goat_year"]


def test_observation_columns_and_ranges():
    w = synthetic_weather("uyo_like", years=1.5, seed=1)
    c = simulate(w, FPCTST(), params=SMALL, seed=2).checks
    for col in ["famacha", "bcs", "bottle_jaw", "dag", "nasal", "fec", "tpi", "need", "anaemic"]:
        assert col in c
    assert c.famacha.between(1, 5).all() and c.bcs.between(1, 5).all()
    assert (c.fec % 50 == 0).all()
    assert (c.need >= c.anaemic).all()  # anaemic implies need


# ---------------- policies & analysis ----------------
def test_fpc_rule():
    obs = pd.DataFrame({"famacha": [2, 4, 3, 3, 1], "bcs": [3, 3, 2, 3, 1.5], "bottle_jaw": [0, 0, 0, 1, 0]})
    assert list(fpc_rule(obs)) == [False, True, True, True, True]


def test_feature_cost():
    assert feature_cost(["famacha", "bcs"]) == pytest.approx(1.5)
    assert feature_cost(["tpi"]) == 0
    assert feature_cost(["log_fec"]) == pytest.approx(20)


def test_sens_at_spec():
    y = np.array([0, 0, 0, 0, 1, 1])
    p = np.array([0.1, 0.2, 0.3, 0.4, 0.9, 0.25])  # threshold at 75% spec = 0.3
    assert sens_at_spec(y, p, 0.75) == pytest.approx(0.5)


def test_indicator_study_ranks_famacha_above_noise():
    df = collect_checks("uyo_like", [0], years=1.5)
    ind = indicator_study(df, n_splits=3).set_index("indicator_set")
    assert ind.loc["FAMACHA only", "auroc"] > ind.loc["Nasal discharge only", "auroc"] + 0.2
    assert set(ind.index) == set(FEATURE_SETS)
