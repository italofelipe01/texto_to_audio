/* Texto para Áudio — front-end (vanilla JS, sem build) */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const STORE_KEY = "tta:form:v1";
const DRAFT_KEY = "tta:draft:v1";
const THEME_KEY = "tta:theme";
const TERMINAL = new Set(["done", "error", "cancelled"]);

const FILE_LABELS = {
  mp3: "Áudio MP3", m4b: "Audiobook M4B", m4a: "Áudio AAC (M4A)", opus: "Áudio Opus", flac: "Áudio FLAC",
  wav: "Áudio WAV", video: "Vídeo MP4", srt: "Legendas SRT", vtt: "Legendas WebVTT",
  transcript: "Transcrição (JSON)", waveform: "Forma de onda (PNG)", peaks: "Picos da onda (JSON)",
  cover: "Capa (JPG)", report: "Relatório técnico (JSON)",
};
const STATUS_LABELS = { queued: "Na fila", running: "Processando", done: "Concluído", error: "Falhou", cancelled: "Cancelado" };
const AUDIO_ORDER = ["mp3", "m4a", "m4b", "opus", "flac", "wav"];

const state = {
  caps: null,
  defaults: {},
  preset: "padrao",
  voices: [],
  job: null,
  events: null,
  poll: null,
  peaks: [],
  sentences: [],
  chapters: [],
  currentSentence: -1,
  file: null,
};

/* ------------------------------------------------------------------ utils */

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data && data.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res;
}

