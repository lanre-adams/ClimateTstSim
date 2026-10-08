/* climate-tst-sim web interface. No build step; Chart.js is vendored. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const main = $("#main");
  const charts = {};
  const state = { info: null, datasets: [], climate: { ids: [], warming: 0, focus: null }, lastSim: null };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // ------------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toFixed(d));
  const pct = (v, d = 1) => (v === null || v === undefined ? "–" : `${(100 * v).toFixed(d)}%`);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const series = (i) => css(`--s${(i % 6) + 1}`);
  const clip = (s, n = 42) => (String(s).length > n ? `${String(s).slice(0, n - 1)}…` : String(s));

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = text; }
    if (!res.ok) {
      let msg = body && body.detail ? body.detail : `Request failed (${res.status})`;
      if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ");
      throw new Error(msg);
    }
    return body;
  }
  const postJSON = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 3200);
  }

  async function poll(job, onTick) {
    let j = job;
    while (j.status === "queued" || j.status === "running") {
      onTick(j);
      await new Promise((r) => setTimeout(r, 700));
      j = await api(`/api/jobs/${j.id}`);
    }
    onTick(j);
    if (j.status === "error") throw new Error(j.error || "The job failed.");
    return j.result;
  }

  function progressHTML(id) {
    return `<div id="${id}" hidden><div class="progress"><span></span></div><p class="small muted" data-msg></p></div>`;
  }
  function setProgress(id, j) {
    const el = $(`#${id}`);
    if (!el) return;
    el.hidden = false;
    $("span", el).style.width = `${Math.round(100 * (j.progress || 0))}%`;
    $("[data-msg]", el).textContent = j.status === "done" ? "Finished" : j.message || "";
  }

  function errorBox(msg) { return `<div class="notice error"><p>${esc(msg)}</p></div>`; }
  function warnList(ws) {
    if (!ws || !ws.length) return "";
    return `<div class="notice"><p><strong>Notes on this dataset</strong></p><ul>${ws.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>`;
  }

  // ------------------------------------------------------------------ charts
  function chartDefaults() {
    Chart.defaults.font.family = css("--sans") || "system-ui";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = css("--muted");
    Chart.defaults.borderColor = css("--grid");
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.legend.labels.boxHeight = 12;
    Chart.defaults.plugins.tooltip.backgroundColor = css("--ink");
    Chart.defaults.plugins.tooltip.titleColor = css("--panel");
    Chart.defaults.plugins.tooltip.bodyColor = css("--panel");
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.animation = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 300 };
  }
  function draw(id, config) {
    if (charts[id]) charts[id].destroy();
    const el = document.getElementById(id);
    if (!el) return null;
    charts[id] = new Chart(el, config);
    return charts[id];
  }
  function destroyAll() { Object.keys(charts).forEach((k) => { charts[k].destroy(); delete charts[k]; }); }

  function hbar(id, labels, values, opts = {}) {
    return draw(id, {
      type: "bar",
      data: { labels, datasets: [{ data: values, backgroundColor: opts.color || series(0), borderRadius: 3, barPercentage: 0.7, categoryPercentage: 0.85 }] },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: opts.label || ((c) => fmt(c.raw, opts.digits ?? 2)) } } },
        scales: {
          x: { min: opts.min, max: opts.max, title: { display: !!opts.xTitle, text: opts.xTitle }, grid: { color: css("--grid") } },
          y: { grid: { display: false }, ticks: { color: css("--ink") } },
        },
      },
    });
  }

  // ------------------------------------------------------------------ data
  async function refreshDatasets() { state.datasets = await api("/api/datasets"); return state.datasets; }
  const byKind = (k) => state.datasets.filter((d) => d.kind === k);
  const dsName = (id) => (state.datasets.find((d) => d.id === id) || {}).name || id;
  const sourceLabel = { builtin: "Built-in", upload: "Uploaded", synthetic: "Generated", simulated: "From simulation" };
  const kindLabel = { weather: "Weather", checks: "Check records" };
  function flag(d) { return d.synthetic ? `<span class="tag syn">Synthetic</span>` : `<span class="tag real">Real data</span>`; }
  function options(list, selected) {
    return list.map((d) => `<option value="${esc(d.id)}" ${d.id === selected ? "selected" : ""}>${esc(d.name)}${d.synthetic ? " (synthetic)" : ""}</option>`).join("");
  }

  // ------------------------------------------------------------------ router
  const routes = { overview: viewOverview, datasets: viewDatasets, climate: viewClimate, simulate: viewSimulate, checks: viewChecks };
  async function route() {
    const [name, query] = (location.hash.replace(/^#/, "") || "overview").split("?");
    const view = routes[name] || viewOverview;
    $$("nav a").forEach((a) => a.setAttribute("aria-current", a.dataset.route === name ? "page" : "false"));
    destroyAll();
    main.innerHTML = `<p class="muted">Loading…</p>`;
    try {
      await view(new URLSearchParams(query || ""));
    } catch (e) {
      main.innerHTML = `<h1>Something went wrong</h1>${errorBox(e.message)}<p>Check that the server is still running, then reload the page.</p>`;
    }
    main.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  // ================================================================== OVERVIEW
  async function viewOverview() {
    let results = null;
    if (state.info.has_results) {
      try { results = await api("/api/results"); } catch { results = null; }
    }
    main.innerHTML = `
      <h1>Which goats to check, how often, and when</h1>
      <p class="lede">A research bench for targeted treatment of worms in smallholder goats. It links a climate-driven index of larval survival on pasture to simulated flocks and lets you test which health checks, schedules and decision rules hold up across climates.</p>
      <div class="notice"><p><strong>Read before using.</strong> The built-in sites, the simulator and its outputs are synthetic, with illustrative parameters. Real data you add is analysed with the same illustrative transmission model. Use this to design studies, not to advise farmers.</p></div>
      <h2>Start here</h2>
      <ol class="steps">
        <li><a href="#datasets">Add or pick a dataset.</a> Upload daily weather for a place, upload goat check records, or generate a synthetic site.</li>
        <li><a href="#climate">Look at its climate.</a> See when temperature and moisture favour worm larvae, and what +2 °C would change.</li>
        <li><a href="#simulate">Run a simulation.</a> Compare treatment policies on deaths, doses, checking time and drug resistance.</li>
        <li><a href="#checks">Test which checks matter.</a> Score indicator sets on check records, and see whether a triage rule trained in one place works in another.</li>
      </ol>
      <h2>Last batch run</h2>
      <div id="ov-results"></div>`;
    const box = $("#ov-results");
    if (!results) {
      box.innerHTML = `<div class="empty">No batch results yet. Run <code>python scripts/run_all.py</code> (or the Docker image's batch command) to produce the full set of figures, or use <a href="#simulate">Simulate</a> for a quick interactive run.</div>`;
      return;
    }
    const pol = results.policy_summary_malawi_like || [];
    box.innerHTML = `
      <p class="muted small">From <code>scripts/run_all.py</code>${results.quick ? " in quick mode" : ""}; Malawi-like synthetic site, ${results.seeds.policy.length} replicates. Means with 5th–95th percentiles.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Policy</th><th class="num">Deaths per 100 goat-years</th><th class="num">Doses per goat-year</th><th class="num">Check minutes per goat-year</th><th class="num">Resistant share at end</th></tr></thead>
        <tbody>${pol.map((r) => `<tr><td>${esc(r.policy)}</td>
          <td class="num">${fmt(r.deaths_per_100_goat_years_mean, 1)} <span class="muted small">(${fmt(r.deaths_per_100_goat_years_p05, 1)}–${fmt(r.deaths_per_100_goat_years_p95, 1)})</span></td>
          <td class="num">${fmt(r.treatments_per_goat_year_mean, 2)}</td>
          <td class="num">${fmt(r.check_minutes_per_goat_year_mean, 0)}</td>
          <td class="num">${fmt(r.final_resistant_frac_mean, 2)}</td></tr>`).join("")}</tbody>
      </table></div>
      <div class="grid2" style="margin-top:20px">
        <figure><img src="/outputs/fig1_transmission_index_by_site.png" alt="Transmission index by month for three synthetic sites"><figcaption>Climatic suitability for larvae by month at three synthetic sites.</figcaption></figure>
        <figure><img src="/outputs/fig3_policy_tradeoffs.png" alt="Policy trade-offs: deaths, doses, check minutes and resistance"><figcaption>Policy trade-offs at the Malawi-like site.</figcaption></figure>
        <figure><img src="/outputs/fig2_indicator_value_for_effort.png" alt="Indicator sets compared by discrimination and effort"><figcaption>What each set of checks buys, sorted by effort.</figcaption></figure>
        <figure><img src="/outputs/fig5_shift_test.png" alt="Triage model tested on other climates"><figcaption>A triage rule trained on one site, tested on others.</figcaption></figure>
      </div>`;
  }

  // ================================================================== DATASETS
  async function viewDatasets(q) {
    await refreshDatasets();
    const mode = q.get("add") || "weather";
    main.innerHTML = `
      <h1>Datasets</h1>
      <p class="lede">Everything the climate, simulation and checks pages can use. Built-in sites cannot be deleted; anything you add is saved on this computer.</p>
      <div id="ds-table"></div>
      <div id="ds-preview"></div>
      <h2>Add a dataset</h2>
      <div class="seg" role="group" aria-label="Kind of dataset to add">
        <button type="button" data-mode="weather" aria-pressed="${mode === "weather"}">Upload weather</button>
        <button type="button" data-mode="checks" aria-pressed="${mode === "checks"}">Upload check records</button>
        <button type="button" data-mode="synthetic" aria-pressed="${mode === "synthetic"}">Create a synthetic site</button>
      </div>
      <div id="ds-form"></div>`;
    renderTable();
    renderForm(mode);
    $$(".seg button").forEach((b) => b.addEventListener("click", () => {
      $$(".seg button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      renderForm(b.dataset.mode);
    }));
  }

  function renderTable() {
    const rows = state.datasets;
    $("#ds-table").innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Name</th><th>Type</th><th>Source</th><th class="num">Rows</th><th>Period</th><th>Data</th><th>Actions</th></tr></thead>
      <tbody>${rows.map((d) => `<tr>
        <td><strong>${esc(d.name)}</strong>${d.lat !== null && d.lat !== undefined ? `<div class="small muted">Lat ${fmt(d.lat, 2)}${d.lon !== null && d.lon !== undefined ? `, lon ${fmt(d.lon, 2)}` : ""}</div>` : ""}</td>
        <td>${kindLabel[d.kind]}</td><td>${sourceLabel[d.source] || d.source}</td>
        <td class="num">${d.rows.toLocaleString()}</td>
        <td class="small">${d.start ? `${esc(d.start)} to ${esc(d.end)}` : "–"}</td>
        <td>${flag(d)}</td>
        <td><div class="row-actions">
          <button class="quiet small" data-act="preview" data-id="${d.id}">Preview</button>
          <a class="button quiet small" href="/api/datasets/${d.id}/download">Download</a>
          ${d.kind === "weather" ? `<a class="button quiet small" href="#climate?ids=${d.id}">Climate</a>` : `<a class="button quiet small" href="#checks?ds=${d.id}">Analyse</a>`}
          ${d.deletable ? `<button class="danger small" data-act="delete" data-id="${d.id}">Delete</button>` : ""}
        </div></td></tr>`).join("")}</tbody></table></div>`;
    $$("#ds-table [data-act]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.id;
      if (b.dataset.act === "preview") return showPreview(id);
      if (!confirmDelete(id)) return;
      try { await api(`/api/datasets/${id}`, { method: "DELETE" }); toast("Dataset deleted"); await refreshDatasets(); renderTable(); $("#ds-preview").innerHTML = ""; }
      catch (e) { toast(e.message); }
    }));
  }
  function confirmDelete(id) {
    const btn = $(`#ds-table [data-act="delete"][data-id="${id}"]`);
    if (btn.dataset.armed) return true;
    btn.dataset.armed = "1";
    btn.textContent = "Confirm delete";
    setTimeout(() => { if (btn.isConnected) { delete btn.dataset.armed; btn.textContent = "Delete"; } }, 4000);
    return false;
  }

  async function showPreview(id) {
    const meta = state.datasets.find((d) => d.id === id);
    const p = await api(`/api/datasets/${id}/preview?n=10`);
    $("#ds-preview").innerHTML = `<div class="panel">
      <h3 style="margin-top:0">${esc(meta.name)}</h3>
      <p class="small muted">${esc(meta.notes || "")} Showing the first ${p.rows.length} of ${p.total.toLocaleString()} rows.</p>
      ${warnList(meta.warnings)}
      <div class="table-wrap"><table><thead><tr>${p.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${p.rows.map((r) => `<tr>${r.map((v) => `<td>${esc(typeof v === "number" ? +v.toFixed(3) : v ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`;
    $("#ds-preview").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function renderForm(mode) {
    const box = $("#ds-form");
    if (mode === "weather") {
      box.innerHTML = `<div class="panel"><form id="f-weather">
        <p>Daily weather for one place. Accepted: a <strong>NASA POWER</strong> daily point CSV as downloaded (parameters T2M, T2M_MAX, T2M_MIN, PRECTOTCORR), or any CSV with a <code>date</code> column and columns for temperature and rainfall (names like <code>tmean</code>, <code>tmax</code>, <code>tmin</code>, <code>rain</code>, <code>T2M</code>, <code>precip</code> are recognised). At least 120 days; a year or more is better. <a href="/api/templates/weather">Download a template</a>.</p>
        <div class="form-grid">
          <div class="field wide"><label for="w-file">CSV file</label><input id="w-file" name="file" type="file" accept=".csv,.txt" required></div>
          <div class="field"><label for="w-name">Name</label><input id="w-name" name="name" type="text" placeholder="e.g. Lilongwe 2015–2024 (NASA POWER)"></div>
          <div class="field"><label for="w-lat">Latitude <span class="hint">required, −60 to 60</span></label><input id="w-lat" name="lat" type="number" step="0.0001" min="-60" max="60" required></div>
          <div class="field"><label for="w-lon">Longitude <span class="hint">optional</span></label><input id="w-lon" name="lon" type="number" step="0.0001" min="-180" max="180"></div>
          <div class="field wide"><label for="w-notes">Notes <span class="hint">source, licence, station</span></label><input id="w-notes" name="notes" type="text"></div>
          <label class="check field wide"><input type="checkbox" name="synthetic" value="true"> This file contains synthetic or simulated values, not observations</label>
        </div>
        <details><summary>Getting NASA POWER data</summary>
          <p class="small">From the repository: <code>python scripts/fetch_nasa_power.py --lat -13.98 --lon 33.78 --start 2015 --end 2024 --out data/real/lilongwe.csv</code>. Or use the NASA POWER Data Access Viewer (daily, point, Agroclimatology community) and download CSV. Latitude matters because it sets day length in the evaporation estimate.</p>
        </details>
        <div class="actions"><button type="submit">Add weather dataset</button></div>
        <div id="w-result"></div></form></div>`;
      $("#f-weather").addEventListener("submit", (e) => submitUpload(e, "/api/datasets/weather", "#w-result"));
    } else if (mode === "checks") {
      const weather = byKind("weather");
      box.innerHTML = `<div class="panel"><form id="f-checks">
        <p>One row per goat per check. <strong>Required:</strong> <code>famacha</code> (1–5) and <code>need</code> (1 if the goat needed treatment, 0 if not). <strong>Optional:</strong> <code>bcs</code>, <code>bottle_jaw</code>, <code>dag</code>, <code>nasal</code> (0/1), <code>fec</code> (eggs per gram), <code>farm</code>, <code>goat</code>, <code>date</code>, <code>kid</code>. Include <code>farm</code> so cross-validation keeps each farm's goats together. <a href="/api/templates/checks">Download a template</a>.</p>
        <p class="small muted">How you define <code>need</code> matters (for example PCV below 18%, or a vet's decision). Record your definition in the notes.</p>
        <div class="form-grid">
          <div class="field wide"><label for="c-file">CSV file</label><input id="c-file" name="file" type="file" accept=".csv,.txt" required></div>
          <div class="field"><label for="c-name">Name</label><input id="c-name" name="name" type="text" placeholder="e.g. Village A FPC records 2024"></div>
          <div class="field"><label for="c-link">Link to weather <span class="hint">adds the climate index by date</span></label>
            <select id="c-link" name="linked_weather"><option value="">None</option>${options(weather)}</select></div>
          <div class="field wide"><label for="c-notes">Notes <span class="hint">how 'need' was defined, who scored, consent</span></label><input id="c-notes" name="notes" type="text"></div>
          <label class="check field wide"><input type="checkbox" name="synthetic" value="true"> These records are synthetic or simulated, not field observations</label>
        </div>
        <div class="actions"><button type="submit">Add check records</button></div>
        <div id="c-result"></div></form></div>`;
      $("#f-checks").addEventListener("submit", (e) => submitUpload(e, "/api/datasets/checks", "#c-result"));
    } else {
      const sites = state.info.sites;
      box.innerHTML = `<div class="panel"><form id="f-syn">
        <p>Generate synthetic daily weather from a site's monthly climate. Start from a preset, or edit the twelve monthly values to describe another place (for example from a published climate normal).</p>
        <div class="form-grid">
          <div class="field"><label for="s-name">Name</label><input id="s-name" name="name" type="text" placeholder="e.g. Uyo-like, +2 °C"></div>
          <div class="field"><label for="s-base">Start from</label><select id="s-base">${Object.entries(sites).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("")}</select></div>
          <div class="field"><label for="s-years">Years</label><input id="s-years" type="number" min="1" max="30" value="4"></div>
          <div class="field"><label for="s-seed">Random seed</label><input id="s-seed" type="number" value="1"></div>
          <div class="field"><label for="s-warm">Warming, °C</label><input id="s-warm" type="number" step="0.5" min="-3" max="6" value="0"></div>
          <div class="field"><label for="s-rain">Rainfall multiplier</label><input id="s-rain" type="number" step="0.1" min="0.2" max="3" value="1"></div>
        </div>
        <label class="check" style="margin-top:14px"><input type="checkbox" id="s-custom"> Edit the monthly values (makes a custom site)</label>
        <div id="s-months" hidden style="margin-top:12px"></div>
        <div class="actions"><button type="submit">Create synthetic site</button></div>
        <div id="s-result"></div></form></div>`;
      const fillMonths = () => {
        const s = sites[$("#s-base").value];
        $("#s-months").innerHTML = `
          <div class="form-grid" style="margin-bottom:10px">
            <div class="field"><label for="s-lat">Latitude</label><input id="s-lat" type="number" step="0.01" min="-60" max="60" value="${s.lat}"></div>
            <div class="field"><label for="s-lon">Longitude</label><input id="s-lon" type="number" step="0.01" value="${s.lon}"></div>
          </div>
          <div class="months">
            <span></span>${MONTHS.map((m) => `<span>${m}</span>`).join("")}
            <span class="label">Mean temp, °C</span>${s.tmean.map((v, i) => `<input type="number" step="0.1" data-t="${i}" value="${v}" aria-label="Mean temperature ${MONTHS[i]}">`).join("")}
            <span class="label">Rainfall, mm</span>${s.rain_mm.map((v, i) => `<input type="number" step="1" min="0" data-r="${i}" value="${v}" aria-label="Rainfall ${MONTHS[i]}">`).join("")}
          </div>`;
      };
      fillMonths();
      $("#s-base").addEventListener("change", fillMonths);
      $("#s-custom").addEventListener("change", (e) => { $("#s-months").hidden = !e.target.checked; });
      $("#f-syn").addEventListener("submit", async (e) => {
        e.preventDefault();
        const btn = $("button[type=submit]", e.target);
        const body = {
          name: $("#s-name").value, years: +$("#s-years").value, seed: +$("#s-seed").value,
          warming_c: +$("#s-warm").value, rain_scale: +$("#s-rain").value,
        };
        if ($("#s-custom").checked) {
          body.tmean = $$("[data-t]").map((i) => +i.value);
          body.rain_mm = $$("[data-r]").map((i) => +i.value);
          body.lat = +$("#s-lat").value;
          body.lon = +$("#s-lon").value;
        } else body.site = $("#s-base").value;
        btn.disabled = true;
        try {
          const meta = await postJSON("/api/datasets/synthetic", body);
          $("#s-result").innerHTML = addedBox(meta);
          await refreshDatasets(); renderTable();
        } catch (err) { $("#s-result").innerHTML = errorBox(err.message); }
        btn.disabled = false;
      });
    }
  }

  function addedBox(meta) {
    const next = meta.kind === "weather" ? `<a href="#climate?ids=${meta.id}">See its climate</a> or <a href="#simulate?w=${meta.id}">simulate on it</a>.` : `<a href="#checks?ds=${meta.id}">Analyse these records</a>.`;
    return `<div class="notice ok"><p><strong>Added “${esc(meta.name)}”</strong>: ${meta.rows.toLocaleString()} rows${meta.start ? `, ${esc(meta.start)} to ${esc(meta.end)}` : ""}. ${next}</p></div>${warnList(meta.warnings)}`;
  }

  async function submitUpload(e, url, resultSel) {
    e.preventDefault();
    const form = e.target;
    const btn = $("button[type=submit]", form);
    const fd = new FormData(form);
    for (const [k, v] of Array.from(fd.entries())) if (v === "" && k !== "file") fd.delete(k);
    btn.disabled = true;
    $(resultSel).innerHTML = `<p class="muted small">Checking the file…</p>`;
    try {
      const meta = await api(url, { method: "POST", body: fd });
      $(resultSel).innerHTML = addedBox(meta);
      form.reset();
      await refreshDatasets(); renderTable();
    } catch (err) { $(resultSel).innerHTML = errorBox(err.message); }
    btn.disabled = false;
  }

  // ================================================================== CLIMATE
  async function viewClimate(q) {
    await refreshDatasets();
    const weather = byKind("weather");
    const fromQ = (q.get("ids") || "").split(",").filter((id) => weather.some((w) => w.id === id));
    if (fromQ.length) state.climate.ids = fromQ;
    if (!state.climate.ids.length) state.climate.ids = weather.slice(0, 3).map((w) => w.id);
    state.climate.ids = state.climate.ids.filter((id) => weather.some((w) => w.id === id)).slice(0, 4);
    state.climate.focus = state.climate.ids.includes(state.climate.focus) ? state.climate.focus : state.climate.ids[0];
    main.innerHTML = `
      <h1>Climate and larval suitability</h1>
      <p class="lede">The transmission index estimates how favourable each day's temperature and moisture are for worm eggs to develop and larvae to reach and survive on grass (1 = 25 °C with no moisture limit). It describes climatic suitability, not infection.</p>
      <div class="panel">
        <div class="form-grid">
          <div class="field wide"><span class="label">Weather datasets <span class="hint">up to four</span></span>
            <div class="checks" style="flex-direction:row;flex-wrap:wrap;gap:8px 20px">${weather.map((w) => `<label class="check"><input type="checkbox" value="${w.id}" ${state.climate.ids.includes(w.id) ? "checked" : ""}> ${esc(w.name)} ${w.synthetic ? "" : `<span class="tag real">Real</span>`}</label>`).join("")}</div></div>
          <div class="field"><label for="cl-warm">What if it were warmer? <span class="hint" id="cl-warm-v">${fmt(state.climate.warming, 1)} °C</span></label>
            <input id="cl-warm" type="range" min="-1" max="4" step="0.5" value="${state.climate.warming}"></div>
        </div>
      </div>
      <div id="cl-body"></div>`;
    $$(".panel input[type=checkbox]").forEach((c) => c.addEventListener("change", () => {
      const ids = $$(".panel input[type=checkbox]:checked").map((x) => x.value);
      if (ids.length > 4) { c.checked = false; toast("Choose up to four datasets"); return; }
      state.climate.ids = ids;
      if (!ids.includes(state.climate.focus)) state.climate.focus = ids[0];
      loadClimate();
    }));
    $("#cl-warm").addEventListener("input", (e) => { $("#cl-warm-v").textContent = `${fmt(+e.target.value, 1)} °C`; });
    $("#cl-warm").addEventListener("change", (e) => { state.climate.warming = +e.target.value; loadClimate(); });
    await loadClimate();
  }

  async function loadClimate() {
    const body = $("#cl-body");
    const ids = state.climate.ids;
    if (!ids.length) { body.innerHTML = `<div class="empty">Choose at least one weather dataset.</div>`; return; }
    body.innerHTML = `<p class="muted">Computing…</p>`;
    let data;
    try { data = await Promise.all(ids.map((id) => api(`/api/climate/${id}?warming=${state.climate.warming}`))); }
    catch (e) { body.innerHTML = errorBox(e.message); return; }
    const focus = data.find((d) => d.id === state.climate.focus) || data[0];
    body.innerHTML = `
      <div class="grid4">${data.map((d, i) => `<div class="stat" style="border-top:3px solid ${series(i)}">
        <strong>${esc(d.name)}</strong>
        <dl><dt>Period</dt><dd>${esc(d.summary.start.slice(0, 7))} to ${esc(d.summary.end.slice(0, 7))}</dd>
        <dt>Annual rainfall</dt><dd>${fmt(d.summary.annual_rain, 0)} mm</dd>
        <dt>Mean temperature</dt><dd>${fmt(d.summary.mean_temp, 1)} °C</dd>
        <dt>Months at high risk</dt><dd>${d.summary.high_months} of 12</dd>
        <dt>Days high / low</dt><dd>${fmt(d.summary.pct_days_high, 0)}% / ${fmt(d.summary.pct_days_low, 0)}%</dd></dl></div>`).join("")}</div>
      <div class="panel">
        <p class="chart-title">Average transmission index by month</p>
        <p class="chart-sub">${data.length === 1 ? "Line = mean across years; shaded = 10th–90th percentile of monthly values. " : ""}Dashed lines mark the illustrative moderate (0.15) and high (0.45) bands used for climate-gated checking.</p>
        <div class="chart-box"><canvas id="ch-monthly" role="img" aria-label="Monthly transmission index"></canvas></div>
      </div>
      <div class="panel">
        <div style="display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap">
          <div><p class="chart-title">Week by week</p><p class="chart-sub">Transmission index and weekly rainfall for one dataset.</p></div>
          ${data.length > 1 ? `<label class="small">Show <select id="cl-focus">${data.map((d) => `<option value="${d.id}" ${d.id === focus.id ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select></label>` : ""}
        </div>
        <div class="chart-box short"><canvas id="ch-weekly" role="img" aria-label="Weekly transmission index"></canvas></div>
        <div class="chart-box short" style="height:140px;margin-top:8px"><canvas id="ch-rain" role="img" aria-label="Weekly rainfall"></canvas></div>
      </div>
      <div class="grid2">
        <div class="panel"><p class="chart-title">Mean temperature by month</p><div class="chart-box short"><canvas id="ch-temp"></canvas></div></div>
        <div class="panel"><p class="chart-title">Rainfall by month</p><div class="chart-box short"><canvas id="ch-rainm"></canvas></div></div>
      </div>
      ${data.some((d) => d.synthetic) ? `<p class="small muted">Synthetic datasets show an illustrative monthly shape, not measured climate.</p>` : ""}`;
    if ($("#cl-focus")) $("#cl-focus").addEventListener("change", (e) => { state.climate.focus = e.target.value; loadClimate(); });

    const bandLine = (v, label) => ({ label, data: Array(12).fill(v), borderColor: css("--rule-strong"), borderDash: [5, 4], borderWidth: 1, pointRadius: 0, fill: false });
    const ds = [];
    data.forEach((d, i) => {
      if (data.length === 1) {
        ds.push({ label: "10th percentile", data: d.monthly.tpi_lo, borderWidth: 0, pointRadius: 0, fill: false });
        ds.push({ label: "90th percentile", data: d.monthly.tpi_hi, borderWidth: 0, pointRadius: 0, fill: "-1", backgroundColor: series(i) + "26" });
      }
      ds.push({ label: d.name, data: d.monthly.tpi_mean, borderColor: series(i), backgroundColor: series(i), borderWidth: 2, pointRadius: 3, cubicInterpolationMode: "monotone" });
    });
    ds.push(bandLine(0.45, "High band"), bandLine(0.15, "Moderate band"));
    draw("ch-monthly", {
      type: "line", data: { labels: MONTHS, datasets: ds },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { filter: (it) => !/percentile|band/.test(it.text) } }, tooltip: { filter: (it) => !/band/.test(it.dataset.label), callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.raw, 2)}` } } },
        scales: { y: { min: 0, title: { display: true, text: "Transmission index" } }, x: { grid: { display: false } } },
      },
    });
    draw("ch-weekly", {
      type: "line",
      data: { labels: focus.weekly.dates, datasets: [{ label: "Transmission index", data: focus.weekly.tpi, borderColor: series(0), backgroundColor: series(0), borderWidth: 1.5, pointRadius: 0, cubicInterpolationMode: "monotone" }] },
      options: {
        interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false } },
        scales: { x: { ticks: { maxTicksLimit: 10 }, grid: { display: false } }, y: { min: 0, title: { display: true, text: "Index" } } },
      },
    });
    draw("ch-rain", {
      type: "bar",
      data: { labels: focus.weekly.dates, datasets: [{ label: "Rainfall (mm per week)", data: focus.weekly.rain, backgroundColor: series(1), barPercentage: 1, categoryPercentage: 1 }] },
      options: { plugins: { legend: { display: false } }, scales: { x: { ticks: { maxTicksLimit: 10 }, grid: { display: false } }, y: { title: { display: true, text: "mm / week" } } } },
    });
    const small = (id, key, unit) => draw(id, {
      type: "line",
      data: { labels: MONTHS, datasets: data.map((d, i) => ({ label: d.name, data: d.monthly[key], borderColor: series(i), backgroundColor: series(i), borderWidth: 2, pointRadius: 2, cubicInterpolationMode: "monotone" })) },
      options: { interaction: { mode: "index", intersect: false }, plugins: { legend: { display: data.length > 1 } }, scales: { y: { title: { display: true, text: unit } }, x: { grid: { display: false } } } },
    });
    small("ch-temp", "tmean", "°C");
    small("ch-rainm", "rain", "mm per month");
  }

  // ================================================================== SIMULATE
  async function viewSimulate(q) {
    await refreshDatasets();
    const weather = byKind("weather");
    const pol = state.info.policies;
    const pre = q.get("w") || (weather[0] || {}).id;
    main.innerHTML = `
      <h1>Simulate treatment policies</h1>
      <p class="lede">Runs simulated smallholder goat flocks on a chosen weather record and compares how each policy trades deaths against doses, checking time and drug resistance. Each replicate is a different random flock on the same weather.</p>
      <div class="panel"><form id="f-sim">
        <div class="form-grid">
          <div class="field"><label for="sim-w">Weather</label><select id="sim-w" required>${options(weather, pre)}</select></div>
          <div class="field"><label for="sim-reps">Replicates <span class="hint">1–10</span></label><input id="sim-reps" type="number" min="1" max="10" value="3"></div>
          <div class="field"><label for="sim-farms">Farms <span class="hint">3–8 goats each</span></label><input id="sim-farms" type="number" min="5" max="200" value="60"></div>
          <div class="field"><label for="sim-warm">Extra warming, °C</label><input id="sim-warm" type="number" min="-3" max="6" step="0.5" value="0"></div>
          <div class="field wide"><span class="label">Policies</span>
            <div class="checks" style="flex-direction:row;flex-wrap:wrap;gap:8px 22px">${Object.entries(pol).map(([k, v]) => `<label class="check"><input type="checkbox" name="pol" value="${k}" checked> ${esc(v)}</label>`).join("")}</div></div>
        </div>
        <details><summary>Advanced parameters</summary>
          <p class="small muted">Leave blank to use the defaults in docs/PARAMETERS.md. All are illustrative.</p>
          <div class="form-grid">
            <div class="field"><label for="a-egg">Transmission dial <span class="hint">eggs reaching pasture, default 0.0025</span></label><input id="a-egg" type="number" step="0.0005" min="0.0001" max="0.01" placeholder="0.0025"></div>
            <div class="field"><label for="a-res">Starting resistant share <span class="hint">default 0.02</span></label><input id="a-res" type="number" step="0.01" min="0" max="0.5" placeholder="0.02"></div>
            <div class="field"><label for="a-eff">Drug efficacy on resistant worms <span class="hint">default 0.2</span></label><input id="a-eff" type="number" step="0.05" min="0" max="0.9" placeholder="0.2"></div>
            <div class="field"><label for="a-err">FAMACHA scoring error <span class="hint">default 0.25</span></label><input id="a-err" type="number" step="0.05" min="0" max="0.6" placeholder="0.25"></div>
          </div>
        </details>
        <label class="check" style="margin-top:14px"><input type="checkbox" id="sim-save"> Save the five-point check records from replicate 1 as a new dataset, for the Checks page</label>
        <div class="actions"><button type="submit" id="sim-run">Run simulation</button><span class="small muted" id="sim-est"></span></div>
        ${progressHTML("sim-prog")}
      </form></div>
      <div id="sim-out"></div>`;
    const est = () => {
      const w = weather.find((x) => x.id === $("#sim-w").value) || { rows: 1461 };
      const n = $$("input[name=pol]:checked").length * +$("#sim-reps").value;
      const secs = Math.max(2, Math.round(n * (w.rows / 1461) * (+$("#sim-farms").value / 60) * 1.4));
      $("#sim-est").textContent = `About ${secs < 90 ? `${secs} seconds` : `${Math.round(secs / 60)} minutes`}`;
    };
    $$("#f-sim input, #f-sim select").forEach((el) => el.addEventListener("change", est));
    est();
    $("#f-sim").addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = {
        weather_id: $("#sim-w").value, replicates: +$("#sim-reps").value, n_farms: +$("#sim-farms").value,
        warming_c: +$("#sim-warm").value, policies: $$("input[name=pol]:checked").map((c) => c.value),
        save_checks: $("#sim-save").checked,
        egg_to_pool: $("#a-egg").value, initial_resistant_fraction: $("#a-res").value,
        efficacy_resistant: $("#a-eff").value, famacha_error: $("#a-err").value,
      };
      $("#sim-run").disabled = true;
      $("#sim-out").innerHTML = "";
      try {
        const job = await postJSON("/api/simulate", body);
        const res = await poll(job, (j) => setProgress("sim-prog", j));
        state.lastSim = res;
        renderSim(res);
      } catch (err) { $("#sim-out").innerHTML = errorBox(err.message); }
      $("#sim-run").disabled = false;
    });
    if (state.lastSim) renderSim(state.lastSim);
  }

  function renderSim(r) {
    const s = r.summary;
    const labels = s.map((x) => x.policy);
    const st = r.settings;
    $("#sim-out").innerHTML = `
      <h2>Results</h2>
      <p class="muted small">${esc(r.weather.name)}${r.weather.synthetic ? " (synthetic)" : " (real weather)"}; ${st.replicates} replicate${st.replicates > 1 ? "s" : ""}, ${st.n_farms} farms, ${st.recorded_days} days recorded after a ${st.burn_in_days}-day burn-in${st.warming_c ? `, ${st.warming_c > 0 ? "+" : ""}${st.warming_c} °C` : ""}. Bars show means; the table gives 5th–95th percentiles.</p>
      ${r.saved_checks_id ? `<div class="notice ok"><p>Check records saved as a new dataset. <a href="#checks?ds=${r.saved_checks_id}">Analyse them</a>.</p></div>` : ""}
      <div class="grid2">
        <div class="panel"><p class="chart-title">Deaths per 100 goat-years</p><div class="chart-box short"><canvas id="sim-d"></canvas></div></div>
        <div class="panel"><p class="chart-title">Anthelmintic doses per goat-year</p><div class="chart-box short"><canvas id="sim-t"></canvas></div></div>
        <div class="panel"><p class="chart-title">Checking time, minutes per goat-year</p><div class="chart-box short"><canvas id="sim-m"></canvas></div></div>
        <div class="panel"><p class="chart-title">Resistant share of worms at the end</p><div class="chart-box short"><canvas id="sim-r"></canvas></div></div>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Policy</th><th class="num">Deaths /100 goat-yr</th><th class="num">Doses /goat-yr</th><th class="num">Check min /goat-yr</th><th class="num">Goat-days anaemic</th><th class="num">Resistant share</th></tr></thead>
        <tbody>${s.map((x) => `<tr><td>${esc(x.policy)}</td>${["deaths_per_100_goat_years", "treatments_per_goat_year", "check_minutes_per_goat_year", "pct_goat_days_anaemic", "final_resistant_frac"].map((m, i) => {
          const d = [1, 2, 0, 1, 2][i];
          const suffix = m === "pct_goat_days_anaemic" ? "%" : "";
          return `<td class="num">${fmt(x[m].mean, d)}${suffix}${st.replicates > 1 ? `<div class="small muted">${fmt(x[m].p05, d)}–${fmt(x[m].p95, d)}</div>` : ""}</td>`;
        }).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="panel"><p class="chart-title">Resistant share of adult worms over time</p><p class="chart-sub">Weekly mean across replicates.</p><div class="chart-box"><canvas id="sim-rt"></canvas></div></div>
      <div class="panel"><p class="chart-title">Goats needing treatment over time</p><p class="chart-sub">Percent of live goats that are anaemic or carry a heavy burden.</p><div class="chart-box short"><canvas id="sim-nt"></canvas></div>
        <p class="chart-title" style="margin-top:14px">Transmission index for the same weeks</p><div class="chart-box short" style="height:130px"><canvas id="sim-tpi"></canvas></div></div>`;
    const tip = (m, d) => (c) => { const x = s[c.dataIndex][m]; return `${fmt(x.mean, d)} (5th–95th: ${fmt(x.p05, d)}–${fmt(x.p95, d)})`; };
    hbar("sim-d", labels, s.map((x) => x.deaths_per_100_goat_years.mean), { label: tip("deaths_per_100_goat_years", 1), min: 0 });
    hbar("sim-t", labels, s.map((x) => x.treatments_per_goat_year.mean), { label: tip("treatments_per_goat_year", 2), min: 0 });
    hbar("sim-m", labels, s.map((x) => x.check_minutes_per_goat_year.mean), { label: tip("check_minutes_per_goat_year", 0), min: 0 });
    hbar("sim-r", labels, s.map((x) => x.final_resistant_frac.mean), { label: tip("final_resistant_frac", 2), min: 0, max: 1 });
    const keys = s.map((x) => x.key);
    const first = r.series[keys[0]];
    const lines = (field) => keys.map((k, i) => ({ label: s[i].policy, data: r.series[k][field], borderColor: series(i), backgroundColor: series(i), borderWidth: 2, pointRadius: 0, cubicInterpolationMode: "monotone" }));
    const xopts = { ticks: { maxTicksLimit: 10 }, grid: { display: false } };
    draw("sim-rt", { type: "line", data: { labels: first.dates, datasets: lines("resistant") }, options: { interaction: { mode: "index", intersect: false }, scales: { x: xopts, y: { min: 0, max: 1, title: { display: true, text: "Resistant share" } } } } });
    draw("sim-nt", { type: "line", data: { labels: first.dates, datasets: lines("need") }, options: { interaction: { mode: "index", intersect: false }, scales: { x: xopts, y: { min: 0, title: { display: true, text: "% of goats" } } } } });
    draw("sim-tpi", { type: "line", data: { labels: r.tpi.dates, datasets: [{ label: "Transmission index", data: r.tpi.values, borderColor: css("--muted"), borderWidth: 1.5, pointRadius: 0 }] }, options: { plugins: { legend: { display: false } }, scales: { x: xopts, y: { min: 0 } } } });
  }

  // ================================================================== CHECKS
  async function viewChecks(q) {
    await refreshDatasets();
    const checks = byKind("checks");
    main.innerHTML = `
      <h1>Checks and triage</h1>
      <p class="lede">Which health checks are worth the time they take, and does a decision rule learned in one place still work in another? Works on uploaded field records or on records saved from a simulation.</p>
      <div id="ck-body"></div>`;
    if (!checks.length) {
      $("#ck-body").innerHTML = `<div class="empty">No check records yet. <a href="#datasets?add=checks">Upload field records</a>, or tick “Save the five-point check records” on <a href="#simulate">Simulate</a> and run a simulation.</div>`;
      return;
    }
    const pre = checks.some((c) => c.id === q.get("ds")) ? q.get("ds") : checks[0].id;
    $("#ck-body").innerHTML = `
      <h2>Which checks are worth it?</h2>
      <div class="panel"><form id="f-ind">
        <div class="form-grid"><div class="field"><label for="ind-ds">Check records</label><select id="ind-ds">${options(checks, pre)}</select></div></div>
        <p class="small muted" style="margin-top:10px">Each set of indicators is scored by cross-validation, grouped by farm when farm IDs exist. Only sets whose columns are present are scored. Effort is in illustrative minutes per goat (a lab egg count is counted as 20).</p>
        <div class="actions"><button type="submit" id="ind-run">Score indicator sets</button></div>
        ${progressHTML("ind-prog")}
      </form></div>
      <div id="ind-out"></div>
      <h2>Does a triage rule transfer?</h2>
      <div class="panel"><form id="f-tri">
        <div class="form-grid">
          <div class="field"><label for="tri-train">Train on</label><select id="tri-train">${options(checks, pre)}</select></div>
          <div class="field"><label for="tri-sens">Target sensitivity <span class="hint">on the training records</span></label><input id="tri-sens" type="number" min="0.5" max="0.99" step="0.01" value="0.9"></div>
          <div class="field wide"><span class="label">Test on</span>
            <div class="checks">${checks.map((c) => `<label class="check"><input type="checkbox" name="tri-test" value="${c.id}" ${c.id !== pre ? "checked" : ""}> ${esc(c.name)} ${flag(c)}</label>`).join("")}</div></div>
        </div>
        <p class="small muted" style="margin-top:10px">A logistic model is fitted on the training records using the indicators every chosen dataset shares. Its threshold is set to reach the target sensitivity on training data, then applied unchanged to each test set.</p>
        <div class="actions"><button type="submit" id="tri-run">Train and test</button></div>
        ${progressHTML("tri-prog")}
      </form></div>
      <div id="tri-out"></div>`;
    $("#f-ind").addEventListener("submit", async (e) => {
      e.preventDefault();
      $("#ind-run").disabled = true; $("#ind-out").innerHTML = "";
      try {
        const job = await postJSON("/api/indicators", { dataset_id: $("#ind-ds").value });
        renderIndicators(await poll(job, (j) => setProgress("ind-prog", j)));
      } catch (err) { $("#ind-out").innerHTML = errorBox(err.message); }
      $("#ind-run").disabled = false;
    });
    $("#f-tri").addEventListener("submit", async (e) => {
      e.preventDefault();
      $("#tri-run").disabled = true; $("#tri-out").innerHTML = "";
      try {
        const job = await postJSON("/api/triage", { train_id: $("#tri-train").value, test_ids: $$("input[name=tri-test]:checked").map((c) => c.value), target_sensitivity: +$("#tri-sens").value });
        renderTriage(await poll(job, (j) => setProgress("tri-prog", j)));
      } catch (err) { $("#tri-out").innerHTML = errorBox(err.message); }
      $("#tri-run").disabled = false;
    });
  }

  function renderIndicators(r) {
    const rows = r.rows;
    const circular = r.dataset.source === "simulated" ? `<div class="notice"><p>These records come from the simulator, where “need” is defined mostly by anaemia, so FAMACHA is favoured by construction. Treat the ranking as a test of the method; real field records are needed to answer the question.</p></div>` : "";
    $("#ind-out").innerHTML = `
      ${circular}
      <p class="small muted">${esc(r.dataset.name)}: ${r.n_checks.toLocaleString()} checks, ${pct(r.prevalence)} needed treatment. ${esc(rows[0]?.cv || "")}.${r.skipped.length ? ` Not scored (columns missing): ${r.skipped.map(esc).join(", ")}.` : ""}</p>
      <div class="panel"><p class="chart-title">Discrimination by indicator set, cheapest first</p><p class="chart-sub">AUROC: 0.5 = no better than chance, 1 = perfect ranking.</p>
        <div class="chart-box" style="height:${80 + rows.length * 34}px"><canvas id="ind-ch"></canvas></div></div>
      <div class="table-wrap"><table><thead><tr><th>Indicator set</th><th class="num">Minutes per goat</th><th class="num">AUROC</th><th class="num">Sensitivity at 80% specificity</th><th class="num">Brier score</th><th>Best value for effort</th></tr></thead>
        <tbody>${rows.map((x) => `<tr><td>${esc(x.indicator_set)}</td><td class="num">${fmt(x.minutes_per_check, 1)}</td><td class="num">${fmt(x.auroc, 3)}</td><td class="num">${fmt(x.sens_at_spec80, 2)}</td><td class="num">${fmt(x.brier, 3)}</td><td>${x.pareto ? "Yes" : ""}</td></tr>`).join("")}</tbody></table></div>`;
    hbar("ind-ch", rows.map((x) => `${x.indicator_set} (${fmt(x.minutes_per_check, 1)} min)`), rows.map((x) => x.auroc), { min: 0.4, max: 1, digits: 3, xTitle: "AUROC" });
  }

  function renderTriage(r) {
    const rows = r.rows;
    $("#tri-out").innerHTML = `
      <p class="small muted">Indicators used: ${r.features.map(esc).join(", ")}. Threshold ${fmt(r.threshold, 3)} chosen for ${pct(r.target_sensitivity, 0)} sensitivity on the training records.</p>
      <div class="table-wrap"><table><thead><tr><th>Dataset</th><th class="num">Checks</th><th class="num">Observed need</th><th class="num">Mean predicted</th><th class="num">AUROC</th><th class="num">Sensitivity</th><th class="num">Specificity</th></tr></thead>
        <tbody>${rows.map((x) => `<tr><td>${esc(x.test_set)} ${x.synthetic ? `<span class="tag syn">Synthetic</span>` : `<span class="tag real">Real</span>`}</td><td class="num">${x.n_checks.toLocaleString()}</td><td class="num">${pct(x.observed_rate)}</td><td class="num">${pct(x.mean_predicted)}</td><td class="num">${fmt(x.auroc, 3)}</td><td class="num">${fmt(x.sensitivity, 2)}</td><td class="num">${fmt(x.specificity, 2)}</td></tr>`).join("")}</tbody></table></div>
      <div class="panel"><p class="chart-title">Sensitivity and specificity at the training threshold</p><div class="chart-box" style="height:${100 + rows.length * 48}px"><canvas id="tri-ch"></canvas></div></div>
      <details><summary>Model coefficients (standardised)</summary><div class="table-wrap" style="margin-top:8px"><table><tbody>${Object.entries(r.coefficients).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${fmt(v, 3)}</td></tr>`).join("")}</tbody></table></div></details>`;
    draw("tri-ch", {
      type: "bar",
      data: {
        labels: rows.map((x) => clip(x.test_set)),
        datasets: [
          { label: "Sensitivity", data: rows.map((x) => x.sensitivity), backgroundColor: series(0), borderRadius: 3 },
          { label: "Specificity", data: rows.map((x) => x.specificity), backgroundColor: series(1), borderRadius: 3 },
        ],
      },
      options: { indexAxis: "y", plugins: { tooltip: { callbacks: { title: (it) => rows[it[0].dataIndex].test_set, label: (c) => `${c.dataset.label}: ${fmt(c.raw, 2)}` } } }, scales: { x: { min: 0, max: 1 }, y: { grid: { display: false }, ticks: { color: css("--ink") } } } },
    });
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    try {
      state.info = await api("/api/info");
      $("#version").textContent = `Version ${state.info.version}`;
    } catch (e) {
      main.innerHTML = `<h1>Cannot reach the server</h1>${errorBox(e.message)}`;
      return;
    }
    chartDefaults();
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { chartDefaults(); route(); });
    window.addEventListener("hashchange", route);
    route();
  }
  boot();
})();
