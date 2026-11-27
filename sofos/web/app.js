// Sofos — interfaz. Todo el texto de mensajes se inserta con textContent (nunca innerHTML).
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const state = {
  devices: [], current: null, tab: "photos", poll: null,
  photos: [], selected: new Set(), lastClicked: null, // fotos
  convs: [], convId: null,                            // mensajes
};
const api = (key, path = "") => `/api/devices/${encodeURIComponent(key)}${path}`;

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new Error(body?.detail || `Error ${res.status}`);
  return body;
}

function showStatus(text, isError = false) {
  const s = $("#status");
  s.hidden = !text;
  s.textContent = text || "";
  s.classList.toggle("error", isError);
}

const fmtTime = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const fmtDay = (t) => new Date(t * 1000).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" });
function fmtShort(t) {
  const d = new Date(t * 1000), now = new Date();
  return d.toDateString() === now.toDateString() ? fmtTime(t) : d.toLocaleDateString();
}

// ---------- Dispositivos ----------

async function loadDevices() {
  try {
    state.devices = await getJSON("/api/devices");
  } catch (e) {
    state.devices = [];
  }
  const list = $("#devices");
  list.replaceChildren();
  if (!state.devices.length) list.append(el("li", "muted", "No hay dispositivos conectados."));
  const icons = { ios: "", android: "🤖", mac: "💻" };
  for (const d of state.devices) {
    const li = el("li", "device" + (state.current?.key === d.key ? " active" : ""));
    const title = el("div", null, `${icons[d.kind] || ""} ${d.name}`);
    if (d.connection) title.append(el("span", "conn", CONNECTION_LABEL[d.connection]));
    li.append(title);
    li.append(el("div", "muted", [d.model, d.os_version].filter(Boolean).join(" · ")));
    if (d.status !== "ok") li.append(el("div", "warn", d.status));
    li.onclick = () => selectDevice(d.key);
    list.append(li);
  }
  if (state.current) {
    const updated = state.devices.find((d) => d.key === state.current.key);
    if (updated) {
      const before = state.current;
      state.current = updated;
      // Se desconectó el cable y sigue por Wi-Fi (o al revés): actualizar pestañas y encabezado.
      if (before.connection !== updated.connection || before.wifi_enabled !== updated.wifi_enabled
          || before.capabilities.join() !== updated.capabilities.join()) {
        refreshDeviceHeader();
        if (before.capabilities.join() !== updated.capabilities.join()) applyTabs(false);
      }
    }
  }
}

const CONNECTION_LABEL = { usb: "🔌 cable", wifi: "📶 Wi-Fi" };

function refreshDeviceHeader() {
  const d = state.current;
  $("#device-name").textContent = d.name;
  const via = { usb: "conectado por cable", wifi: "conectado por Wi-Fi · la pantalla sólo se ve por cable" }[d.connection];
  $("#device-info").textContent = [d.model, d.os_version, via].filter(Boolean).join(" · ");
  const btn = $("#wifi-toggle");
  // Sólo se puede activar/desactivar con el cable puesto.
  btn.hidden = d.kind !== "ios" || d.connection !== "usb" || d.wifi_enabled === null;
  btn.classList.toggle("on", !!d.wifi_enabled);
  btn.textContent = d.wifi_enabled ? "📶 Wi-Fi activado" : "📶 Usar también por Wi-Fi";
  btn.title = d.wifi_enabled
    ? "En la misma red Wi-Fi, este iPhone aparece aunque desconectes el cable. Clic para desactivarlo."
    : "Permite usar este iPhone por Wi-Fi (fotos y mensajes) cuando esté en la misma red que esta Mac.";
}

// Pestañas según lo que el dispositivo puede hacer ahora. `restart`: volver a cargar la pestaña.
function applyTabs(restart = true) {
  const d = state.current;
  // "browse" (ver en el teléfono y elegir) o "photos" (copiar todo) habilitan la pestaña Fotos.
  const caps = d.capabilities.map((c) => (c === "browse" ? "photos" : c));
  document.querySelectorAll("#main-tabs button").forEach((b) => (b.hidden = !caps.includes(b.dataset.tab)));
  const tab = caps.includes(state.tab) ? state.tab : caps[0];
  if (!tab) { document.querySelectorAll(".tab").forEach((t) => (t.hidden = true)); return; }
  if (restart || tab !== state.tab) switchTab(tab);
}