function fmtTime(seconds) {
  if (!isFinite(seconds) || seconds < 0) seconds = 0;
  const s = Math.floor(seconds % 60), m = Math.floor(seconds / 60) % 60, h = Math.floor(seconds / 3600);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

function fmtDuration(seconds) {
  if (!seconds) return "0 s";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
  if (m < 60) return `${m} min${s ? ` ${s} s` : ""}`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

function fmtBytes(bytes) {
  if (bytes == null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toLocaleString("pt-BR", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }); }
  catch (_) { return iso || ""; }
}

function num(value, digits = 1) {
  return Number(value).toLocaleString("pt-BR", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

let toastTimer;
function toast(message, kind = "info") {
  const box = $("#toast");
  box.textContent = message;
  box.className = `toast ${kind === "error" ? "error" : ""}`;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, kind === "error" ? 7000 : 3500);
}

function safeStorage(fn, fallback = null) {
  try { return fn(); } catch (_) { return fallback; }
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/* ------------------------------------------------------------------ theme */

function initTheme() {
  const saved = safeStorage(() => localStorage.getItem(THEME_KEY));
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-toggle").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    safeStorage(() => localStorage.setItem(THEME_KEY, next));
    drawWave();
  });
}

/* ------------------------------------------------------------------ capabilities */

function renderStatus(caps) {
  const box = $("#status");
  box.replaceChildren();
  const ff = caps.ffmpeg;
  box.append(el("span", { class: `pill ${ff.available ? "ok" : "bad"}`, title: ff.available ? "FFmpeg disponível" : "Instale o FFmpeg" },
    ff.available ? `FFmpeg ${String(ff.version).split("-")[0]}` : "FFmpeg ausente"));
  for (const engine of caps.engines) {
    box.append(el("span", { class: `pill ${engine.available ? "ok" : "bad"}`, title: `${engine.label}: ${engine.reason}` },
      engine.name === "edge" ? "Edge" : engine.name === "gtts" ? "gTTS" : engine.name === "piper" ? "Piper" : engine.name === "espeak" ? "eSpeak" : engine.name));
  }
  $("#version").textContent = `v${caps.version}`;
}

function renderPresets(presets) {
  const box = $("#presets");
  box.replaceChildren();
  for (const preset of presets) {
    const btn = el("button", {
      type: "button", class: "preset", role: "radio", "aria-checked": String(preset.name === state.preset),
      dataset: { preset: preset.name }, title: preset.description,
      onclick: () => selectPreset(preset.name, true),
    }, el("strong", {}, preset.label), el("span", {}, preset.description));
    box.append(btn);
  }
}

function renderFormats(formats) {
  const box = $("#formats");
  box.replaceChildren();
  for (const fmt of formats) {
    box.append(el("label", { title: fmt.available ? fmt.label : "Encoder indisponível neste FFmpeg" },
      el("input", { type: "checkbox", value: fmt.key, disabled: !fmt.available, name: "formats" }),
      fmt.key.toUpperCase()));
  }
}

function renderLanguages(languages) {
  const select = $("#lang");
  select.replaceChildren(...languages.map((l) => el("option", { value: l.code }, l.label)));
}

function renderEngines(engines) {
  const select = $("#engine");
  select.replaceChildren(el("option", { value: "auto" }, "Automático (melhor disponível)"));
  for (const engine of engines) {
    select.append(el("option", { value: engine.name, disabled: !engine.available, title: engine.reason },
      `${engine.label}${engine.available ? "" : " — indisponível"}`));
  }
}

function engineHint() {
  const value = $("#engine").value;
  const caps = state.caps;
  if (!caps) return;
  const available = caps.engines.filter((e) => e.available);
  let hint;
  if (value === "auto") {
    hint = available.length
      ? `Ordem automática: ${available.map((e) => e.label).join(" → ")}. Se um motor falhar (ex.: sem internet), o próximo assume sozinho.`
      : "Nenhum motor de voz disponível. Rode “tta doctor” no servidor.";
  } else {
    const engine = caps.engines.find((e) => e.name === value);
    hint = engine ? `${engine.description}` : "";
  }
  $("#engine-hint").textContent = hint;
}

async function loadVoices() {
  const engine = $("#engine").value;
  const lang = $("#lang").value;
  const select = $("#voice");
  const previous = select.value || select.dataset.wanted || "";
  select.disabled = true;
  try {
    const data = await api(`/api/voices?engine=${encodeURIComponent(engine)}&lang=${encodeURIComponent(lang)}`);
    state.voices = data.voices;
  } catch (err) {
    state.voices = [];
    toast(`Não foi possível listar as vozes: ${err.message}`, "error");
  }
  select.replaceChildren(el("option", { value: "" }, "Padrão recomendado"));
  const groups = {};
  for (const voice of state.voices) (groups[voice.engine] ||= []).push(voice);
  const labels = Object.fromEntries((state.caps?.engines || []).map((e) => [e.name, e.label]));
  for (const [engineName, voices] of Object.entries(groups)) {
    const group = el("optgroup", { label: labels[engineName] || engineName });
    for (const voice of voices) {
      const gender = voice.gender === "Female" ? "feminina" : voice.gender === "Male" ? "masculina" : "";
      const extra = [voice.locale, gender, voice.installed ? "" : "baixa no 1º uso"].filter(Boolean).join(" · ");
      group.append(el("option", { value: voice.id }, `${voice.name} (${extra})`));
    }
    select.append(group);
  }
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
  delete select.dataset.wanted;
  select.disabled = false;
}

/* ------------------------------------------------------------------ form state */

const SCALAR_FIELDS = [
  "lang", "engine", "rate", "pitch", "loudness", "true_peak", "pause_sentence", "pause_paragraph",
  "pause_section", "music_gain_db", "video_style", "artist", "album",
];
const BOOL_FIELDS = ["enhance", "trim_silence", "ducking", "burn_subtitles", "subtitles", "chapters", "waveform"];

function applyOptions(options) {
  for (const key of SCALAR_FIELDS) {
    const input = $(`#${key}`);
    if (!input || !(key in options)) continue;
    let value = options[key];
    if (key === "loudness") value = value == null ? "off" : String(Math.round(value));
    if (key === "true_peak") {
      const opts = [...input.options].map((o) => Number(o.value));
      value = String(opts.reduce((a, b) => (Math.abs(b - value) < Math.abs(a - value) ? b : a)));
    }
    input.value = value ?? "";
  }
  for (const key of BOOL_FIELDS) if (key in options) $(`#${key}`).checked = Boolean(options[key]);
  if ("formats" in options) {
    for (const box of $$("#formats input")) box.checked = options.formats.includes(box.value) && !box.disabled;
  }
  if ("video" in options) {
    const radio = $(`#video-choice input[value="${options.video || ""}"]`);
    if (radio) radio.checked = true;
  }
  if ("voice" in options) {
    const select = $("#voice");
    select.dataset.wanted = options.voice || "";
    if ([...select.options].some((o) => o.value === (options.voice || ""))) select.value = options.voice || "";
  }
  updateOutputs();
}

function selectPreset(name, apply) {
  state.preset = name;
  for (const btn of $$("#presets .preset")) btn.setAttribute("aria-checked", String(btn.dataset.preset === name));
  if (apply) {
    const preset = state.caps.presets.find((p) => p.name === name);
    const keep = { lang: $("#lang").value, engine: $("#engine").value, voice: $("#voice").value, artist: $("#artist").value, album: $("#album").value };
    applyOptions({ ...state.defaults, ...(preset ? preset.options : {}), ...keep });
    if (preset && preset.options.video) $("#video-card").open = true;
    saveForm();
  }
}

function collectOptions() {
  const options = { preset: state.preset };
  for (const key of SCALAR_FIELDS) {
    const value = $(`#${key}`).value;
    if (["rate", "pitch", "pause_sentence", "pause_paragraph", "pause_section", "music_gain_db", "true_peak"].includes(key)) {
      options[key] = Number(value);
    } else if (key === "loudness") {
      options.loudness = value === "off" ? "off" : Number(value);
    } else if (value !== "") {
      options[key] = value;
    }
  }
  for (const key of BOOL_FIELDS) options[key] = $(`#${key}`).checked;
  options.formats = $$("#formats input:checked").map((i) => i.value);
  options.video = ($("#video-choice input:checked") || {}).value || null;
  options.voice = $("#voice").value || null;
  const title = $("#title").value.trim();
  if (title) options.title = title;
  return options;
}

const saveForm = debounce(() => {
  const data = collectOptions();
  delete data.title;
  data.lexicon = $("#lexicon").value;
  safeStorage(() => localStorage.setItem(STORE_KEY, JSON.stringify(data)));
}, 300);

const saveDraft = debounce(() => safeStorage(() => localStorage.setItem(DRAFT_KEY, $("#text").value)), 500);

function restoreForm() {
  const saved = safeStorage(() => JSON.parse(localStorage.getItem(STORE_KEY) || "null"));
  const draft = safeStorage(() => localStorage.getItem(DRAFT_KEY));
  if (draft) $("#text").value = draft;
  if (saved) {
    state.preset = saved.preset || "padrao";
    selectPreset(state.preset, false);
    applyOptions({ ...state.defaults, ...saved });
    $("#lexicon").value = saved.lexicon || "";
  } else {
    selectPreset("padrao", true);
  }
}

function updateOutputs() {
  const rate = Number($("#rate").value);
  $("#rate-out").textContent = rate === 0 ? "normal" : `${rate > 0 ? "+" : ""}${rate}%`;
  const pitch = Number($("#pitch").value);
  $("#pitch-out").textContent = pitch === 0 ? "original" : `${pitch > 0 ? "+" : ""}${num(pitch, pitch % 1 ? 1 : 0)} semitom${Math.abs(pitch) === 1 ? "" : "s"}`;
  for (const key of ["pause_sentence", "pause_paragraph", "pause_section"]) {
    $(`#${key}-out`).textContent = `${num($(`#${key}`).value, 2)} s`;
  }
  $("#music_gain_db-out").textContent = `${$("#music_gain_db").value} dB`;
  const formats = $$("#formats input:checked").map((i) => i.value.toUpperCase());
  const loud = $("#loudness").value;
  $("#post-summary").textContent = `${formats.join(", ") || "MP3"} · ${loud === "off" ? "sem normalização" : `${loud} LUFS`}`;
  const video = ($("#video-choice input:checked") || {}).value;
  $("#video-summary").textContent = video ? { landscape: "MP4 16:9", portrait: "MP4 9:16", square: "MP4 1:1" }[video] : "sem vídeo";
  updateTextStats();
}

function updateTextStats() {
  const text = $("#text").value;
  const words = (text.match(/\S+/g) || []).length;
  const rate = Number($("#rate").value) || 0;
  const seconds = (words / 155) * 60 / Math.max(0.3, 1 + rate / 100);
  $("#text-stats").textContent = `${text.length.toLocaleString("pt-BR")} caracteres · ${words.toLocaleString("pt-BR")} palavras · ~${fmtDuration(seconds)} de áudio`;
}

/* ------------------------------------------------------------------ content tabs / file */

function setTab(name) {
  for (const tab of $$(".tabs [role=tab]")) {
    const selected = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  $("#panel-text").hidden = name !== "text";
  $("#panel-file").hidden = name !== "file";
}

function setFile(file) {
  state.file = file || null;
  $("#file-name").textContent = file ? `${file.name} · ${fmtBytes(file.size)}` : "";
  if (file && !$("#title").value) {
    $("#title").placeholder = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ");
  }
}

function initContent() {
  for (const tab of $$(".tabs [role=tab]")) {
    tab.addEventListener("click", () => setTab(tab.dataset.tab));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const next = tab.dataset.tab === "text" ? "file" : "text";
        setTab(next);
        $(`.tabs [data-tab="${next}"]`).focus();
      }
    });
  }
  const zone = $("#dropzone");
  const input = $("#file");
  input.addEventListener("change", () => setFile(input.files[0]));
  for (const evt of ["dragenter", "dragover"]) zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add("drag"); });
  for (const evt of ["dragleave", "drop"]) zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove("drag"); });
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) setFile(file);
  });
  document.addEventListener("dragover", (e) => e.preventDefault());
  document.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = e.dataTransfer && e.dataTransfer.files[0];
    if (file) { setTab("file"); setFile(file); }
  });
  $("#text").addEventListener("input", () => { updateTextStats(); saveDraft(); });
  $("#clear-text").addEventListener("click", () => { $("#text").value = ""; updateTextStats(); saveDraft(); $("#text").focus(); });
}

