"""Agent-based synthetic smallholder goat flocks with a Haemonchus-like parasite.

Each farm has one shared grazing pool (eggs -> faecal L3 -> herbage L3, driven
by daily weather through `transmission.daily_rates`). Each goat carries an
adult worm burden split into drug-susceptible and drug-resistant genotypes,
a packed cell volume (PCV), a latent body condition, and fixed susceptibility.

Observable indicators (what a farmer or community animal health worker could
record) are generated from the latent state with noise:
  FAMACHA (1-5) from PCV with scorer error; body condition score (BCS);
  bottle jaw; dag/scour; nasal discharge (deliberately uninformative here);
  faecal egg count (FEC, McMaster-style, costly).

All parameters are illustrative (docs/PARAMETERS.md). Outputs are SYNTHETIC.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .climate import SITES
from .transmission import daily_rates, transmission_potential

# FAMACHA categories by PCV (%), after van Wyk & Bath (2002):
# 1: >=28, 2: 23-27, 3: 18-22, 4: 13-17, 5: <=12
FAMACHA_EDGES = np.array([12.5, 17.5, 22.5, 27.5])
NEED_PCV = 17.5     # anaemic: true FAMACHA 4 or 5
NEED_WORMS = 2000.0  # illustrative burden likely to impair production before anaemia shows
# Ground truth "would benefit from treatment" = anaemic OR burden >= NEED_WORMS.


def famacha_from_pcv(pcv: np.ndarray) -> np.ndarray:
    return 5 - np.searchsorted(FAMACHA_EDGES, pcv, side="right")


@dataclass
class FlockParams:
    n_farms: int = 60
    goats_min: int = 3
    goats_max: int = 8
    kid_fraction: float = 0.35
    susceptibility_shape: float = 1.2      # gamma shape -> aggregation (smaller = more aggregated)
    intake_rate: float = 0.002             # fraction of herbage L3 pool ingested per goat per day
    establishment: float = 0.5
    immunity_adult: float = 0.6            # proportional reduction in establishment
    immunity_kid: float = 0.15
    worm_mortality: float = 0.02           # per day
    fecundity: float = 4000.0              # eggs per adult worm per day
    egg_to_pool: float = 2.5e-3            # fraction of eggs reaching the farm grazing pool (tuned, see docs)
    fecundity_dd: float = 3000.0           # burden at which per-worm fecundity halves (density dependence)
    faecal_g_per_day: float = 700.0
    pcv_base: float = 30.0
    pcv_recovery: float = 0.05             # per day
    blood_loss: float = 6e-4               # PCV points per worm per day (scaled by PCV/30)
    dry_season_pcv_penalty: float = 2.0
    death_hazard_low_pcv: float = 0.04     # daily death probability when PCV < 11
    replace_after_days: int = 45
    initial_resistant_fraction: float = 0.02
    pcv_base_sd: float = 2.0               # between-goat variation in healthy PCV
    acquired_immunity_max: float = 0.30    # extra establishment reduction from exposure
    acquired_immunity_scale: float = 15000.0  # cumulative ingested L3 for ~63% of the maximum
    immunity_cap: float = 0.9
    efficacy_susceptible: float = 0.98
    efficacy_resistant: float = 0.20
    famacha_error: float = 0.25            # prob. of scoring one category off
    initial_pool: float = 2000.0
    initial_worms: float = 300.0


@dataclass
class SimResult:
    daily: pd.DataFrame
    checks: pd.DataFrame
    summary: dict = field(default_factory=dict)


class Policy:
    """Base policy: decides check days and who to treat."""

    name = "base"
    check_minutes = 0.0

    def is_check_day(self, day: int, farm_offset: np.ndarray, band_today: np.ndarray) -> np.ndarray:
        return np.zeros_like(farm_offset, dtype=bool)

    def treat(self, obs: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(obs), dtype=bool)

    def whole_flock_today(self, day: int) -> bool:
        return False


def _dryness(weather: pd.DataFrame) -> np.ndarray:
    """0..1 seasonal forage scarcity proxy from trailing 60-day rain."""
    r60 = weather["rain"].rolling(60, min_periods=1).sum().values
    return np.clip(1 - r60 / 150.0, 0, 1)


def simulate(
    weather: pd.DataFrame,
    policy: Policy,
    params: FlockParams | None = None,
    seed: int = 0,
    lat: float | None = None,
    burn_in_days: int = 180,
) -> SimResult:
    p = params or FlockParams()
    rng = np.random.default_rng(seed)
    site = weather["site"].iloc[0]
    lat = lat if lat is not None else SITES.get(site, SITES["lilongwe_like"]).lat

    rates = daily_rates(weather, lat)
    tpi = transmission_potential(weather, lat).values
    from .transmission import risk_band

    band = risk_band(tpi)
    dry = _dryness(weather)
    n_days = len(weather)

    # ---- population ----------------------------------------------------------
    sizes = rng.integers(p.goats_min, p.goats_max + 1, p.n_farms)
    farm = np.repeat(np.arange(p.n_farms), sizes)
    n = len(farm)
    kid = rng.random(n) < p.kid_fraction
    susc = rng.gamma(p.susceptibility_shape, 1 / p.susceptibility_shape, n)
    imm0 = np.where(kid, p.immunity_kid, p.immunity_adult)
    cum_l3 = np.zeros(n)
    pcv_own = p.pcv_base + rng.normal(0, p.pcv_base_sd, n)
    wr = np.full(n, p.initial_worms * p.initial_resistant_fraction)
    ws = np.full(n, p.initial_worms * (1 - p.initial_resistant_fraction))
    pcv = pcv_own + rng.normal(0, 1.0, n)
    bcs = np.full(n, 3.0) + rng.normal(0, 0.3, n)
    alive = np.ones(n, bool)
    dead_since = np.full(n, -1)
    last_treat = np.full(n, -10_000)
    low_streak = np.zeros(n, int)

    # farm pools by genotype: eggs E, faecal L3 F, herbage L3 H  (shape: farms x 2)
    E = np.zeros((p.n_farms, 2))
    F = np.zeros((p.n_farms, 2))
    H = np.zeros((p.n_farms, 2))
    H[:, 0] = p.initial_pool * (1 - p.initial_resistant_fraction)
    H[:, 1] = p.initial_pool * p.initial_resistant_fraction

    farm_offset = rng.integers(0, 14, p.n_farms)
    goats_per_farm = np.bincount(farm, minlength=p.n_farms)

    daily_rows, check_rows = [], []
    deaths = treatments = checks_done = 0
    goat_days = 0
    anaemic_days = 0

    for d in range(n_days):
        r = rates.iloc[d]
        recording = d >= burn_in_days

        # ---- pasture dynamics ----
        dev = r.dev
        newF = E * dev
        E = E * np.clip(1 - dev - r.mu_e, 0, 1)
        mig = F * r.mig
        F = F * np.clip(1 - r.mig - r.mu_f, 0, 1) + newF
        H = H * np.clip(1 - r.mu_h, 0, 1) + mig

        # ---- infection ----
        alive_per_farm = np.bincount(farm[alive], minlength=p.n_farms)
        frac_eaten = np.clip(p.intake_rate * alive_per_farm, 0, 0.9)
        ingested_pool = H * frac_eaten[:, None]
        H = H - ingested_pool
        share = np.where(alive, susc, 0.0)
        share_sum = np.bincount(farm, weights=share, minlength=p.n_farms)
        w_goat = np.where(share_sum[farm] > 0, share / np.maximum(share_sum[farm], 1e-9), 0)
        imm = np.minimum(imm0 + p.acquired_immunity_max * (1 - np.exp(-cum_l3 / p.acquired_immunity_scale)), p.immunity_cap)
        est = p.establishment * (1 - imm) * alive
        in_s = ingested_pool[farm, 0] * w_goat
        in_r = ingested_pool[farm, 1] * w_goat
        cum_l3 += in_s + in_r
        ws += in_s * est
        wr += in_r * est
        ws *= 1 - p.worm_mortality
        wr *= 1 - p.worm_mortality

        # ---- egg output to pasture ----
        dd = 1.0 / (1.0 + (ws + wr) / p.fecundity_dd)
        eggs_s = np.bincount(farm, weights=ws * dd * alive, minlength=p.n_farms) * p.fecundity * p.egg_to_pool
        eggs_r = np.bincount(farm, weights=wr * dd * alive, minlength=p.n_farms) * p.fecundity * p.egg_to_pool
        E[:, 0] += eggs_s
        E[:, 1] += eggs_r

        # ---- host physiology ----
        w = ws + wr
        base = pcv_own - p.dry_season_pcv_penalty * dry[d]
        pcv = pcv + p.pcv_recovery * (base - pcv) - p.blood_loss * w * (pcv / 30.0) + rng.normal(0, 0.3, n)
        bcs_target = 3.3 - 0.8 * dry[d] - 0.00015 * w
        bcs = bcs + 0.02 * (bcs_target - bcs)

        low = alive & (pcv < 11)
        die = low & (rng.random(n) < p.death_hazard_low_pcv)
        if recording:
            deaths += int(die.sum())
        alive[die] = False
        dead_since[die] = d
        # replacement with an uninfected kid after a delay (keeps flock size stable)
        rep = (~alive) & (dead_since >= 0) & (d - dead_since >= p.replace_after_days)
        if rep.any():
            k = rep.sum()
            alive[rep] = True
            kid[rep] = True
            imm0[rep] = p.immunity_kid
            cum_l3[rep] = 0.0
            pcv_own[rep] = p.pcv_base + rng.normal(0, p.pcv_base_sd, k)
            susc[rep] = rng.gamma(p.susceptibility_shape, 1 / p.susceptibility_shape, k)
            ws[rep] = wr[rep] = 0.0
            pcv[rep] = pcv_own[rep]
            bcs[rep] = 3.0
            dead_since[rep] = -1

        need = alive & (pcv < NEED_PCV)
        if recording:
            goat_days += int(alive.sum())
            anaemic_days += int(need.sum())

        # ---- management ----
        treat_mask = np.zeros(n, bool)
        dm = d - burn_in_days  # management starts after burn-in
        if recording and policy.whole_flock_today(dm):
            treat_mask = alive.copy()
        farm_check = policy.is_check_day(dm, farm_offset, np.full(p.n_farms, band[d]))
        goat_check = alive & farm_check[farm] & recording
        if goat_check.any():
            idx = np.flatnonzero(goat_check)
            obs = observe(idx, pcv, bcs, w, kid, dry[d], tpi[d], d, last_treat, rng, p)
            decide = policy.treat(obs)
            treat_mask[idx[decide]] = True
            if recording:
                checks_done += len(idx)
                obs["treated"] = decide
                obs["farm"] = farm[idx]
                obs["day"] = d
                check_rows.append(obs)
        if treat_mask.any():
            ws[treat_mask] *= 1 - p.efficacy_susceptible
            wr[treat_mask] *= 1 - p.efficacy_resistant
            last_treat[treat_mask] = d
            if recording:
                treatments += int(treat_mask.sum())

        if recording:
            tot_w = (ws + wr)[alive]
            daily_rows.append(
                (
                    weather["date"].iloc[d], tpi[d], band[d],
                    float(H.sum()), float(H[:, 1].sum() / max(H.sum(), 1e-9)),
                    float(tot_w.mean()) if tot_w.size else 0.0,
                    float(pcv[alive].mean()), float(need.sum() / max(alive.sum(), 1)),
                    float(wr[alive].sum() / max((ws + wr)[alive].sum(), 1e-9)),
                )
            )

    daily = pd.DataFrame(
        daily_rows,
        columns=["date", "tpi", "band", "herbage_l3", "pasture_resistant_frac",
                 "mean_worms", "mean_pcv", "frac_need", "worm_resistant_frac"],
    )
    checks = pd.concat(check_rows, ignore_index=True) if check_rows else pd.DataFrame()
    goat_years = goat_days / 365.25
    summary = {
        "policy": policy.name,
        "site": site,
        "seed": seed,
        "goats": int(n),
        "goat_years": round(goat_years, 1),
        "deaths_per_100_goat_years": 100 * deaths / max(goat_years, 1e-9),
        "treatments_per_goat_year": treatments / max(goat_years, 1e-9),
        "checks_per_goat_year": checks_done / max(goat_years, 1e-9),
        "check_minutes_per_goat_year": checks_done * policy.check_minutes / max(goat_years, 1e-9),
        "pct_goat_days_anaemic": 100 * anaemic_days / max(goat_days, 1),
        "final_resistant_frac": float(daily["worm_resistant_frac"].iloc[-30:].mean()) if len(daily) else np.nan,
    }
    return SimResult(daily=daily, checks=checks, summary=summary)


def observe(idx, pcv, bcs, worms, kid, dryness, tpi_today, day, last_treat, rng, p: FlockParams) -> pd.DataFrame:
    """Generate the noisy indicators a person in the field could record."""
    k = len(idx)
    true_f = famacha_from_pcv(pcv[idx])
    err = rng.random(k) < p.famacha_error
    step = np.where(rng.random(k) < 0.5, -1, 1)
    famacha = np.clip(true_f + err * step, 1, 5)
    bcs_obs = np.clip(np.round((bcs[idx] + rng.normal(0, 0.35, k)) * 2) / 2, 1, 5)
    pj = np.where(pcv[idx] < 12, 0.6, np.where(pcv[idx] < 15, 0.2, 0.01))
    bottle_jaw = rng.random(k) < pj
    dag_p = 0.05 + 0.10 * (1 - dryness) + 0.15 * (worms[idx] > 3000)
    dag = rng.random(k) < dag_p
    nasal = rng.random(k) < (0.08 + 0.06 * dryness)
    true_epg = worms[idx] * p.fecundity / p.faecal_g_per_day
    sampled = true_epg * rng.gamma(2.0, 0.5, k)
    fec = rng.poisson(sampled / 50.0) * 50
    return pd.DataFrame(
        {
            "goat": idx,
            "famacha": famacha.astype(int),
            "bcs": bcs_obs,
            "bottle_jaw": bottle_jaw.astype(int),
            "dag": dag.astype(int),
            "nasal": nasal.astype(int),
            "fec": fec.astype(int),
            "kid": kid[idx].astype(int),
            "tpi": float(tpi_today),
            "days_since_treat": np.clip(day - last_treat[idx], 0, 365),
            "need": ((pcv[idx] < NEED_PCV) | (worms[idx] >= NEED_WORMS)).astype(int),
            "anaemic": (pcv[idx] < NEED_PCV).astype(int),
            "true_pcv": pcv[idx].round(1),
        }
    )
