/* OrbitDesk frontend — vanilla JS + Leaflet. Talks only to /api endpoints. */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  group: "stations",
  query: "",
  satellites: [],
  selectedId: null,
  markers: new Map(),
  trackLine: null,
  chatHistory: [],
};

// ---------------- helpers ----------------

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = null;
  try { body = await resp.json(); } catch { /* non-JSON */ }
  if (!resp.ok) {
    const detail = body && body.detail
      ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail))
      : `HTTP ${resp.status}`;
    throw new Error(detail);
  }
  return body;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
}

function relTime(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function show(el, visible) { el.classList.toggle("hidden", !visible); }

// ---------------- map ----------------

const map = L.map("map", { worldCopyJump: true, zoomControl: true }).setView([20, 0], 1);
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
  maxZoom: 19,
}).addTo(map);

function markerIcon(selected) {
  return L.divIcon({
    className: "",
    html: `<div class="sat-marker${selected ? " selected" : ""}"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

function clearMarkers() {
  state.markers.forEach((m) => map.removeLayer(m));
  state.markers.clear();
  if (state.trackLine) { map.removeLayer(state.trackLine); state.trackLine = null; }
}

function addMarker(sat, lat, lon) {
  const existing = state.markers.get(sat.norad_id);
  if (existing) { existing.setLatLng([lat, lon]); return existing; }
  const m = L.marker([lat, lon], {
    icon: markerIcon(sat.norad_id === state.selectedId),
    title: sat.name,
    alt: sat.name,
  }).addTo(map);
  m.on("click", () => selectSatellite(sat.norad_id));
  m.bindPopup(`<strong>${sat.name}</strong><br>NORAD ${sat.norad_id}<br><em>calculated position</em>`);
  state.markers.set(sat.norad_id, m);
  return m;
}

// ---------------- satellite list ----------------

function setListState({ loading = false, error = null, empty = false }) {
  show($("list-loading"), loading);
  show($("list-error"), !!error);
  if (error) $("list-error-msg").textContent = error;
  show($("sat-list"), !loading && !error && !empty);
  show($("list-empty"), empty && !loading && !error);
}

async function loadGroups() {
  const data = await api("/api/satellites/groups");
  const sel = $("group-select");
  sel.innerHTML = "";
  const cachedIds = new Set(data.cached.map((g) => g.group_name));
  for (const g of data.available) {
    const opt = document.createElement("option");
    opt.value = g.id;
    opt.textContent = g.label + (cachedIds.has(g.id) ? "" : " (not loaded)");
    if (g.id === state.group) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function loadSatellites() {
  setListState({ loading: true });
  $("btn-refresh").disabled = true;
  try {
    // Ensure the group is fetched/cached server-side first.
    const meta = await api(`/api/satellites/refresh/${state.group}`, { method: "POST" });
    show($("notice"), !!meta.notice);
    if (meta.notice) $("notice").textContent = meta.notice;

    const params = new URLSearchParams({ group: state.group, limit: "500" });
    if (state.query) params.set("q", state.query);
    const data = await api(`/api/satellites?${params}`);

    state.satellites = data.items;
    updateChips(data.data_source, data.last_updated);
    renderList();
  } catch (err) {
    setListState({ error: err.message });
  } finally {
    $("btn-refresh").disabled = false;
  }
}

function renderList() {
  const ul = $("sat-list");
  ul.innerHTML = "";
  if (!state.satellites.length) { setListState({ empty: true }); return; }
  setListState({});
  for (const sat of state.satellites) {
    const li = document.createElement("li");
    li.className = "sat-item" + (sat.norad_id === state.selectedId ? " selected" : "");
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = sat.name;
    const id = document.createElement("span");
    id.className = "id";
    id.textContent = sat.norad_id;
    li.append(name, id);
    const pick = () => selectSatellite(sat.norad_id);
    li.addEventListener("click", pick);
    li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    ul.appendChild(li);
  }
  $("list-count").textContent =
    `${state.satellites.length} satellite${state.satellites.length === 1 ? "" : "s"}` +
    (state.query ? ` matching “${state.query}”` : "");
}

function updateChips(source, lastUpdated) {
  const chipSrc = $("chip-source");
  chipSrc.classList.remove("hidden", "chip-demo", "chip-ok");
  if (source === "demo") { chipSrc.textContent = "DEMO DATA"; chipSrc.classList.add("chip-demo"); }
  else if (source === "celestrak") { chipSrc.textContent = "CelesTrak live TLEs"; chipSrc.classList.add("chip-ok"); }
  else chipSrc.classList.add("hidden");

  const chipUpd = $("chip-updated");
  if (lastUpdated) {
    chipUpd.classList.remove("hidden");
    chipUpd.textContent = `Data updated ${fmtTime(lastUpdated)} (${relTime(lastUpdated)})`;
  } else chipUpd.classList.add("hidden");
}

// ---------------- details & positions ----------------

function currentPosTime() {
  const v = $("pos-time").value;
  if (!v) return null; // server defaults to now
  return encodeURIComponent(v + (v.length === 16 ? ":00" : "") + "Z");
}

async function selectSatellite(noradId) {
  state.selectedId = noradId;
  renderList();
  state.markers.forEach((m, k) => m.setIcon(markerIcon(k === noradId)));
  show($("details-placeholder"), false);
  show($("details-body"), true);
  $("details-body").querySelectorAll("dd").forEach((dd) => { dd.textContent = "…"; });

  try {
    const sat = await api(`/api/satellites/${noradId}`);
    const els = sat.orbital_elements;
    $("det-name").textContent = sat.name;
    $("det-norad").textContent = sat.norad_id;
    $("det-objid").textContent = sat.object_id || "—";
    $("det-group").textContent = sat.group_name;
    $("det-source").textContent = sat.source === "demo" ? "DEMO (clearly labeled)" : "CelesTrak";
    $("det-fetched").textContent = fmtTime(sat.fetched_at);
    $("det-inc").textContent = `${els.inclination_deg.toFixed(4)}°`;
    $("det-raan").textContent = `${els.raan_deg.toFixed(4)}°`;
    $("det-ecc").textContent = els.eccentricity.toFixed(7);
    $("det-aop").textContent = `${els.arg_perigee_deg.toFixed(4)}°`;
    $("det-mm").textContent = `${els.mean_motion_revs_day.toFixed(8)} rev/day`;
    $("det-tle").textContent = `${sat.tle_line1}\n${sat.tle_line2}`;
    await refreshPosition(sat);
  } catch (err) {
    $("det-name").textContent = "Error loading satellite";
    $("det-norad").textContent = err.message;
  }
}

async function refreshPosition(satMaybe) {
  if (!state.selectedId) return;
  const sat = satMaybe || (await api(`/api/satellites/${state.selectedId}`).catch(() => null));
  if (!sat) return;
  try {
    const qs = currentPosTime();
    const data = await api(`/api/satellites/${state.selectedId}/position${qs ? `?time=${qs}` : ""}`);
    const p = data.position;
    $("det-epoch").textContent = fmtTime(p.tle_epoch);
    $("det-period").textContent = p.period_minutes ? `${p.period_minutes.toFixed(1)} min` : "—";
    $("det-apo").textContent = p.apogee_km != null ? `${p.apogee_km.toFixed(1)} km` : "—";
    $("det-peri").textContent = p.perigee_km != null ? `${p.perigee_km.toFixed(1)} km` : "—";
    $("det-lat").textContent = `${p.latitude.toFixed(4)}°`;
    $("det-lon").textContent = `${p.longitude.toFixed(4)}°`;
    $("det-alt").textContent = `${p.altitude_km.toFixed(1)} km`;
    $("det-vel").textContent = `${p.velocity_km_s.toFixed(3)} km/s`;

    // Map marker + caption.
    const m = addMarker({ norad_id: sat.norad_id, name: sat.name }, p.latitude, p.longitude);
    m.setPopupContent(`<strong>${sat.name}</strong><br>NORAD ${sat.norad_id}<br>` +
      `alt ${p.altitude_km.toFixed(0)} km · calc at ${fmtTime(p.computed_at)}`);
    $("map-sat-name").textContent = sat.name;
    $("map-sat-meta").textContent =
      `Calculated SGP4 position at ${fmtTime(p.computed_at)} from TLE epoch ${fmtTime(p.tle_epoch)}. ` +
      `Data source: ${data.satellite.source === "demo" ? "DEMO elements (approximate)" : "CelesTrak"} · fetched ${fmtTime(data.satellite.data_fetched_at)}. Not a live observation.`;
    show($("map-time"), true);
    $("map-computed-at").textContent = fmtTime(p.computed_at);
    $("map-tle-epoch").textContent = fmtTime(p.tle_epoch);

    if ($("toggle-track").checked) drawGroundTrack();
    else if (state.trackLine) { map.removeLayer(state.trackLine); state.trackLine = null; }
  } catch (err) {
    $("map-sat-meta").textContent = `Position calculation failed: ${err.message}`;
  }
}

async function drawGroundTrack() {
  if (!state.selectedId) return;
  if (state.trackLine) { map.removeLayer(state.trackLine); state.trackLine = null; }
  try {
    const qs = currentPosTime();
    const data = await api(`/api/satellites/${state.selectedId}/ground-track?points=120${qs ? `&time=${qs}` : ""}`);
    // Split into segments where longitude wraps around the antimeridian.
    const segs = [[]];
    let prev = null;
    for (const pt of data.points) {
      if (prev && Math.abs(pt.lon - prev) > 180) segs.push([]);
      segs[segs.length - 1].push([pt.lat, pt.lon]);
      prev = pt.lon;
    }
    state.trackLine = L.layerGroup(segs
      .filter((s) => s.length > 1)
      .map((s) => L.polyline(s, { color: "#9d7cff", weight: 1.5, opacity: 0.7, dashArray: "4 4" })))
      .addTo(map);
  } catch (err) { console.warn("ground track failed:", err.message); }
}

// ---------------- assistant ----------------

function addChat(role, text, extraHtml) {
  const div = document.createElement("div");
  div.className = `msg ${role === "user" ? "user" : "bot"}`;
  div.textContent = text;
  if (extraHtml) {
    const span = document.createElement("span");
    span.className = "tools";
    span.innerHTML = extraHtml;
    div.appendChild(span);
  }
  $("chat-log").appendChild(div);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  return div;
}

async function sendChat(question) {
  question = question.trim();
  if (!question) return;
  $("chat-input").value = "";
  addChat("user", question);
  const pending = addChat("bot", "Consulting mission data…");
  pending.classList.add("pending");
  try {
    const res = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ question, history: state.chatHistory }),
    });
    pending.remove();
    const toolsNote = res.tool_calls && res.tool_calls.length
      ? `🔧 Grounded via backend tools: ${res.tool_calls.map((t) => t.tool).join(", ")}`
      : (res.disabled ? "" : "⚠ No backend tool data was used for this reply.");
    addChat("bot", res.answer, toolsNote.replace(/&/g, "&amp;").replace(/</g, "&lt;"));
    state.chatHistory.push({ role: "user", content: question });
    state.chatHistory.push({ role: "assistant", content: res.answer });
  } catch (err) {
    pending.remove();
    const div = addChat("bot", `Request failed: ${err.message}`);
    div.classList.add("error");
  }
}

async function loadAssistantStatus() {
  try {
    const st = await api("/api/assistant/status");
    const chip = $("chip-ai");
    if (st.enabled) {
      chip.textContent = `AI: ${st.model}`;
      chip.classList.add("chip-ok");
    } else {
      chip.textContent = "AI assistant disabled — set OPENAI_API_KEY on the server";
      chip.classList.add("chip-warn");
      addChat("bot", "The AI assistant is not enabled yet. Set the OPENAI_API_KEY environment " +
        "variable on the server and restart it (see README). The satellite list, map, positions " +
        "and ground tracks all work without it.");
    }
  } catch { /* status chip stays default */ }
}

// ---------------- events & init ----------------

$("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  state.query = $("search-input").value.trim();
  loadSatellites();
});
let searchTimer;
$("search-input").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = $("search-input").value.trim();
    loadSatellites();
  }, 350);
});
$("group-select").addEventListener("change", () => {
  state.group = $("group-select").value;
  state.selectedId = null;
  clearMarkers();
  loadSatellites();
});
$("btn-refresh").addEventListener("click", async () => {
  $("btn-refresh").disabled = true;
  try {
    await api(`/api/satellites/refresh/${state.group}?force=true`, { method: "POST" });
    await loadSatellites();
  } catch (err) {
    show($("notice"), true);
    $("notice").textContent = err.message;
  } finally {
    $("btn-refresh").disabled = false;
  }
});
$("btn-pos-now").addEventListener("click", () => { $("pos-time").value = ""; refreshPosition(); });
$("pos-time").addEventListener("change", () => refreshPosition());
$("toggle-track").addEventListener("change", () => refreshPosition());

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t === tab);
      t.setAttribute("aria-selected", t === tab ? "true" : "false");
    });
    show($("tab-details"), tab.dataset.tab === "details");
    show($("tab-assistant"), tab.dataset.tab === "assistant");
  });
});

$("chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("chat-input").value); });
$("chat-examples").addEventListener("click", (e) => {
  if (e.target.classList.contains("chip-q")) sendChat(e.target.textContent);
});

(async function init() {
  try {
    await loadGroups();
    await loadSatellites();
  } catch (err) {
    setListState({ error: err.message });
  }
  loadAssistantStatus();
})();