/* ------------------------------------------------------------------ preview */

async function preview() {
  const btn = $("#preview-btn");
  const status = $("#preview-status");
  btn.disabled = true;
  status.textContent = "Gerando amostra…";
  try {
    const sample = $("#text").value.trim().split(/(?<=[.!?])\s+/)[0]?.slice(0, 300) || null;
    const res = await api("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        engine: $("#engine").value, voice: $("#voice").value || null, lang: $("#lang").value,
        rate: Number($("#rate").value), pitch: Number($("#pitch").value), text: sample,
      }),
    });
    const blob = await res.blob();
    const audio = new Audio(URL.createObjectURL(blob));
    const voice = res.headers.get("X-TTA-Voice");
    status.textContent = voice ? `Voz: ${voice}` : "";
    await audio.play();
  } catch (err) {
    status.textContent = "";
    toast(`Amostra falhou: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------------------ submit */

async function submit(event) {
  if (event) event.preventDefault();
  const usingFile = !$("#panel-file").hidden;
  const text = $("#text").value.trim();
  if (usingFile && !state.file) { toast("Escolha um arquivo para converter.", "error"); return; }
  if (!usingFile && !text) { toast("Digite ou cole um texto primeiro.", "error"); $("#text").focus(); return; }
  const options = collectOptions();
  if (!options.formats.length) options.formats = ["mp3"];
  const form = new FormData();
  form.append("options", JSON.stringify(options));
  if (usingFile) form.append("file", state.file);
  else form.append("text", text);
  const lexicon = $("#lexicon").value.trim();
  if (lexicon) form.append("lexicon", lexicon);
  for (const key of ["music", "intro", "outro", "cover", "background"]) {
    const input = $(`#${key}`);
    if (input.files && input.files[0]) form.append(key, input.files[0]);
  }
  const btn = $("#submit-btn");
  btn.disabled = true;
  try {
    const job = await api("/api/jobs", { method: "POST", body: form });
    openJob(job.id, job);
    loadHistory();
    if (matchMedia("(max-width: 960px)").matches) $("#job-card").scrollIntoView({ behavior: "smooth" });
  } catch (err) {
    toast(err.message, "error");
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------------------ job view */

function stopTracking() {
  if (state.events) { state.events.close(); state.events = null; }
  if (state.poll) { clearInterval(state.poll); state.poll = null; }
}

function openJob(id, initial) {
  stopTracking();
  state.job = initial || { id, status: "queued", progress: 0, stage: "Carregando…" };
  resetResult();
  renderJob(state.job);
  history.replaceState(null, "", `#job=${id}`);
  markActiveHistory(id);
  if (initial && TERMINAL.has(initial.status)) return;
  if ("EventSource" in window) {
    const source = new EventSource(`/api/jobs/${id}/events`);
    state.events = source;
    source.onmessage = (e) => {
      const job = JSON.parse(e.data);
      renderJob(job);
      if (TERMINAL.has(job.status)) { source.close(); state.events = null; loadHistory(); }
    };
    source.addEventListener("deleted", () => { source.close(); $("#job-card").hidden = true; });
    source.onerror = () => { source.close(); state.events = null; startPolling(id); };
  } else {
    startPolling(id);
  }
}

function startPolling(id) {
  if (state.poll) return;
  state.poll = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${id}`);
      renderJob(job);
      if (TERMINAL.has(job.status)) { clearInterval(state.poll); state.poll = null; loadHistory(); }
    } catch (_) { clearInterval(state.poll); state.poll = null; }
  }, 1500);
}

function renderJob(job) {
  const previous = state.job;
  state.job = job;
  const card = $("#job-card");
  card.hidden = false;
  card.classList.toggle("running", job.status === "running");
  $("#job-title").textContent = job.title || job.source_name || "Novo áudio";
  const badge = $("#job-badge");
  badge.textContent = STATUS_LABELS[job.status] || job.status;
  badge.className = `badge ${job.status}`;
  const pct = Math.round((job.progress || 0) * 100);
  $("#job-bar").style.width = `${pct}%`;
  $(".progress", card).setAttribute("aria-valuenow", String(pct));
  $("#job-stage").textContent = job.status === "running" ? `${job.stage} · ${pct}%` : job.stage || "";
  $("#job-progress").hidden = job.status === "done";
  $("#cancel-btn").hidden = TERMINAL.has(job.status);
  const error = $("#job-error");
  error.hidden = !job.error;
  error.textContent = job.error || "";
  if (job.status === "done" && (!previous || previous.id !== job.id || previous.status !== "done" || $("#job-result").hidden)) {
    showResult(job);
  }
}

function fileUrl(job, kind, download = false) {
  const name = job.files[kind];
  return name ? `/api/jobs/${job.id}/files/${encodeURIComponent(name)}${download ? "?download=true" : ""}` : null;
}

function resetResult() {
  const audio = $("#audio");
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  $("#player").classList.remove("playing");
  $("#job-result").hidden = true;
  $("#video").hidden = true;
  $("#video").removeAttribute("src");
  state.peaks = [];
  state.sentences = [];
  state.chapters = [];
  state.currentSentence = -1;
}

async function showResult(job) {
  $("#job-result").hidden = false;
  const audioKind = AUDIO_ORDER.find((k) => job.files[k]);
  const audio = $("#audio");
  audio.src = fileUrl(job, audioKind);
  audio.playbackRate = Number($("#speed").value);
  $("#time-total").textContent = fmtTime(job.report?.duration || 0);

  // downloads
  const list = $("#downloads");
  list.replaceChildren();
  for (const [kind, name] of Object.entries(job.files)) {
    if (kind === "peaks") continue;
    list.append(el("li", {}, el("a", { href: fileUrl(job, kind, true), download: name },
      el("span", { class: "kind" }, FILE_LABELS[kind] || kind),
      el("span", { class: "size" }, fmtBytes(job.sizes?.[kind])))));
  }
  $("#zip-link").href = `/api/jobs/${job.id}/zip`;
  const video = $("#video");
  if (job.files.video) {
    video.src = fileUrl(job, "video");
    video.hidden = false;
  }
  renderReport(job);

  const [peaks, transcript] = await Promise.all([
    job.files.peaks ? fetch(fileUrl(job, "peaks")).then((r) => r.json()).catch(() => []) : [],
    job.files.transcript ? fetch(fileUrl(job, "transcript")).then((r) => r.json()).catch(() => null) : null,
  ]);
  if (state.job.id !== job.id) return;
  state.peaks = peaks || [];
  drawWave();
  renderTranscript(transcript);
}

function renderTranscript(data) {
  const box = $("#transcript");
  box.replaceChildren();
  state.sentences = data?.sentences || [];
  state.chapters = data?.chapters || [];
  if (!state.sentences.length) {
    box.append(el("p", { class: "muted" }, "Transcrição indisponível."));
  }
  state.sentences.forEach((s, i) => {
    const node = s.title
      ? el("strong", { dataset: { i: String(i) } }, s.text)
      : el("span", { dataset: { i: String(i) } }, s.text);
    node.addEventListener("click", () => seek(s.start, true));
    box.append(node, s.title ? "" : " ");
  });
  const chapters = $("#chapters");
  chapters.replaceChildren();
  chapters.hidden = state.chapters.length < 2;
  state.chapters.forEach((c, i) => {
    chapters.append(el("li", {}, el("button", { type: "button", dataset: { c: String(i) }, title: `${c.title} · ${fmtTime(c.start)}`, onclick: () => seek(c.start, true) },
      `${fmtTime(c.start)} ${c.title}`)));
  });
}

function renderReport(job) {
  const r = job.report || {};
  const dl = $("#report");
  dl.replaceChildren();
  const add = (label, value, cls) => {
    if (value == null || value === "") return;
    dl.append(el("dt", {}, label), el("dd", { class: cls || null }, value));
  };
  const loud = r.loudness || {};
  add("Duração", fmtDuration(r.duration));
  add("Voz", r.voice ? `${r.voice} (${r.engine_label})` : null);
  if (r.fallbacks && r.fallbacks.length) add("Motores que falharam", r.fallbacks.join(" · "), "warn");
  if (loud.integrated != null) {
    const ok = loud.target == null || Math.abs(loud.integrated - loud.target) <= 1;
    add("Loudness integrada", `${num(loud.integrated)} LUFS${loud.target != null ? ` (alvo ${num(loud.target)})` : ""}`, ok ? "good" : "warn");
  }
  if (loud.true_peak != null) {
    add("Pico real (true peak)", `${num(loud.true_peak)} dBTP (limite ${num(loud.true_peak_target)})`, loud.true_peak <= loud.true_peak_target + 0.3 ? "good" : "warn");
  }
  if (loud.lra != null) add("Faixa dinâmica (LRA)", `${num(loud.lra)} LU`);
  add("Áudio", r.sample_rate ? `${(r.sample_rate / 1000).toLocaleString("pt-BR")} kHz · ${r.channels === 2 ? "estéreo" : "mono"}` : null);
  add("Texto", r.words != null ? `${r.words.toLocaleString("pt-BR")} palavras · ${r.characters.toLocaleString("pt-BR")} caracteres` : null);
  add("Trechos sintetizados", r.chunks != null ? `${r.chunks} (${r.cache_hits} do cache)` : null);
  add("Capítulos / legendas", r.chunks != null ? `${r.chapters} capítulos · ${r.subtitle_cues} legendas` : null);
  add("Tempo de processamento", r.elapsed != null ? fmtDuration(r.elapsed) : null);
  add("FFmpeg", r.ffmpeg);
}

/* ------------------------------------------------------------------ player */

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawWave() {
  const canvas = $("#wave");
  if (!canvas || $("#job-result").hidden) return;
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext("2d");
  ctx.scale(ratio, ratio);
  ctx.clearRect(0, 0, width, height);
  const audio = $("#audio");
  const duration = audio.duration || state.job?.report?.duration || 0;
  const progress = duration ? audio.currentTime / duration : 0;
  const bar = 3, gap = 1.5;
  const count = Math.max(1, Math.floor(width / (bar + gap)));
  const peaks = state.peaks.length ? state.peaks : new Array(count).fill(0.08);
  const base = cssVar("--wave"), played = cssVar("--wave-played");
  for (let i = 0; i < count; i++) {
    const start = Math.floor((i / count) * peaks.length);
    const end = Math.max(start + 1, Math.floor(((i + 1) / count) * peaks.length));
    let peak = 0;
    for (let j = start; j < end; j++) peak = Math.max(peak, peaks[j] || 0);
    const h = Math.max(2, Math.sqrt(peak) * (height - 4));
    ctx.fillStyle = i / count <= progress ? played : base;
    ctx.fillRect(i * (bar + gap), (height - h) / 2, bar, h);
  }
  canvas.setAttribute("aria-valuenow", String(Math.round(progress * 100)));
  canvas.setAttribute("aria-valuetext", `${fmtTime(audio.currentTime)} de ${fmtTime(duration)}`);
}

function seek(seconds, play = false) {
  const audio = $("#audio");
  if (!audio.src) return;
  audio.currentTime = Math.max(0, seconds);
  if (play) audio.play();
  drawWave();
}

function syncTranscript() {
  const t = $("#audio").currentTime;
  const list = state.sentences;
  let lo = 0, hi = list.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (list[mid].start <= t) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (found !== state.currentSentence) {
    const box = $("#transcript");
    const old = box.querySelector(".current");
    if (old) old.classList.remove("current");
    state.currentSentence = found;
    if (found >= 0) {
      const node = box.querySelector(`[data-i="${found}"]`);
      if (node) {
        node.classList.add("current");
        if ($("#follow").checked && !$("[data-rpanel=transcript]").hidden) {
          const top = node.offsetTop - box.offsetTop - box.clientHeight / 3;
          box.scrollTo({ top, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
        }
      }
    }
  }
  let chapter = -1;
  state.chapters.forEach((c, i) => { if (c.start <= t) chapter = i; });
  $$("#chapters button").forEach((b) => b.classList.toggle("active", Number(b.dataset.c) === chapter));
}

function initPlayer() {
  const audio = $("#audio");
  const canvas = $("#wave");
  $("#play-btn").addEventListener("click", () => (audio.paused ? audio.play() : audio.pause()));
  audio.addEventListener("play", () => { $("#player").classList.add("playing"); $("#play-btn").setAttribute("aria-label", "Pausar"); });
  audio.addEventListener("pause", () => { $("#player").classList.remove("playing"); $("#play-btn").setAttribute("aria-label", "Reproduzir"); });
  audio.addEventListener("loadedmetadata", () => { $("#time-total").textContent = fmtTime(audio.duration); drawWave(); });
  audio.addEventListener("timeupdate", () => { $("#time-current").textContent = fmtTime(audio.currentTime); drawWave(); syncTranscript(); });
  audio.addEventListener("ended", () => { $("#player").classList.remove("playing"); });
  $("#speed").addEventListener("change", (e) => { audio.playbackRate = Number(e.target.value); });
  let dragging = false;
  const seekFromEvent = (e) => {
    const rect = canvas.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    if (audio.duration) seek(fraction * audio.duration);
  };
  canvas.addEventListener("pointerdown", (e) => { dragging = true; canvas.setPointerCapture(e.pointerId); seekFromEvent(e); });
  canvas.addEventListener("pointermove", (e) => { if (dragging) seekFromEvent(e); });
  canvas.addEventListener("pointerup", () => { dragging = false; });
  canvas.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { seek(audio.currentTime + 5); e.preventDefault(); }
    if (e.key === "ArrowLeft") { seek(audio.currentTime - 5); e.preventDefault(); }
  });
  new ResizeObserver(() => drawWave()).observe(canvas);
  for (const tab of $$(".result-tabs [role=tab]")) {
    tab.addEventListener("click", () => {
      for (const other of $$(".result-tabs [role=tab]")) {
        const selected = other === tab;
        other.setAttribute("aria-selected", String(selected));
        other.tabIndex = selected ? 0 : -1;
      }
      for (const panel of $$("[data-rpanel]")) panel.hidden = panel.dataset.rpanel !== tab.dataset.rtab;
    });
  }
  $("#cancel-btn").addEventListener("click", async () => {
    if (!state.job) return;
    try { await api(`/api/jobs/${state.job.id}/cancel`, { method: "POST" }); toast("Cancelando…"); }
    catch (err) { toast(err.message, "error"); }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { submit(e); return; }
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
    if (e.key === " " && !typing && !$("#job-result").hidden && document.activeElement?.tagName !== "BUTTON") {
      e.preventDefault();
      audio.paused ? audio.play() : audio.pause();
    }
  });
}

/* ------------------------------------------------------------------ history */

function markActiveHistory(id) {
  for (const btn of $$("#history .open")) btn.classList.toggle("active", btn.dataset.id === id);
}

async function loadHistory() {
  let jobs = [];
  try { jobs = (await api("/api/jobs")).jobs; } catch (_) { return; }
  const list = $("#history");
  list.replaceChildren();
  if (!jobs.length) { list.append(el("li", { class: "muted" }, "Nenhum áudio gerado ainda.")); return; }
  for (const job of jobs) {
    const details = [STATUS_LABELS[job.status] || job.status, fmtDate(job.created_at)];
    if (job.duration) details.push(fmtDuration(job.duration));
    list.append(el("li", { class: "item" },
      el("button", {
        type: "button", class: "open", dataset: { id: job.id },
        onclick: async () => {
          try { openJob(job.id, await api(`/api/jobs/${job.id}`)); } catch (err) { toast(err.message, "error"); }
        },
      }, el("strong", {}, job.title || job.source_name || job.text_preview || "Sem título"), el("small", {}, details.join(" · "))),
      el("button", {
        type: "button", class: "del", title: "Excluir", "aria-label": `Excluir ${job.title || "job"}`,
        onclick: async () => {
          if (!confirm("Excluir este áudio e todos os arquivos gerados?")) return;
          try {
            await api(`/api/jobs/${job.id}`, { method: "DELETE" });
            if (state.job && state.job.id === job.id) { stopTracking(); resetResult(); $("#job-card").hidden = true; history.replaceState(null, "", location.pathname); }
            loadHistory();
          } catch (err) { toast(err.message, "error"); }
        },
      }, el("span", { "aria-hidden": "true" }, "✕"))));
  }
  if (state.job) markActiveHistory(state.job.id);
}

/* ------------------------------------------------------------------ boot */

async function init() {
  initTheme();
  initContent();
  initPlayer();
  try {
    state.caps = await api("/api/capabilities");
  } catch (err) {
    toast(`Servidor indisponível: ${err.message}`, "error");
    return;
  }
  const caps = state.caps;
  state.defaults = caps.defaults;
  renderStatus(caps);
  renderPresets(caps.presets);
  renderFormats(caps.formats);
  renderLanguages(caps.languages);
  renderEngines(caps.engines);
  $("#file").accept = caps.extensions.join(",");
  $("#file-help").textContent = `TXT, Markdown, HTML, DOCX, ODT, EPUB e PDF · até ${caps.limits.max_upload_mb} MB`;
  restoreForm();
  if (!caps.ffmpeg.video) {
    for (const input of $$("#video-choice input")) if (input.value) input.disabled = true;
  }
  engineHint();
  await loadVoices();

  $("#job-form").addEventListener("submit", submit);
  $("#job-form").addEventListener("input", (e) => {
    if (e.target.id === "text") return;
    updateOutputs();
    saveForm();
  });
  $("#job-form").addEventListener("change", (e) => {
    if (e.target.id === "engine") { engineHint(); loadVoices(); }
    if (e.target.id === "lang") loadVoices();
    updateOutputs();
    saveForm();
  });
  $("#preview-btn").addEventListener("click", preview);
  $("#refresh-history").addEventListener("click", loadHistory);
  $("#reset-form").addEventListener("click", () => {
    safeStorage(() => localStorage.removeItem(STORE_KEY));
    $("#lexicon").value = "";
    selectPreset("padrao", true);
    toast("Configurações restauradas.");
  });

  await loadHistory();
  const match = location.hash.match(/job=([a-f0-9]{12})/);
  if (match) {
    try { openJob(match[1], await api(`/api/jobs/${match[1]}`)); } catch (_) { history.replaceState(null, "", location.pathname); }
  }
}

document.addEventListener("DOMContentLoaded", init);