$("#wifi-toggle").onclick = async () => {
  const d = state.current, on = !d.wifi_enabled;
  const btn = $("#wifi-toggle");
  btn.disabled = true;
  try {
    await getJSON(api(d.key, "/wifi"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }),
    });
    d.wifi_enabled = on;
    refreshDeviceHeader();
    showStatus(on ? "Listo: en la misma red Wi-Fi podrás usar este iPhone sin cable (fotos y mensajes)." : "", false);
  } catch (e) {
    showStatus(e.message, true);
  } finally {
    btn.disabled = false;
  }
};

function selectDevice(key) {
  stopScreen();
  state.current = state.devices.find((d) => d.key === key);
  const d = state.current;
  $("#empty").hidden = true;
  $("#device-view").hidden = false;
  refreshDeviceHeader();
  document.querySelectorAll("#devices li.device").forEach((li, i) =>
    li.classList.toggle("active", state.devices[i]?.key === key));
  showStatus(d.status !== "ok" ? d.status : "", false);
  state.convId = null;
  state.fs.root = "media";
  state.fs.path = "/";
  applyTabs();
  if (d.job?.running) watchJob();
}

function switchTab(tab) {
  if (state.tab === "screen" && tab !== "screen") stopScreen();
  state.tab = tab;
  document.querySelectorAll("#main-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab").forEach((t) => (t.hidden = t.id !== `tab-${tab}`));
  // La Mac lee Mensajes directo: no hay nada que "actualizar".
  $("#refresh-messages").hidden = state.current.kind === "mac";
  if (tab === "photos") loadPhotos();
  else if (tab === "files") startFiles();
  else if (tab === "screen") startScreen();
  else loadConversations();
}

// ---------- Tareas largas (copiar fotos / respaldo de mensajes) ----------

async function startJob(kind) {
  const path = kind === "photos" ? "/photos/sync" : "/messages/refresh";
  try {
    const job = await getJSON(api(state.current.key, path), { method: "POST" });
    showStatus(job.message);
    watchJob();
  } catch (e) {
    showStatus(e.message, true);
  }
}

function watchJob() {
  clearInterval(state.poll);
  const key = state.current.key;
  setBusy(true);
  const tick = async () => {
    const job = await getJSON(api(key, "/job")).catch(() => null);
    if (!job || state.current?.key !== key) return;
    state.current.job = job;
    if (job.kind === "transfer") showProgress(job);
    else showStatus(job.message, job.error);
    if (!job.running) {
      clearInterval(state.poll);
      setBusy(false);
      if (job.kind === "transfer" && state.tab === "photos") loadPhotos(); // marca las recién guardadas
      if (job.kind === "photos" && state.tab === "photos") loadPhotos();
      if (job.kind === "messages" && !job.error && state.tab === "messages") loadConversations();
    }
  };
  tick();
  state.poll = setInterval(tick, 700);
}

function setBusy(busy) {
  $("#sync-photos").disabled = busy;
  $("#transfer").disabled = busy || !state.selected.size;
  $("#refresh-messages").disabled = busy;
}

// ---------- Fotos ----------

