# climate-tst-sim

**Which goat-health checks, how often, and when?**
A climate-aware simulation of targeted selective treatment (TST) against
gastrointestinal nematodes on smallholder goat farms.

> **Everything here is synthetic.** The weather comes from a generator, the
> farms and goats are simulated, and the parameters are illustrative values
> chosen to give plausible qualitative behaviour. They are **not** the
> published GLOWORM-FL or Q0 parameterisations. This is a research
> demonstrator for thinking about study design. It is **not** an advisory tool
> and must not be used to make treatment decisions for real animals.

## Why this exists

Worms such as *Haemonchus contortus* are a major cause of goat illness and
death on African smallholdings, and resistance to the main dewormers is
spreading. Two practical ideas address both problems:

1. **Targeted selective treatment.** Check each goat (for example with the
   FAMACHA eye-colour card or a five-point check) and treat only those that
   need it. Untreated worms stay susceptible (the *refugia* principle).
2. **Climate-driven risk forecasting.** Larvae develop and survive on pasture
   only under suitable temperature and moisture, so weather can indicate when
   to watch closely.

This repository asks how those two ideas fit together. It treats the
questions as a modelling and evaluation problem that real field data could
later answer:

- Which **indicators** are worth their effort?
- Does a **climate index** let farmers or animal health workers check less
  often without missing sick goats?
- Do **decision rules** learned on one region **transfer** to another climate?

## What it does

| Module | What it contains |
|---|---|
| `tstsim/climate.py` | Synthetic daily weather for three site *profiles* (central Malawi-like, northern Nigeria-like, south-east Nigeria-like), plus a loader for real NASA POWER CSV files. |
| `tstsim/transmission.py` | A Q0/GLOWORM-style chain (egg to L3 development, migration onto herbage, larval survival), driven by temperature and a rain/PET moisture factor (Hargreaves PET, FAO-56). It produces a daily **transmission potential index** and 3-level risk band. |
| `tstsim/flock.py` | An agent-based simulation of 60 farms with 3–8 goats each. Each goat has worm burdens split into drug-susceptible and drug-resistant genotypes, aggregated susceptibility, age and exposure-dependent immunity, packed cell volume (PCV) and body condition. Observable indicators are generated with noise: FAMACHA with scorer error, BCS, bottle jaw, dag, nasal discharge, and McMaster-style FEC. |
| `tstsim/policies.py` | The management policies compared (see below). |
| `tstsim/analysis.py` | The three experiments: indicator value-for-effort (farm-grouped CV), cross-climate shift test, and policy comparison over replicates. |
| `tstsim/mcda.py` | Multi-criteria trade-off analysis of policy outcomes: four normalisation methods, Pareto dominance, robustness to weights (4,000 random weightings), hard limits, and an argument-style "why A over B, and what would reverse it" explanation. |
| `tstsim/datasets.py` | Parsing, validation and storage of datasets you add (weather and check records). |
| `tstsim/webapp/` | The local web interface: FastAPI server, background jobs, downloadable reports (`reports.py`), and a no-build HTML/JS front end with Chart.js (vendored, MIT). |

The management policies are:

- No treatment
- Whole-flock treatment every 60 days
- FAMACHA-only TST every 14 days
- Five-point-style TST every 14 days
- Five-point-style TST on a climate-gated schedule (every 14 days when risk is moderate or high, every 28 days when low)
- A logistic-regression triage model using cheap checks plus the climate index

**Ground truth** for "would benefit from treatment" is: anaemic (true PCV
below 17.5, i.e. FAMACHA 4–5) **or** a worm burden of at least 2,000, an
illustrative production-impairing load that may not yet show as anaemia.

## Results (synthetic; full run, `make results`)

### Policy outcomes, Malawi-like site

Ten replicate simulations, about 3.5 years each after a 180-day burn-in.
Values are means, with 5th–95th percentiles in brackets. Labour is checking
time plus about 2 minutes per dose.

