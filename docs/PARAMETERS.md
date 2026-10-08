# Parameters and their status

Status key:

- **A**: anchored to a published source.
- **I**: illustrative, chosen for plausible qualitative behaviour.
- **T**: tuned so the simulated disease burden lands in a plausible range.

Everything marked I or T should be replaced or estimated from data before any
substantive claim is made.

## Free-living stages (`tstsim/transmission.py`)

| Parameter | Value | Status | Note |
|---|---|---|---|
| Development threshold | 10 °C | I | Real *H. contortus* thresholds must come from GLOWORM-FL / experimental data |
| Development slope | 0.01 day⁻¹ °C⁻¹ | I | Gives about 6–7 days egg to L3 at 25 °C |
| Development decline | 35–40 °C | I | Direction informed by Rose Vineer et al. (2016, *Vet Parasitol* 229), which reported lower development success at 37 °C than the model predicted |
| Moisture factor | 7-day rain / Hargreaves PET, saturating at 1 | I | Structure follows the idea that moisture gates development and migration; the threshold is not published |
| Egg, faecal-L3 and herbage-L3 mortality | quadratic or power in temperature, plus a dryness term | I | |
| PET and Ra | FAO-56 equations 21 and 52 | A | Allen et al. (1998) |

## Hosts and parasites (`tstsim/flock.py`, `FlockParams`)

| Parameter | Value | Status | Note |
|---|---|---|---|
| Goats per farm | 3–8 | A (approx.) | Malawi smallholdings average roughly 3–6 goats (Airs et al. 2026, citing Airs et al. 2023a; Kaumbata et al. 2020) |
| Kid fraction | 0.35 | I | |
| Susceptibility | gamma, shape 1.2 | I | Produces the aggregated "few goats carry most worms" pattern |
| Intake rate | 0.002 of herbage pool per goat per day | T | |
| Establishment | 0.5 × (1 − immunity) | I | |
| Innate immunity | adult 0.6, kid 0.15 | I | |
| Acquired immunity | up to +0.30, scale 15,000 L3 | T | Added so year-round-wet sites do not collapse |
| Adult worm mortality | 0.02 day⁻¹ | I | Mean lifespan of about 50 days |
| Fecundity | 4,000 eggs/worm/day, halving at 3,000 worms | I | |
| Eggs reaching the grazing pool | 0.0025 | T | Main transmission dial (see tuning below) |
| PCV | 30 ± 2; recovery 0.05 day⁻¹; loss 6×10⁻⁴ per worm-day | I | |
| Death hazard | 4% per day when PCV < 11 | I | |
| Initial resistant share | 0.02 | I | |
| Drug efficacy | 98% (susceptible), 20% (resistant) | I | |
| FAMACHA bands | ≥28, 23–27, 18–22, 13–17, ≤12 | A | van Wyk & Bath (2002) |
| FAMACHA scorer error | 25% one category off | I | |
| "Needs treatment" | PCV < 17.5 or ≥ 2,000 worms | I | Burden threshold is illustrative |
| FEC | McMaster-style, 50 epg detection limit, gamma-Poisson | I | |

## Effort (`tstsim/policies.py`, `analysis.py`)

| Check | Minutes per goat | Status |
|---|---|---|
| FAMACHA | 1.0 | I |
| Body condition score | 0.5 | I |
| Bottle jaw | 0.3 | I |
| Dag | 0.3 | I |
| Nasal discharge | 0.2 | I |
| FEC (lab, incl. sampling and transport) | 20 equivalent | I |

## Tuning record

The transmission dial was first set at 2×10⁻⁴. At that value the parasite
went extinct because each worm replaced itself less than once.

A grid over two settings (eggs reaching the pool: 2.0 or 2.5×10⁻³; maximum
acquired immunity: 0.2 or 0.3) was then run on one weather seed. It was
scored on untreated deaths and the FAMACHA-TST treatment rate at all three
sites. The final values (2.5×10⁻³ and 0.30) produce:

- moderate seasonal disease at the Malawi-like site;
- high challenge at the humid site;
- low challenge at the short-season site.

This tuning targets plausibility, not fit to data.