const canBrowse = () => state.current.capabilities.includes("browse");
const fmtMB = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(b / 1e6))} MB`);
state.photoView = "phone"; // "phone": lo que hay en el teléfono · "saved": sofos/descargas/Fotos
// En la vista del teléfono se puede elegir; en "Guardadas" sólo mirar.
const selecting = () => canBrowse() && state.photoView === "phone";

function photoUrl(p, kind) {
  const q = `${kind}?path=${encodeURIComponent(p.path)}`;
  if (state.photoView === "saved") return `/api/saved/${q}`;
  return api(state.current.key, canBrowse() ? `/phone-photos/${q}` : `/photos/${q}`);
}

function setPhotoView(view) {
  state.photoView = view;
  loadPhotos();
}

async function loadPhotos() {
  const key = state.current.key, view = state.photoView;
  state.selected.clear();
  state.lastClicked = null;
  document.querySelectorAll("#photo-view button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  $("#photo-view").hidden = !canBrowse();
  $("#transfer").hidden = $("#sel-all").hidden = $("#photo-hint").hidden = !selecting();
  $("#hide-saved-wrap").hidden = !selecting();
  $("#saved-open").hidden = view !== "saved";
  $("#sync-photos").hidden = canBrowse();
  $("#photo-count").textContent = view === "saved" ? "Cargando…" : "Leyendo fotos del teléfono…";
  $("#gallery").replaceChildren();
  let data;
  try {
    data = await getJSON(view === "saved" ? "/api/saved"
      : api(key, `${canBrowse() ? "/phone-photos" : "/photos"}?limit=20000`));
  } catch (e) {
    $("#photo-count").textContent = e.message;
    return;
  }
  if (state.current?.key !== key || state.photoView !== view) return;
  state.photos = data.items;
  refreshSavedCount();
  const n = state.photos.length;
  if (view === "saved") {
    $("#photo-count").textContent = n ? `${n} guardadas en sofos/descargas/Fotos` : "Todavía no has guardado fotos.";
  } else if (canBrowse()) {
    const savedN = state.photos.filter((p) => p.saved).length;
    $("#photo-count").textContent = n
      ? `${n} en el teléfono` + (savedN ? ` · ${savedN} ya guardadas` : "")
      : "No hay fotos en el teléfono.";
  } else {
    $("#photo-count").textContent = n ? `${n} copiadas` : "Aún no has copiado fotos.";
  }
  renderGallery();
  updateSelection();
}

async function refreshSavedCount() {
  const data = await getJSON("/api/saved").catch(() => null);
  $("#saved-count").textContent = data?.total ? `(${data.total})` : "";
}

// Fotos visibles según el filtro «Ocultar guardadas».
const visiblePhotos = () =>
  selecting() && $("#hide-saved").checked ? state.photos.filter((p) => !p.saved) : state.photos;

function renderGallery() {
  const gallery = $("#gallery");
  gallery.replaceChildren();
  let grid = null, lastDay = null;
  state.photos.forEach((p, idx) => {
    if (selecting() && $("#hide-saved").checked && p.saved) return;
    const day = p.date ? fmtDay(p.date) : "Sin fecha";
    if (day !== lastDay) {
      lastDay = day;
      const group = el("section", "day-group");
      const head = el("div", "day-head");
      head.append(el("span", null, day));
      if (selecting()) {
        const btn = el("button", null, "Elegir día");
        btn.onclick = () => toggleDay(day);
        head.append(btn);
      }
      grid = el("div", "grid");
      group.append(head, grid);
      gallery.append(group);
    }
    const tile = el("div", "tile" + (selecting() && p.saved ? " is-saved" : ""));
    tile.dataset.idx = idx;
    const img = el("img");
    img.loading = "lazy";
    img.alt = p.name;
    img.src = photoUrl(p, "thumb");
    img.onerror = () => img.classList.add("failed");
    tile.append(img);
    if (p.is_video) tile.append(el("div", "video-badge", "▶ video"));
    if (selecting()) {
      if (p.saved) tile.append(el("div", "saved-badge", "✓ Guardada"));
      tile.append(el("div", "check"));
    }
    tile.title = [p.name, p.date && new Date(p.date * 1000).toLocaleString(), p.size && fmtMB(p.size),
      selecting() && p.saved && "ya está en sofos/descargas"].filter(Boolean).join(" · ");
    tile.onclick = (e) => (selecting() ? clickTile(idx, e.shiftKey) : openViewer(p));
    tile.ondblclick = () => openViewer(p);
    grid.append(tile);
  });
}

function clickTile(idx, shift) {
  const path = state.photos[idx].path;
  if (shift && state.lastClicked !== null) {
    // Shift+clic: aplica al rango el estado de la foto que se acaba de tocar.
    const select = !state.selected.has(path);
    const [a, b] = [state.lastClicked, idx].sort((x, y) => x - y);
    for (let i = a; i <= b; i++) {
      if (select && $("#hide-saved").checked && state.photos[i].saved) continue;
      if (select) state.selected.add(state.photos[i].path);
      else state.selected.delete(state.photos[i].path);
    }
  } else if (state.selected.has(path)) {
    state.selected.delete(path);
  } else {
    state.selected.add(path);
  }
  state.lastClicked = idx;
  updateSelection();
}

function toggleDay(day) {
  // Elegir un día toma sólo las que faltan guardar.
  const ofDay = state.photos.filter((p) => (p.date ? fmtDay(p.date) : "Sin fecha") === day && !p.saved);
  if (!ofDay.length) return;
  const allIn = ofDay.every((p) => state.selected.has(p.path));
  ofDay.forEach((p) => (allIn ? state.selected.delete(p.path) : state.selected.add(p.path)));
  updateSelection();
}

function updateSelection() {
  document.querySelectorAll("#gallery .tile").forEach((t) =>
    t.classList.toggle("selected", state.selected.has(state.photos[t.dataset.idx].path)));
  const n = state.selected.size;
  const bytes = state.photos.filter((p) => state.selected.has(p.path)).reduce((s, p) => s + (p.size || 0), 0);
  $("#sel-info").textContent = n ? `${n} elegidas · ${fmtMB(bytes)}` : "";
  $("#sel-none").hidden = !n;
  $("#transfer").disabled = !n || !!state.current.job?.running;
  $("#transfer").textContent = n ? `Guardar ${n} en la Mac` : "Guardar en la Mac";
}

async function startTransfer() {
  const paths = state.photos.filter((p) => state.selected.has(p.path)).map((p) => p.path);
  try {
    await getJSON(api(state.current.key, "/phone-photos/transfer"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths }),
    });
    watchJob();
  } catch (e) {
    showStatus(e.message, true);
  }
}

function showProgress(job) {
  const box = $("#progress");
  box.hidden = false;
  box.classList.toggle("error", job.error);
  const pct = job.bytes_total ? (100 * job.bytes_done) / job.bytes_total : job.running ? 0 : 100;
  $("#progress-fill").style.width = `${pct}%`;
  let text = job.message;
  if (job.running && job.bytes_done) {
    const secs = Date.now() / 1000 - job.started_at;
    const speed = job.bytes_done / secs;
    const left = (job.bytes_total - job.bytes_done) / speed;
    text += ` · ${fmtMB(speed)}/s · quedan ~${Math.ceil(left)} s`;
  }
  $("#progress-text").textContent = text;
  $("#open-result").hidden = job.running || job.error || !job.result_dir;
}

function openViewer(p) {
  const src = photoUrl(p, "file");
  const media = p.is_video ? el("video") : el("img");
  media.src = src;
  if (p.is_video) { media.controls = true; media.autoplay = true; }
  $("#viewer-content").replaceChildren(media);
  $("#viewer-name").textContent = p.name;
  const dl = $("#viewer-download");
  dl.textContent = p.saved || state.photoView === "saved" ? "Descargar copia" : "Descargar";
  dl.hidden = !canBrowse();
  dl.href = `${src}&download=1`;
  $("#viewer").hidden = false;
}

// ---------- Explorador de archivos (sólo lectura) ----------

state.fs = { roots: [], root: "media", path: "/", entries: [], selected: null };
const BIG_FILE = 50 * 1e6; // más que esto: preguntar antes de traerlo para la vista previa
const fsQuery = (extra) => new URLSearchParams({ root: state.fs.root, ...extra }).toString();
const fileIcon = (e) => {
  if (e.is_dir) return "📁";
  const ext = e.name.split(".").pop().toLowerCase();
  if (["jpg", "jpeg", "png", "heic", "gif", "webp"].includes(ext)) return "🖼️";
  if (["mov", "mp4", "m4v"].includes(ext)) return "🎞️";
  if (["mp3", "m4a", "wav", "aac", "opus", "ogg", "caf", "amr"].includes(ext)) return "🎵";
  if (["sqlite", "sqlitedb", "db", "storedata", "cloudphotodb"].includes(ext)) return "🗄️";
  if (["plist", "json", "xml", "txt", "log"].includes(ext)) return "📄";
  if (ext === "pdf") return "📕";
  return "▫️";
};

async function startFiles() {
  const key = state.current.key;
  const list = $("#fs-roots");
  list.replaceChildren(el("li", "muted", "Cargando…"));
  try {
    state.fs.roots = await getJSON(api(key, "/fs/roots"));
  } catch (e) {
    list.replaceChildren(el("li", "muted", e.message));
    return;
  }
  if (state.current?.key !== key) return;
  if (!state.fs.roots.some((r) => r.id === state.fs.root)) state.fs.root = "media";
  renderRoots();
  openFolder(state.fs.path || "/");
}

function renderRoots() {
  const list = $("#fs-roots");
  list.replaceChildren();
  for (const r of state.fs.roots) {
    const li = el("li", r.id === state.fs.root ? "active" : "", (r.kind === "media" ? "📱 " : "📦 ") + r.name);
    li.title = r.bundle_id || "DCIM, PhotoData, Books, Music…";
    li.onclick = () => { state.fs.root = r.id; renderRoots(); openFolder("/"); };
    list.append(li);
  }
}

async function openFolder(path) {
  const key = state.current.key, root = state.fs.root;
  $("#fs-preview").hidden = true;
  $("#fs-info").textContent = "Cargando…";
  $("#fs-table tbody").replaceChildren();
  let data;
  try {
    data = await getJSON(api(key, `/fs/list?${fsQuery({ path })}`));
  } catch (e) {
    $("#fs-info").textContent = e.message;
    return;
  }
  if (state.current?.key !== key || state.fs.root !== root) return;
  state.fs.path = data.path;
  state.fs.entries = data.entries;
  renderCrumbs();
  const dirs = data.entries.filter((e) => e.is_dir).length;
  const files = data.entries.length - dirs;
  const bytes = data.entries.reduce((s, e) => s + (e.is_dir ? 0 : e.size), 0);
  $("#fs-info").textContent = `${dirs} carpetas · ${files} archivos` + (files ? ` · ${fmtSize(bytes)}` : "");
  const body = $("#fs-table tbody");
  for (const e of data.entries) {
    const tr = el("tr");
    tr.append(el("td", null, `${fileIcon(e)} ${e.name}`),
      el("td", "num", e.is_dir ? "" : fmtSize(e.size)),
      el("td", null, e.date ? new Date(e.date * 1000).toLocaleString() : ""));
    tr.title = e.path;
    tr.onclick = () => (e.is_dir ? openFolder(e.path) : selectFile(e, tr));
    body.append(tr);
  }
}

function fmtSize(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1e6) return `${(b / 1024).toFixed(0)} KB`;
  if (b < 1e9) return `${(b / 1e6).toFixed(1)} MB`;
  return `${(b / 1e9).toFixed(2)} GB`;
}

function renderCrumbs() {
  const nav = $("#fs-crumbs");
  nav.replaceChildren();
  const rootInfo = state.fs.roots.find((r) => r.id === state.fs.root);
  const base = state.fs.root === "media" ? "/" : "/Documents";
  const crumb = (label, path) => {
    const b = el("button", null, label);
    b.onclick = () => openFolder(path);
    return b;
  };
  nav.append(crumb(rootInfo?.name || "Inicio", base));
  const rest = state.fs.path.slice(base.length).split("/").filter(Boolean);
  let acc = base.replace(/\/$/, "");
  for (const part of rest) {
    acc += "/" + part;
    nav.append(el("span", null, "›"), crumb(part, acc));
  }
}

function selectFile(e, row) {
  document.querySelectorAll("#fs-table tbody tr").forEach((r) => r.classList.toggle("active", r === row));
  state.fs.selected = e;
  const pv = $("#fs-preview");
  pv.hidden = false;
  $("#fs-pv-name").textContent = e.name;
  $("#fs-pv-meta").textContent = [fmtSize(e.size), e.date && new Date(e.date * 1000).toLocaleString(), e.path]
    .filter(Boolean).join(" · ");
  $("#fs-pv-download").href = api(state.current.key, `/fs/file?${fsQuery({ path: e.path, download: 1 })}`);
  const body = $("#fs-pv-body");
  body.replaceChildren();
  if (e.size > BIG_FILE) {
    const btn = el("button", "primary", `Traer y ver (${fmtSize(e.size)})`);
    btn.onclick = () => loadPreview(e);
    body.append(el("p", "muted", "Archivo grande: traerlo del iPhone puede tardar."), btn);
  } else {
    loadPreview(e);
  }
}

async function loadPreview(e) {
  const key = state.current.key;
  const body = $("#fs-pv-body");
  body.replaceChildren(el("p", "muted", "Trayendo del iPhone…"));
  let pv;
  try {
    pv = await getJSON(api(key, `/fs/preview?${fsQuery({ path: e.path, size: e.size })}`));
  } catch (err) {
    body.replaceChildren(el("p", "muted", err.message));
    return;
  }
  if (state.fs.selected !== e) return;
  const src = api(key, `/fs/file?${fsQuery({ path: e.path })}`);
  body.replaceChildren();
  if (pv.kind === "image") { const i = el("img"); i.src = src; body.append(i); }
  else if (pv.kind === "video") { const v = el("video"); v.src = src; v.controls = true; body.append(v); }
  else if (pv.kind === "audio") { const a = el("audio"); a.src = src; a.controls = true; body.append(a); }
  else if (pv.kind === "pdf") { const f = el("iframe"); f.src = src; body.append(f); }
  else if (pv.kind === "sqlite") renderSqlite(e, pv.tables);
  else if (pv.kind === "too_big") body.append(el("p", "muted", "Demasiado grande para la vista previa. Usa «Descargar»."));
  else {
    if (pv.kind === "binary") body.append(el("p", "muted", "Archivo binario: primeros 1024 bytes."));
    body.append(el("pre", null, pv.text || ""));
    if (pv.truncated) body.append(el("p", "muted", "(mostrando sólo el comienzo)"));
  }
}

function renderSqlite(e, tables) {
  const body = $("#fs-pv-body");
  body.replaceChildren(el("p", "muted", `Base de datos SQLite · ${tables.length} tablas. Clic en una para ver filas.`));
  const ul = el("ul", "tbl-list");
  for (const t of [...tables].sort((a, b) => (b.rows || 0) - (a.rows || 0))) {
    const li = el("li");
    const left = el("div");
    left.append(el("strong", null, t.name), el("div", "cols", t.columns.join(", ")));
    li.append(left, el("span", "muted", t.rows === null ? "?" : `${t.rows.toLocaleString()} filas`));
    li.onclick = () => showTable(e, t, tables);
    ul.append(li);
  }
  body.append(ul);
}

async function showTable(e, t, tables) {
  const body = $("#fs-pv-body");
  const back = el("button", null, "‹ Tablas");
  back.onclick = () => renderSqlite(e, tables);
  body.replaceChildren(back, el("p", "muted", `Cargando ${t.name}…`));
  let data;
  try {
    data = await getJSON(api(state.current.key, `/fs/sqlite?${fsQuery({ path: e.path, table: t.name, limit: 200 })}`));
  } catch (err) {
    body.replaceChildren(back, el("p", "muted", err.message));
    return;
  }
  const table = el("table", "data-grid");
  const head = el("tr");
  data.columns.forEach((c) => head.append(el("th", null, c)));
  table.append(head);
  for (const row of data.rows) {
    const tr = el("tr");
    row.forEach((v) => { const td = el("td", null, v === null ? "∅" : String(v)); td.title = td.textContent; tr.append(td); });
    table.append(tr);
  }
  const wrap = el("div", "grid-wrap");
  wrap.append(table);
  body.replaceChildren(back, el("p", "muted", `${t.name}: primeras ${data.rows.length} de ${t.rows?.toLocaleString() ?? "?"} filas`), wrap);
}

// ---------- Pantalla en vivo ----------
// Zoom: "fit" (por defecto) ajusta el iPhone a la ventana; un número es la escala
// respecto a los píxeles reales del video (1 = 100 %).

const ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2, 3];
state.screenZoom = "fit";
state.screenMode = "live";

function startScreen() {
  setScreenMode(state.screenMode);
  refreshCapturesCount();
}

function setScreenMode(mode) {
  state.screenMode = mode;
  document.querySelectorAll("#screen-mode button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === mode));
  document.querySelectorAll("#tab-screen .live-only").forEach((e) => (e.hidden = mode !== "live"));
  document.querySelectorAll("#tab-screen .captures-only").forEach((e) => (e.hidden = mode !== "captures"));
  $("#screen-wrap").hidden = mode !== "live";
  $("#captures").hidden = mode !== "captures";
  stopScreen();
  if (mode === "captures") loadCaptures();
}

// La transmisión nunca arranca sola: sólo con «Ver pantalla del iPhone». Mientras transmite,
// el iPhone manda su audio a la Mac y iOS puede preguntar por audífonos.
function startLive() {
  const key = state.current.key;
  const img = $("#screen-img");
  $("#screen-start").hidden = true;
  $("#screen-stop").hidden = false;
  $("#screen-resume").hidden = true;
  img.hidden = true;
  $("#screen-capture").disabled = true;
  $("#screen-status").textContent = "Conectando…";
  // Cada carga abre un stream MJPEG; el servidor lo apaga cuando nadie lo mira.
  img.onload = () => {
    if (img.hidden) { img.hidden = false; applyZoom(); }
    $("#screen-capture").disabled = false;
  };
  img.onerror = () => { img.hidden = true; };
  img.src = api(key, `/screen.mjpg?t=${Date.now()}`);
  clearInterval(state.screenPoll);
  state.screenPoll = setInterval(async () => {
    const st = await getJSON(api(key, "/screen/status")).catch((e) => ({ state: "error", message: e.message }));
    if (state.tab !== "screen" || state.screenMode !== "live" || state.current?.key !== key) return;
    const text = {
      streaming: "En vivo",
      starting: st.message || "Conectando…",
      error: st.message,
      idle: "Detenido",
      paused: st.message,
    }[st.state];
    $("#screen-status").textContent = text || "";
    $("#screen-status").style.color = st.state === "error" ? "var(--error)" : "";
    const watch = { "pidiendo permiso": " · pidiendo permiso de micrófono en la Mac",
                    "sin permiso": " · sin permiso de micrófono: no puedo pausar si el iPhone suena" }[st.sound_watch];
    if (watch && st.state === "streaming") $("#screen-status").textContent += watch;
    if (st.state === "paused") showPaused(key, st.message);
    if (st.state === "error") {
      clearInterval(state.screenPoll);
      img.hidden = true;
      $("#screen-start").hidden = false;
      $("#screen-stop").hidden = true;
    }
  }, 1000);
}

function stopScreen() {
  clearInterval(state.screenPoll);
  const img = $("#screen-img");
  img.removeAttribute("src"); // cierra la conexión; el servidor apaga la captura a los pocos segundos
  img.hidden = true;
  $("#screen-start").hidden = false;
  $("#screen-stop").hidden = true;
  $("#screen-resume").hidden = true;
  $("#screen-capture").disabled = true;
  $("#screen-status").textContent = "";
  $("#screen-status").style.color = "";
}

// Escala con la que se ve ahora (en modo "fit" depende del tamaño de la ventana).
function currentScale() {
  const img = $("#screen-img");
  if (state.screenZoom !== "fit") return state.screenZoom;
  return img.naturalWidth ? img.clientWidth / img.naturalWidth : 1;
}

function applyZoom() {
  const img = $("#screen-img"), wrap = $("#screen-wrap");
  const fit = state.screenZoom === "fit";
  wrap.classList.toggle("fit", fit);
  img.style.width = fit || !img.naturalWidth ? "" : `${Math.round(img.naturalWidth * state.screenZoom)}px`;
  $("#zoom-fit").textContent = fit ? "Ajustado" : `${Math.round(state.screenZoom * 100)} %`;
  $("#zoom-fit").title = fit ? "Ajustado a la ventana" : "Volver a ajustar a la ventana (⌘0)";
}

function zoomBy(direction) {
  const wrap = $("#screen-wrap");
  const now = currentScale();
  const next = direction > 0
    ? ZOOM_STEPS.find((z) => z > now + 0.01) ?? ZOOM_STEPS.at(-1)
    : [...ZOOM_STEPS].reverse().find((z) => z < now - 0.01) ?? ZOOM_STEPS[0];
  // Mantener centrado el mismo punto al cambiar de escala.
  const cx = (wrap.scrollLeft + wrap.clientWidth / 2) / Math.max(wrap.scrollWidth, 1);
  const cy = (wrap.scrollTop + wrap.clientHeight / 2) / Math.max(wrap.scrollHeight, 1);
  state.screenZoom = next;
  applyZoom();
  wrap.scrollLeft = cx * wrap.scrollWidth - wrap.clientWidth / 2;
  wrap.scrollTop = cy * wrap.scrollHeight - wrap.clientHeight / 2;
}

function zoomFit() { state.screenZoom = "fit"; applyZoom(); }

$("#zoom-in").onclick = () => zoomBy(1);
$("#zoom-out").onclick = () => zoomBy(-1);
$("#zoom-fit").onclick = zoomFit;

// Pellizco en el trackpad (llega como rueda con ctrlKey) o ⌘/Ctrl + rueda.
$("#screen-wrap").addEventListener("wheel", (e) => {
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  if (Math.abs(e.deltaY) > 2) zoomBy(e.deltaY < 0 ? 1 : -1);
}, { passive: false });

// ⌘+ / ⌘− / ⌘0 mientras se ve la pantalla.
document.addEventListener("keydown", (e) => {
  if (state.tab !== "screen" || state.screenMode !== "live" || !(e.metaKey || e.ctrlKey)) return;
  if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomBy(1); }
  else if (e.key === "-") { e.preventDefault(); zoomBy(-1); }
  else if (e.key === "0") { e.preventDefault(); zoomFit(); }
});

// Con zoom: arrastrar para moverse.
(() => {
  const wrap = $("#screen-wrap");
  let drag = null;
  wrap.addEventListener("mousedown", (e) => {
    if (state.screenZoom === "fit") return;
    drag = { x: e.clientX, y: e.clientY, left: wrap.scrollLeft, top: wrap.scrollTop };
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!drag) return;
    wrap.scrollLeft = drag.left - (e.clientX - drag.x);
    wrap.scrollTop = drag.top - (e.clientY - drag.y);
  });
  window.addEventListener("mouseup", () => (drag = null));
})();

$("#screen-capture").onclick = async () => {
  try {
    const r = await getJSON(api(state.current.key, "/screen/capture"), { method: "POST" });
    $("#screen-status").textContent = `Captura guardada: ${r.name}`;
    refreshCapturesCount();
  } catch (e) {
    $("#screen-status").textContent = e.message;
  }
};
$("#screen-start-btn").onclick = startLive;

// El iPhone empezó a sonar: el servidor ya soltó la captura (el sonido volvió al iPhone).
// Dejamos la última imagen congelada y ofrecemos reanudar.
function showPaused(key, message) {
  clearInterval(state.screenPoll);
  const img = $("#screen-img");
  img.onerror = null;
  img.src = api(key, `/screen/frame?t=${Date.now()}`);
  img.hidden = false;
  $("#screen-status").textContent = message;
  $("#screen-stop").hidden = true;
  $("#screen-resume").hidden = false;
}
$("#screen-resume").onclick = () => { $("#screen-resume").hidden = true; startLive(); };
$("#screen-stop").onclick = stopScreen;
$("#screen-full").onclick = () => $("#screen-img").requestFullscreen?.();
document.querySelectorAll("#screen-mode button").forEach((b) => (b.onclick = () => setScreenMode(b.dataset.mode)));

// ---------- Capturas guardadas ----------

async function refreshCapturesCount() {
  const list = await getJSON("/api/captures").catch(() => []);
  $("#captures-count").textContent = list.length ? `(${list.length})` : "";
  return list;
}

async function loadCaptures() {
  const box = $("#captures");
  const list = await refreshCapturesCount();
  box.replaceChildren();
  if (!list.length) {
    box.append(el("p", "muted", "Aún no hay capturas. En «En vivo», toca «Guardar captura»."));
    return;
  }
  for (const c of list) {
    const item = el("div", "capture");
    const img = el("img");
    img.loading = "lazy";
    img.alt = c.name;
    img.src = `/api/captures/thumb?name=${encodeURIComponent(c.name)}`;
    item.append(img, el("div", null, new Date(c.date * 1000).toLocaleString()));
    item.onclick = () => openCapture(c);
    box.append(item);
  }
}

function openCapture(c) {
  const src = `/api/captures/file?name=${encodeURIComponent(c.name)}`;
  const img = el("img");
  img.src = src;
  $("#viewer-content").replaceChildren(img);
  $("#viewer-name").textContent = c.name;
  const dl = $("#viewer-download");
  dl.hidden = false;
  dl.textContent = "Descargar";
  dl.href = `${src}&download=1`;
  $("#viewer").hidden = false;
}

$("#captures-open").onclick = () => fetch("/api/captures/open", { method: "POST" });

// ---------- Mensajes ----------

async function loadConversations() {
  const list = $("#conversations");
  list.replaceChildren(el("li", "muted", "Cargando…"));
  try {
    state.convs = await getJSON(api(state.current.key, "/conversations"));
    renderConversations();
  } catch (e) {
    list.replaceChildren(el("li", "muted", e.message));
  }
}

function renderConversations() {
  const q = $("#conv-search").value.trim().toLowerCase();
  const list = $("#conversations");
  list.replaceChildren();
  for (const c of state.convs) {
    if (q && !c.title.toLowerCase().includes(q) && !c.last_message.toLowerCase().includes(q)) continue;
    const li = el("li", c.id === state.convId ? "active" : "");
    const top = el("div", "conv-top");
    top.append(el("span", "conv-title", c.title), el("span", "muted", fmtShort(c.last_date)));
    li.append(top, el("div", "conv-preview", c.last_message));
    li.onclick = () => openConversation(c);
    list.append(li);
  }
}

async function openConversation(c) {
  state.convId = c.id;
  renderConversations();
  $("#thread-title").textContent = c.title;
  const thread = $("#thread");
  thread.replaceChildren(el("div", "muted", "Cargando…"));
  const msgs = await getJSON(api(state.current.key, `/conversations/${encodeURIComponent(c.id)}/messages?limit=500`));
  thread.replaceChildren();
  const isGroup = new Set(msgs.filter((m) => !m.from_me).map((m) => m.sender)).size > 1;
  let lastDay = "", lastSender = "";
  for (const m of msgs) {
    const day = fmtDay(m.date);
    if (day !== lastDay) { thread.append(el("div", "day", day)); lastDay = day; lastSender = ""; }
    if (isGroup && !m.from_me && m.sender !== lastSender) thread.append(el("div", "sender", m.sender));
    lastSender = m.sender;
    const b = el("div", "bubble" + (m.from_me ? " me" : ""), m.text);
    b.title = fmtTime(m.date);
    thread.append(b);
  }
  thread.scrollTop = thread.scrollHeight;
}

// ---------- Eventos ----------

$("#reload").onclick = loadDevices;
$("#sync-photos").onclick = () => startJob("photos");
$("#refresh-messages").onclick = () => startJob("messages");
$("#transfer").onclick = startTransfer;
$("#sel-all").onclick = () => {
  state.photos.filter((p) => !p.saved).forEach((p) => state.selected.add(p.path));
  updateSelection();
};
$("#hide-saved").onchange = () => { renderGallery(); updateSelection(); };
document.querySelectorAll("#photo-view button").forEach((b) => (b.onclick = () => setPhotoView(b.dataset.view)));
$("#saved-open").onclick = () => fetch("/api/saved/open", { method: "POST" });
$("#sel-none").onclick = () => { state.selected.clear(); updateSelection(); };
$("#open-result").onclick = () => fetch("/api/saved/open", { method: "POST" });
$("#conv-search").oninput = renderConversations;
$("#viewer-close").onclick = () => { $("#viewer").hidden = true; $("#viewer-content").replaceChildren(); };
document.querySelectorAll("#main-tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#viewer-close").click(); });

loadDevices();
setInterval(loadDevices, 5000); // detecta cuando conectas/desconectas el cable