| Policy | Deaths /100 goat-yr | Anaemic goat-days | Doses /goat-yr | Labour min /goat-yr | Resistant share at end |
|---|---|---|---|---|---|
| No treatment | 10.0 (6.2–15.0) | 3.8% | 0 | 0 | 0.02 |
| Whole flock / 60 d | 4.2 (1.8–7.5) | 1.6% | 6.0 | 12 | 1.00 |
| FAMACHA only, 14 d | 1.9 (0.8–3.8) | 1.9% | 0.70 | 27 | 0.77 (0.65–0.85) |
| 5-point-style, 14 d | 2.1 (0.8–3.8) | 1.8% | 1.18 | 62 | 0.88 |
| 5-point-style, climate-gated | 2.1 (0.8–3.8) | 2.0% | 0.95 | 45 | 0.83 |
| Model triage, climate-gated | 2.0 (0.7–3.7) | 1.4% | 0.88 | 45 | 0.82 |

### Multi-criteria view (new in v0.3.0)

Each policy is treated as an option and each outcome as a separate objective:
deaths (animal health), anaemic goat-days (welfare), doses (drug use),
labour (cost to the farmer) and resistance (long-term sustainability).

| Scaling method | First with equal weights, all six policies | First when no policy may have more deaths than whole-flock treatment |
|---|---|---|
| Min–max | FAMACHA only | Model triage |
| Reference (whole flock) | No treatment | FAMACHA only |
| Target | FAMACHA only | FAMACHA only |
| Rank | FAMACHA only | FAMACHA only |

- **Four of six policies are Pareto-optimal.** Both five-point-style policies
  are dominated (by the model triage rule, and the climate-gated one also by
  FAMACHA only). That partly reflects how weak the extra checks are by
  construction here (see point 3 below).
- **The scaling method changes the winner.** Under reference scaling,
  "No treatment" wins in 78% of random weightings, despite five times the
  deaths of FAMACHA-only. At the SE Nigeria-like site it ranks first under
  three of four scalings with equal weights.
- **A hard limit changes the question.** Ruling out any policy with more
  deaths than whole-flock treatment removes "No treatment". Under min–max the
  winner then flips from FAMACHA-only to the model triage rule, because
  min–max scores depend on which options are in the comparison set
  (rank reversal).
- **Explanations are checkable.** With the limit and equal weights, the
  model triage rule beats FAMACHA-only mainly on anaemic goat-days; the
  ranking reverses if labour carries at least 28% of the weight.

### What the simulator says, given its assumptions

1. **TST beats whole-flock treatment.** Every TST variant had fewer deaths
   than whole-flock dosing, using 80–90% fewer doses. TST slowed resistance
   but did **not** stop it within 3.5 years.
2. **Climate gating saves effort only where there is a dry season.** It cut
   checking effort by about 28% (60 to 43 minutes per goat-year) at the
   Malawi-like site, with no rise in deaths. At the year-round-wet SE
   Nigeria-like site it saved only about 9%, because risk is high most of
   the year.
3. **Extra checks beyond FAMACHA added little discrimination here.** Treat
   this as an artefact, not a finding: in this simulator "need" is driven
   mainly by anaemia, and dag and nasal discharge are weak by construction.
   Whether the extra checks pay their way can only be settled with real
   five-point-check records. That is the question this framework is built
   to ask of real data.
4. **The triage rule did not transfer cleanly between climates.** The
   threshold was chosen to reach about 90% sensitivity on Malawi-like farms.
   - Northern Nigeria-like site: sensitivity fell to 0.78.
   - SE Nigeria-like site: the model under-predicted prevalence (0.14
     predicted vs 0.16 observed), and as a policy it gave more doses than the
     rule-based check (5.0 vs 4.4 per goat-year).

   Local recalibration is needed. How to do that with few labels is a
   research question in its own right.
5. **High-challenge humid sites need more than TST.** At the SE
   Nigeria-like site, TST roughly halved deaths compared with whole-flock
   treatment (about 20 vs 42 per 100 goat-years). Deaths stayed high and
   resistance still approached fixation, consistent with calls for a
   "basket of options" (nutrition, browse, botanical supplementation).

Figures are in `outputs/`:

- `fig1` — transmission index by site
- `fig2` — indicator value for effort
- `fig3` / `fig3b` — policy trade-offs
- `fig4` — resistance over time
- `fig5` — shift test
- `fig6` / `fig6b` — multi-criteria view (scaled outcomes, first-place share by scaling method)

All numbers are in `outputs/results.json` and the CSVs. `docs/PARAMETERS.md`
lists every parameter and its status.

## Run it

### Web interface (recommended)

With Docker Desktop running, from this folder:

```bash
docker compose up --build
```

Then open **http://localhost:8000**. The first build takes a few minutes and
runs the tests. Stop it with Ctrl+C.

- Datasets you add are saved in `data/user/` on your computer, so they survive restarts.
- The Overview page shows batch results from `outputs/`, if you have run `scripts/run_all.py`.

Without Docker:

```bash
pip install -r requirements.txt
python -m tstsim.webapp          # then open http://localhost:8000
```

The interface has five pages:

| Page | What you can do |
|---|---|
| Overview | What the tool is, where to start, and the last batch results |
| Datasets | Upload daily weather; upload goat check records; create synthetic sites from presets or your own monthly temperature and rainfall; preview, download and delete |
| Climate | Compare up to four weather datasets: monthly and weekly transmission index, rainfall, temperature, and a warming slider |
| Simulate | Run the treatment policies on any weather dataset, with replicates and advanced parameters. Then weigh the trade-offs: choose a scaling method, set how much each outcome matters, optionally rule out policies with more deaths than current practice, and ask why one policy ranks above another. Optionally save the simulated check records as a new dataset |
| Checks and triage | Score which indicator sets are worth their effort on any check records, and test whether a triage rule trained on one dataset transfers to others |

**Reports.** Every analysis can be downloaded: an HTML report (tables,
charts, settings and, for simulations, the trade-off analysis with your
chosen scaling, weights and limit), the results table as CSV, or everything
as JSON. Open the HTML report in a browser and use Print to save it as PDF.

It is a local tool with no login. The compose file binds it to this computer
only; do not expose it to the internet. API documentation is at
`/api/docs`.

### Adding data

**Weather.** You can upload either of these:

- a NASA POWER daily point CSV, as downloaded (T2M, T2M_MAX, T2M_MIN,
  PRECTOTCORR);
- any CSV with a `date` column plus temperature and rainfall columns.
  Common names are recognised: `tmean`, `tmax`, `tmin`, `rain`, `T2M`,
  `precip`, `prcp`.

Rules and handling:

- You must enter the site's latitude, because day length enters the
  evaporation estimate.
- At least 120 days are needed.
- Missing days are filled and implausible values are treated as missing.
  Each fix is listed as a note on the dataset.

**Check records.** One row per goat per check.

- **Required:** `famacha` (1–5) and `need` (1 = needed treatment, 0 = did
  not).
- **Optional:** `bcs`, `bottle_jaw`, `dag`, `nasal`, `fec`, `farm`, `goat`,
  `date`, `kid`.

Notes:

- Include `farm` so cross-validation keeps each farm's goats together.
- If you link a weather dataset and the records have dates, the climate
  index for each check date is added automatically.
- Record how you defined `need` in the notes.

Templates for both are downloadable from the Datasets page. Tick "synthetic"
when uploading files that are not real observations, so they are labelled
correctly everywhere.

### Batch experiments and tests

```bash
make install      # all dependencies incl. pytest
make test         # 45 tests, ~20 s
make quick        # smoke run of the batch experiments, ~1 min
make results      # full batch run, ~5 min on 2 vCPU; writes outputs/
make docker-batch # the full batch run inside Docker
```

### Real weather from the command line

The scripts below have not been run here because the build environment has
no network access. The loader is tested against the NASA POWER CSV format.

```bash
python scripts/fetch_nasa_power.py --lat -13.98 --lon 33.78 --start 2015 --end 2024 --out data/real/lilongwe.csv
python scripts/real_weather_tpi.py data/real/lilongwe.csv --lat -13.98
```

## Changes in v0.3.0

- **Labour now includes dosing time** (about 2 minutes per dose). Before,
  whole-flock treatment showed zero labour, which flattered it.
- **Anaemic goat-days** is reported alongside deaths as a welfare outcome.
- **Fixes:** replacement kids now start with no treatment history (they
  inherited the dead goat's), and faecal egg counts use the same density
  dependence as egg output to pasture.
- **New:** multi-criteria trade-off module, Trade-offs panel in the web app,
  downloadable HTML/CSV/JSON reports, and figure 6. Eight new tests.

## Honest limits

- **Illustrative parameters.** The free-living stage parameters are
  placeholders. The next step is to replace them with published GLOWORM-FL
  values for *H. contortus*, and to check the index against observed
  seasonality.
- **Simplified biology.**
  - One parasite species.
  - No arrested development (hypobiosis).
  - No pre-patent delay.
  - A haploid-like resistance phenotype.
  - No goat movement or trade between farms.
- **Partly circular evaluation.** Indicators are generated from the same
  latent state that defines "need", so absolute AUROCs are optimistic. The
  *design* of the evaluation transfers to real data (farm-grouped CV, cost
  per check, shift tests); the numbers do not.
- **Rough effort costs.** Check times (minutes per goat) are rough guesses
  for illustration.
- **No real farmer, field or app work.** Practical constraints matter: for
  example, a recent Malawi follow-up study reported that 39% of survey
  respondents lacked access to a mobile phone. Any app would likely need to be designed for
  animal health workers or veterinary assistants rather than only farm
  owners.

## Key background (verified sources)

- Rose H, Wang T, van Dijk J, Morgan ER (2015) GLOWORM-FL: a simulation model of the effects of climate and climate change on the free-living stages of gastro-intestinal nematode parasites of ruminants. *Ecological Modelling* 297: 232–245.
- Rose H, Caminade C, Bolajoko MB, et al., Morgan ER (2016) Climate-driven changes to the spatio-temporal distribution of the parasitic nematode *Haemonchus contortus* in sheep in Europe. *Global Change Biology*.
- Bolajoko MB, Rose H, Musella V, et al., Morgan ER (2015) The basic reproduction quotient (Q0) as a potential spatial predictor of the seasonality of ovine haemonchosis. *Geospatial Health*. doi:10.4081/gh.2015.356
- Airs PM, Tinsley JHI, Mvula W, Takahashi T, van Wyk JA, Morgan ER, Safalaoh A (2026) Five Point Check©-based management of goat health can be self-sustainable without long-term public funding: a 5-year retrospective study of Malawi smallholdings. *Small Ruminant Research* 255: 107678. doi:10.1016/j.smallrumres.2025.107678
- van Wyk JA, Bath GF (2002) The FAMACHA system for managing haemonchosis in sheep and goats by clinically identifying individual animals for treatment. *Veterinary Research* 33: 509–529.
- Bath GF, van Wyk JA (2009) The Five Point Check© for targeted selective treatment of internal parasites in small ruminants. *Small Ruminant Research* 86: 6–13.
- Allen RG, Pereira LS, Raes D, Smith M (1998) Crop evapotranspiration. FAO Irrigation and Drainage Paper 56 (Hargreaves and Ra equations).

## AI-assistance statement

This code was written with substantial help from an AI assistant (Claude,
Anthropic). The author directed the design and is responsible for reviewing,
running and interpreting it. Disclose this wherever the repository is cited
in an application.

MIT licence.
