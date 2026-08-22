/* ═══════════════════════════════════════════════════════════════════════
   LUMEN — веб-интерфейс (app.js)
   Нулевые зависимости: fetch + SSE-pоток конвейера «Луч».
   ═══════════════════════════════════════════════════════════════════════ */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const CATS = {
  identity: "Личность",
  preferences: "Предпочтения",
  projects: "Проекты",
  relationships: "Люди",
  wishes: "Пожелания",
  notes: "Заметки",
};

const INTENT_RU = {
  identity: "о себе", capability: "возможности", greeting: "приветствие",
  farewell: "прощание", thanks: "благодарность", time: "время",
  math: "вычисление", weather: "погода", system_status: "система",
  file_search: "поиск файлов", memory_save: "память: записать",
  memory_recall: "память: вспомнить", web_info: "веб-поиск", chat: "диалог",
};

const STAGE_RU = {
  intake: "приём запроса…", guard: "защитный слой…", plan: "анализ намерений…",
  tool: "инструменты…", context: "сборка контекста…",
  remember: "запоминание…", done: "готово",
};

const state = {
  view: "dialog",
  sessionId: null,
  busy: false,
  memory: { category: null, facts: {}, search: "" },
  settingsCache: null,
};

/* ─── API ─────────────────────────────────────────────────────────────── */
async function api(path, method = "GET", body = null) {
  const opt = { method, headers: {} };
  if (body !== null) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data = {};
  try { data = await res.json(); } catch (e) { /* нет тела */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ─── утилиты ─────────────────────────────────────────────────────────── */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function mdToHtml(src) {
  let t = esc(src || "");
  // блоки кода
  t = t.replace(/```([\s\S]*?)```/g, (_, code) =>
    `<pre><code>${code.replace(/^\w*\n/, "")}</code></pre>`);
  // инлайн-код
  t = t.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  // жирный / курсив
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // ссылки
  t = t.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // списки
  t = t.replace(/(?:^|\n)((?:[-•*] .*(?:\n|$))+)/g, (_, block) =>
    "<ul>" + block.trim().split("\n").map(l =>
      `<li>${l.replace(/^[-•*] /, "")}</li>`).join("") + "</ul>");
  t = t.replace(/(?:^|\n)((?:\d+\. .*(?:\n|$))+)/g, (_, block) =>
    "<ol>" + block.trim().split("\n").map(l =>
      `<li>${l.replace(/^\d+\. /, "")}</li>`).join("") + "</ol>");
  // заголовки
  t = t.replace(/^###### (.*)$/gm, "<b>$1</b>")
       .replace(/^##### (.*)$/gm, "<b>$1</b>")
       .replace(/^#### (.*)$/gm, "<b>$1</b>")
       .replace(/^### (.*)$/gm, "<b>$1</b>")
       .replace(/^## (.*)$/gm, "<b>$1</b>")
       .replace(/^# (.*)$/gm, "<b>$1</b>");
  // подчёркивание-курсив (остатки) и переносы
  t = t.replace(/(^|\s)_([^_\n]+)_(?=\s|[.,!?;:)]|$)/gm, "$1<em>$2</em>");
  t = t.split(/\n{2,}/).map(par =>
    par.includes("<ul>") || par.includes("<ol>") || par.includes("<pre>")
      ? par
      : `<p>${par.replace(/\n/g, "<br>")}</p>`
  ).join("");
  return t;
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toast-root").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function clockTick() {
  const d = new Date();
  $("#clock").textContent = d.toLocaleTimeString("ru-RU");
}

/* ─── роутер ──────────────────────────────────────────────────────────── */
const VIEWS = {
  dialog:   { crumb: "Диалог", load: () => {} },
  tools:    { crumb: "Инструменты", load: loadTools },
  memory:   { crumb: "Память", load: loadMemory },
  system:   { crumb: "Система", load: loadSystem },
  settings: { crumb: "Настройки", load: loadSettings },
};

function route() {
  const hash = (location.hash || "#/dialog").slice(2).split("?")[0];
  const view = VIEWS[hash] ? hash : "dialog";
  state.view = view;
  $$(".nav-item").forEach(a =>
    a.classList.toggle("active", a.dataset.view === view));
  $$(".view").forEach(v => v.classList.add("hidden"));
  $(`#view-${view}`).classList.remove("hidden");
  $("#crumb").textContent = VIEWS[view].crumb;
  VIEWS[view].load();
}
window.addEventListener("hashchange", route);

/* ─── ДИАЛОГ ──────────────────────────────────────────────────────────── */
function welcomeCard() {
  return `
  <div class="welcome">
    <img src="assets/logo.svg" alt=""/>
    <h2>Здравствуйте. Я <b>LUMEN</b>.</h2>
    <p>Самостоятельная ИИ-платформа: ядро LUMEN-1, шесть стадий конвейера
       «Луч», контекстная память и инструменты. Спросите что-нибудь — или
       начните с подсказки.</p>
    <div class="sugg-row">
      <button class="sugg">Кто ты?</button>
      <button class="sugg">Сколько сейчас времени?</button>
      <button class="sugg">Состояние системы</button>
      <button class="sugg">Какая погода в Москве?</button>
      <button class="sugg">Найди файлы *.md</button>
      <button class="sugg">Что ты умеешь?</button>
    </div>
  </div>`;
}

function renderChat() {
  const inner = $("#chat-inner");
  if (!inner.dataset.rendered) {
    inner.innerHTML = welcomeCard();
    inner.dataset.rendered = "1";
    $$(".sugg", inner).forEach(b =>
      b.addEventListener("click", () => send(b.textContent)));
  }
}

function typingCard() {
  const el = document.createElement("div");
  el.className = "lumina";
  el.dataset.typing = "1";
  el.innerHTML = `
    <div class="lumina-header">
      <img src="assets/logo.svg" alt=""/><span class="lumina-name">LUMEN</span>
      <span class="lumina-spacer"></span>
      <span class="chip accent">конвейер «Луч»</span>
    </div>
    <div class="typing">
      <div class="dots"><i></i><i></i><i></i></div>
      <span class="stage-label">приём запроса…</span>
    </div>`;
  return el;
}

function luminaCard(r) {
  const el = document.createElement("div");
  el.className = "lumina";
  const intentChip = `<span class="chip">${esc(INTENT_RU[r.intent] || r.intent)}</span>`;
  const conf = r.confidence >= 0.6
    ? `<span class="chip">уверенность ${Math.round(r.confidence * 100)}%</span>` : "";
  const risk = r.risk === "blocked"
    ? `<span class="chip bad">заблокировано</span>`
    : r.risk === "caution" ? `<span class="chip warn">осторожно</span>` : "";
  const tools = (r.tools_used || []).map(t =>
    `<span class="tool-chip ${t.ok ? "ok" : "err"}">
       <span class="tk">${t.ok ? "✓" : "✕"}</span>${esc(t.name)} · ${t.ms} мс
     </span>`).join("");
  const fb = r.message_id ? `
      <button class="act" data-fb="1" title="Полезно">👍</button>
      <button class="act" data-fb="-1" title="Не совсем">👎</button>` : "";
  el.innerHTML = `
    <div class="lumina-header">
      <img src="assets/logo.svg" alt=""/><span class="lumina-name">LUMEN</span>
      ${risk}${intentChip}${conf}
      <span class="lumina-spacer"></span>
      <span class="chip">${esc({lumen_core: "LUMEN Core", lhc: "LUMEN Core", heuristic: "LUMEN Core"}[r.backend] || r.backend)} · ${r.latency_ms} мс</span>
    </div>
    <div class="lumina-body">${mdToHtml(r.reply)}</div>
    <div class="lumina-footer">
      ${tools || ""}
      <span class="lumina-acts">
        ${fb}
        <button class="act" data-copy title="Копировать">⧉</button>
        <button class="act" data-regen title="Сгенерировать заново">↻</button>
      </span>
    </div>`;

  const bodyText = r.reply;
  el.querySelector("[data-copy]")?.addEventListener("click", () => {
    navigator.clipboard?.writeText(bodyText).then(
      () => toast("Скопировано", "ok"), () => toast("Не удалось скопировать", "err"));
  });
  el.querySelector("[data-regen]")?.addEventListener("click", () => {
    const lastUser = lastUserText();
    if (lastUser && !state.busy) { el.remove(); send(lastUser); }
  });
  el.querySelectorAll("[data-fb]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        await api("/api/feedback", "POST", {
          message_id: r.message_id, rating: +b.dataset.fb, intent: r.intent,
        });
        b.classList.add("on");
        toast(b.dataset.fb === "1"
          ? "Спасибо! LUMEN учтёт оценку."
          : "Записано: буду отвечать точнее.", "ok");
      } catch (e) { toast(e.message, "err"); }
    }));
  return el;
}

function lastUserText() {
  const bubbles = $$("#chat-inner .msg-user");
  const last = bubbles[bubbles.length - 1];
  return last ? last.querySelector(".bubble-user").dataset.text : "";
}

async function send(text) {
  const msg = (text ?? $("#input").value).trim();
  if (!msg || state.busy) return;
  state.busy = true;
  $("#input").value = "";
  autoGrow();
  $("#send").disabled = true;

  const inner = $("#chat-inner");
  if (inner.dataset.rendered === "" || $("#chat-inner .welcome")) {
    $("#chat-inner .welcome")?.remove();
  }

  const userEl = document.createElement("div");
  userEl.className = "msg-user";
  userEl.innerHTML = `<div class="bubble-user" data-text="${esc(msg)}">
    <div class="who">Вы</div>${esc(msg)}</div>`;
  inner.appendChild(userEl);

  const tEl = typingCard();
  inner.appendChild(tEl);
  scrollChat();

  const stageLabel = $(".stage-label", tEl);
  const fail = (err) => {
    tEl.remove();
    const e = document.createElement("div");
    e.className = "lumina";
    e.innerHTML = `<div class="lumina-body">
      <p><b style="color:var(--bad)">Ошибка соединения с ядром:</b> ${esc(err.message || err)}</p>
      <p>Проверьте, запущен ли сервер: <code>python -m lumen serve</code></p></div>`;
    inner.appendChild(e);
    state.busy = false; $("#send").disabled = false;
    scrollChat();
  };

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg, session_id: state.sessionId }),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let result = null;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const lines = chunk.split("\n");
        let ev = "message", data = "{}";
        for (const line of lines) {
          if (line.startsWith("event: ")) ev = line.slice(7).trim();
          if (line.startsWith("data: ")) data = line.slice(6);
        }
        let payload = {};
        try { payload = JSON.parse(data); } catch (e) { /* ignore */ }
        if (ev === "result") result = payload;
        else if (ev === "error") throw new Error(payload.error || "ошибка ядра");
        else if (ev === "start" && payload.session_id) state.sessionId = payload.session_id;
        else if (ev === "stage" && stageLabel) {
          if (payload.stage === "tool") {
            stageLabel.textContent = `инструмент: ${payload.name}…`;
          } else if (STAGE_RU[payload.stage]) {
            stageLabel.textContent = STAGE_RU[payload.stage];
          }
        }
      }
    }
    tEl.remove();
    if (result) {
      state.sessionId = result.session_id || state.sessionId;
      inner.appendChild(luminaCard(result));
      if (result.harvested?.length) {
        toast(`Запомнено автоматически: ${result.harvested.map(h => h.value).join(", ")}`, "ok");
      }
    } else {
      fail(new Error("поток завершён без ответа"));
      return;
    }
  } catch (e) {
    fail(e);
    return;
  }
  state.busy = false;
  $("#send").disabled = false;
  scrollChat();
}

function scrollChat() {
  const c = $("#chat");
  c.scrollTop = c.scrollHeight;
}

function autoGrow() {
  const ta = $("#input");
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

async function newSession() {
  try {
    const r = await api("/api/sessions", "POST", { title: "Сессия из UI" });
    state.sessionId = r.session_id;
    $("#chat-inner").innerHTML = welcomeCard();
    delete $("#chat-inner").dataset.rendered;
    renderChat();
    toast("Новая сессия начата", "ok");
  } catch (e) { toast(e.message, "err"); }
}

/* ─── ИНСТРУМЕНТЫ ─────────────────────────────────────────────────────── */
async function loadTools() {
  const grid = $("#tools-grid");
  grid.innerHTML = `<div class="mem-empty">Загрузка реестра…</div>`;
  try {
    const { tools } = await api("/api/tools");
    grid.innerHTML = tools.map((t, i) => `
      <div class="tool-card" data-i="${i}">
        <div class="tool-top">
          <span class="tool-icon">${esc(t.icon || "⚙")}</span>
          <div>
            <div class="tool-name">${esc(t.name)}</div>
            <div class="tool-title">${esc(t.title)} · ${esc(t.category)}</div>
          </div>
        </div>
        <div class="tool-desc">${esc(t.description)}</div>
        <div class="tool-params">
          ${t.params.map(p =>
            `<span class="chip" title="${esc(p.description)}">${esc(p.name)}${p.required ? " *" : ""}</span>`
          ).join("") || "<span class='chip'>без параметров</span>"}
        </div>
        <div class="tool-foot">
          <span class="avail">
            <span class="dot ${t.available ? "on" : "off"}"></span>
            ${t.available ? "доступен" : esc(t.availability_reason || "недоступен")}
          </span>
          <button class="accent-btn" data-run ${t.available ? "" : "disabled"}>Запустить</button>
        </div>
      </div>`).join("");
    $$("[data-run]", grid).forEach(b =>
      b.addEventListener("click", () => toolModal(tools[b.closest(".tool-card").dataset.i])));
  } catch (e) {
    grid.innerHTML = `<div class="mem-empty">Ошибка: ${esc(e.message)}</div>`;
  }
}

function toolModal(t) {
  const root = $("#modal-root");
  root.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal">
        <div class="modal-head">
          <b>${esc(t.icon || "⚙")} ${esc(t.name)}</b>
          <button class="modal-close">×</button>
        </div>
        <div class="modal-body" id="tool-modal-body">
          ${t.params.map(p => `
            <div class="set-row">
              <label>${esc(p.name)} ${p.required ? "<b style='color:var(--bad)'>*</b>" : ""}
                <span class="hint-inline">— ${esc(p.description)}</span></label>
              <input data-p="${esc(p.name)}" placeholder="${esc(p.default ?? "")}"
                ${p.type === "number" || p.type === "integer" ? "type='number'" : ""}/>
            </div>`).join("") ||
            `<p style="color:var(--muted)">Инструмент не требует параметров.</p>`}
          <div id="tool-modal-result"></div>
        </div>
        <div class="modal-foot">
          <button class="ghost-btn" id="tool-cancel">Закрыть</button>
          <button class="accent-btn" id="tool-go">Выполнить</button>
        </div>
      </div>
    </div>`;
  const close = () => { root.innerHTML = ""; };
  root.querySelector(".modal-close").addEventListener("click", close);
  $("#tool-cancel").addEventListener("click", close);
  root.querySelector(".modal-backdrop").addEventListener("click", e => {
    if (e.target.classList.contains("modal-backdrop")) close();
  });
  $("#tool-go").addEventListener("click", async () => {
    const args = {};
    $$("[data-p]").forEach(inp => {
      if (inp.value.trim() !== "") args[inp.dataset.p] = inp.value.trim();
    });
    const res = $("#tool-modal-result");
    res.innerHTML = `<pre>Выполняется…</pre>`;
    try {
      const r = await api("/api/tools/run", "POST", { name: t.name, args });
      res.innerHTML = `<pre>${esc(r.text || JSON.stringify(r.data, null, 2))}</pre>`;
    } catch (e) {
      res.innerHTML = `<pre style="color:var(--bad)">${esc(e.message)}</pre>`;
    }
  });
}

/* ─── ПАМЯТЬ ──────────────────────────────────────────────────────────── */
async function loadMemory() {
  const catSel = $("#mem-cat");
  if (!catSel.options.length) {
    catSel.innerHTML = Object.entries(CATS)
      .map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  }
  renderMemCats();
  await refreshFacts();
}

function renderMemCats() {
  const wrap = $("#mem-cats");
  const items = state.memory.facts;
  const counts = { all: 0 };
  for (const [c, v] of Object.entries(items)) counts[c] = Object.keys(v).length;
  counts.all = Object.values(counts).reduce((a, b) => a + b, 0);
  const cur = state.memory.category;
  wrap.innerHTML = [
    `<div class="mem-cat ${!cur ? "active" : ""}" data-c="">Все <span class="cnt">${counts.all}</span></div>`,
    ...Object.entries(CATS).map(([k, v]) =>
      `<div class="mem-cat ${cur === k ? "active" : ""}" data-c="${k}">${v} <span class="cnt">${counts[k] || 0}</span></div>`),
  ].join("");
  $$(".mem-cat", wrap).forEach(el => el.addEventListener("click", () => {
    state.memory.category = el.dataset.c || null;
    renderMemCats();
    refreshFacts();
  }));
}

async function refreshFacts() {
  const list = $("#mem-list");
  try {
    const { facts, count } = await api("/api/memory");
    state.memory.facts = facts;
    renderMemCats();
    let items = [];
    for (const [cat, entries] of Object.entries(facts)) {
      if (state.memory.category && state.memory.category !== cat) continue;
      for (const [key, v] of Object.entries(entries)) {
        items.push({ cat, key, ...v });
      }
    }
    items.sort((a, b) => (b.updated || 0) - (a.updated || 0));
    if (!items.length) {
      list.innerHTML = `<div class="mem-empty">
        ${count ? "В этой категории пусто." : "Память пуста. Напишите «запомни, что…» в диалоге — или добавьте факт слева."}</div>`;
      return;
    }
    list.innerHTML = items.map(f => `
      <div class="fact-card" data-cat="${esc(f.cat)}" data-key="${esc(f.key)}">
        <span class="fact-badge">${esc(CATS[f.cat] || f.cat)}</span>
        <div class="fact-body">
          <div class="fact-key">${esc(f.key)}</div>
          <div class="fact-value">${esc(f.value)}</div>
          <div class="fact-upd">${esc(f.updated_human || "")} · ${esc(f.origin || "")}</div>
        </div>
        <button class="fact-del" title="Забыть">×</button>
      </div>`).join("");
    $$(".fact-del", list).forEach(b => b.addEventListener("click", async () => {
      const card = b.closest(".fact-card");
      try {
        await api("/api/memory", "DELETE", {
          category: card.dataset.cat, key: card.dataset.key });
        toast("Факт удалён из памяти", "ok");
        refreshFacts();
      } catch (e) { toast(e.message, "err"); }
    }));
  } catch (e) {
    list.innerHTML = `<div class="mem-empty">Ошибка: ${esc(e.message)}</div>`;
  }
}

/* ─── СИСТЕМА ─────────────────────────────────────────────────────────── */
const PIPELINE = [
  ["Приём", "нормализация, лимиты, спам"],
  ["Защита", "анти-injection, уровни риска"],
  ["Анализ", "намерения, слоты, план"],
  ["Контекст", "персона + факты + история"],
  ["Инструменты", "реестр, таймауты, изоляция"],
  ["Генерация", "LUMEN Core — собственный ИИ (офлайн, без API)"],
];

async function loadSystem() {
  const grid = $("#sys-grid");
  grid.innerHTML = `<div class="sys-card"><div class="mem-empty">Загрузка…</div></div>`;
  try {
    const [sys, health, learn] = await Promise.all([
      api("/api/system"), api("/api/health"), api("/api/learning/stats"),
    ]);
    const p = sys.platform || {};
    const mem = sys.mem || {}, disk = sys.disk || {};
    const bar = (v, max = 100) => {
      const pct = Math.max(0, Math.min(100, v));
      return `<div class="bar ${pct > 75 ? "hot" : ""}"><i style="width:${pct}%"></i></div><div class="kv"><span class="k"></span><span class="v">${v >= 0 ? v + "%" : "н/д"}</span></div>`;
    };
    const topIntents = Object.entries(learn.intents || {})
      .slice(0, 5).map(([k, v]) => `${INTENT_RU[k] || k} — ${v}`).join(" · ") || "—";
    grid.innerHTML = `
      <div class="sys-card">
        <h3>◈ Платформа</h3>
        <div class="kv"><span class="k">Бренд</span><span class="v">${esc(p.brand || "LUMEN")}</span></div>
        <div class="kv"><span class="k">Ядро</span><span class="v">${esc(p.model || "LUMEN-1")} · конвейер «Луч»</span></div>
        <div class="kv"><span class="k">Версия</span><span class="v">${esc(p.version || "1.0.0")}</span></div>
        <div class="kv"><span class="k">Модуль генерации</span><span class="v">${esc(p.backend || "")}</span></div>
        <div class="kv"><span class="k">Уптайм</span><span class="v">${p.uptime_sec ?? 0} с</span></div>
      </div>
      <div class="sys-card">
        <h3>🖥 Машина</h3>
        <div class="kv"><span class="k">Система</span><span class="v">${esc(sys.os || "")} · ${esc(sys.hostname || "")}</span></div>
        <div class="kv"><span class="k">Python</span><span class="v">${esc(sys.python || "")}</span></div>
        <div class="kv"><span class="k">CPU</span><span class="v">${esc(sys.cpu_count || "")} · ${sys.cpu_percent >= 0 ? sys.cpu_percent + "%" : "н/д"}</span></div>
        <div style="color:var(--muted);font-size:12px">Загрузка CPU</div>${bar(sys.cpu_percent)}
        <div style="color:var(--muted);font-size:12px">Память${mem.total_gb > 0 ? ` · ${mem.used_gb} / ${mem.total_gb} ГБ` : ""}</div>${bar(mem.percent)}
        <div style="color:var(--muted);font-size:12px">Диск${disk.total_gb > 0 ? ` · свободно ${disk.free_gb} ГБ` : ""}</div>${bar(disk.percent)}
      </div>
      <div class="sys-card">
        <h3>🧠 Обучение</h3>
        <div class="kv"><span class="k">Запросов обработано</span><span class="v">${learn.requests ?? 0}</span></div>
        <div class="kv"><span class="k">Оценок (👍/)</span><span class="v">${learn.feedback_positive ?? 0} / ${learn.feedback_negative ?? 0}</span></div>
        <div class="kv"><span class="k">Уровень одобрения</span><span class="v">${Math.round((learn.approval_rate ?? 0) * 100)}%</span></div>
        <div class="kv"><span class="k">Топ намерений</span><span class="v">${esc(topIntents)}</span></div>
        ${(learn.learned_preferences || []).length ? `
          <div style="margin-top:8px;color:var(--muted);font-size:12.5px">
            <b>Выученные предпочтения:</b>
            ${learn.learned_preferences.map(x => `<div>• ${esc(x)}</div>`).join("")}
          </div>` : ""}
      </div>
      <div class="sys-card">
        <h3>🧩 Инструменты и память</h3>
        <div class="kv"><span class="k">Инструментов</span><span class="v">${p.tools_available ?? 0} из ${p.tools_total ?? 0} доступно</span></div>
        <div class="kv"><span class="k">Фактов в памяти</span><span class="v">${p.facts ?? 0}</span></div>
        <div class="kv"><span class="k">Ядро отвечает</span><span class="v" style="color:var(--good)">● да</span></div>
      </div>`;

    $("#pipeline-card").innerHTML = `
      <h3>Конвейер «Луч» — шесть стадий каждого запроса</h3>
      <div class="pipeline">
        ${PIPELINE.map(([n, d], i) => `
          <div class="stage"><span class="n">${i + 1}</span><b>${n}</b><span>${d}</span></div>
          ${i < PIPELINE.length - 1 ? '<span class="stage-arrow">→</span>' : ""}
        `).join("")}
      </div>`;
    await loadSessions();
  } catch (e) {
    grid.innerHTML = `<div class="sys-card"><div class="mem-empty">Ошибка: ${esc(e.message)}</div></div>`;
  }
}

async function loadSessions() {
  const wrap = $("#sessions-list");
  try {
    const { sessions } = await api("/api/sessions");
    if (!sessions.length) {
      wrap.innerHTML = `<div class="mem-empty">Сессий пока нет.</div>`;
      return;
    }
    wrap.innerHTML = sessions.slice(0, 12).map(s => `
      <div class="session-row">
        <span class="sid">#${esc(s.id)}</span>
        <span>${esc(s.title || "Сессия")}</span>
        <span class="spacer"></span>
        <span style="color:var(--muted);font-size:12px">${esc(s.created || "")} · ${s.messages ?? 0} реплик</span>
        <button class="ghost-btn" data-del="${esc(s.id)}">Удалить</button>
      </div>`).join("");
    $$("[data-del]", wrap).forEach(b => b.addEventListener("click", async () => {
      try {
        await api(`/api/sessions/${b.dataset.del}`, "DELETE");
        toast("Сессия удалена", "ok");
        loadSessions();
      } catch (e) { toast(e.message, "err"); }
    }));
  } catch (e) { /* тихо */ }
}

/* ─── НАСТРОЙКИ ───────────────────────────────────────────────────────── */
async function loadSettings() {
  try {
    const s = await api("/api/settings");
    state.settingsCache = s;
    const b = s.backend || {}, p = s.persona || {}, c = s.context || {},
          sf = s.safety || {}, v = s.voice || {}, ui = s.ui || {};
    $("#settings-grid").innerHTML = `
      <div class="set-card">
        <h3>Модуль генерации</h3>
        <div class="set-row">
          <label>ИИ платформы (backend.provider)</label>
          <div class="core-badge" style="padding:10px 14px;border:1px solid var(--accent,#6ea8fe);border-radius:10px;background:rgba(110,168,254,.08)">
            <b>LUMEN Core</b> — собственный ИИ
            <div class="hint-inline" style="margin-top:4px">
              Полностью офлайн: база знаний, диалог, вычисления и
              инструменты. Без внешних API, без ключей, без сети
              для генерации.
            </div>
          </div>
        </div>
      </div>
      <div class="set-card">
        <h3>Персона</h3>
        <div class="set-row"><label>Имя (persona.name)</label>
          <input id="s-name" value="${esc(p.name || "LUMEN")}"/></div>
        <div class="set-row"><label>Стиль (persona.style)</label>
          <select id="s-style">
            <option value="brief" ${p.style === "brief" ? "selected" : ""}>Краткий</option>
            <option value="balanced" ${p.style === "balanced" ? "selected" : ""}>Сбалансированный</option>
            <option value="creative" ${p.style === "creative" ? "selected" : ""}>Развёрнутый</option>
          </select></div>
        <h3 style="margin-top:8px">Контекст</h3>
        <div class="set-row"><label>Реплик в контексте (context.max_turns)</label>
          <input id="s-turns" type="number" min="2" max="60" value="${c.max_turns ?? 12}"/></div>
        <div class="set-row"><label>Фактов в промпт (context.max_facts)</label>
          <input id="s-facts" type="number" min="0" max="20" value="${c.max_facts ?? 6}"/></div>
      </div>
      <div class="set-card">
        <h3>Безопасность</h3>
        <div class="set-row"><label>Лимит запроса, знаков (safety.max_input_length)</label>
          <input id="s-maxlen" type="number" min="200" max="100000" step="100"
            value="${sf.max_input_length ?? 8000}"/></div>
        <div class="set-row check">
          <label>Блокировать prompt-injection (safety.block_injection)</label>
          <input id="s-block" type="checkbox" ${sf.block_injection ? "checked" : ""}/>
        </div>
        <h3 style="margin-top:8px">Голос</h3>
        <div class="set-row check">
          <label>Включить голосовой модуль (voice.enabled)</label>
          <input id="s-voice" type="checkbox" ${v.enabled ? "checked" : ""}/>
        </div>
        <div class="set-row"><label>Движок TTS</label>
          <select id="s-tts">
            <option value="edge" ${v.tts_engine === "edge" ? "selected" : ""}>edge-tts</option>
          </select>
          <span class="hint-inline">Нужен установленный edge-tts на машине.</span></div>
      </div>
      <div class="set-card">
        <h3>Внешний вид</h3>
        <div class="set-row"><label>Тема (ui.theme)</label>
          <select id="s-theme">
            <option value="dark" ${ui.theme === "dark" ? "selected" : ""}>Tёмная «Luminance»</option>
            <option value="light" ${ui.theme === "light" ? "selected" : ""}>Светлая</option>
          </select></div>
        <h3 style="margin-top:8px">Данные</h3>
        <div class="set-row">
          <button class="ghost-btn" id="s-export" style="width:100%">
            Экспорт корпуса для дообучения (JSONL)
          </button>
          <span class="hint-inline">Собирает «запрос → ответ → оценка» из всех сессий — готовый датасет для future fine-tuning.</span>
        </div>
      </div>`;

    $("#s-export").addEventListener("click", async () => {
      try {
        const r = await api("/api/learning/corpus", "POST", {});
        toast(`Корпус сформирован: ${r.count} записей`, "ok");
      } catch (e) { toast(e.message, "err"); }
    });
  } catch (e) {
    $("#settings-grid").innerHTML =
      `<div class="sys-card"><div class="mem-empty">Ошибка: ${esc(e.message)}</div></div>`;
  }
}

async function saveSettings() {
  const val = id => $(id)?.value;
  const chk = id => $(id)?.checked;
  const payload = {
    backend: { provider: "lumen_core" },
    persona: { name: val("#s-name") || "LUMEN", style: val("#s-style") },
    context: { max_turns: parseInt(val("#s-turns") || "12", 10),
               max_facts: parseInt(val("#s-facts") || "6", 10) },
    safety: { max_input_length: parseInt(val("#s-maxlen") || "8000", 10),
              block_injection: chk("#s-block") },
    voice: { enabled: chk("#s-voice"), tts_engine: val("#s-tts") },
    ui: { theme: val("#s-theme") },
  };
  try {
    await api("/api/settings", "PUT", payload);
    document.documentElement.dataset.theme = payload.ui.theme;
    toast("Настройки сохранены и применены", "ok");
    refreshHealth();
  } catch (e) { toast(e.message, "err"); }
}

/* ─── служебное ───────────────────────────────────────────────────────── */
async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const dot = $("#backend-dot");
    dot.className = "dot " + (h.backend.available ? "on" : "off");
    $("#backend-label").textContent = h.backend.available
      ? `ядро активно · ${h.backend.display}`
      : `локальный модуль · ${h.backend.reason}`;
    $("#rail-ver").textContent = `${h.model} · v${h.version}`;
  } catch (e) {
    const dot = $("#backend-dot");
    dot.className = "dot off";
    $("#backend-label").textContent = "ядро недоступно";
  }
}

/* ─── инициализация ───────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  // тема из сохранённых настроек (best-effort)
  api("/api/settings").then(s => {
    document.documentElement.dataset.theme = s.ui?.theme || "dark";
  }).catch(() => {});

  renderChat();
  route();
  refreshHealth();
  setInterval(clockTick, 1000);
  clockTick();
  setInterval(refreshHealth, 20000);

  const input = $("#input");
  input.addEventListener("input", autoGrow);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $("#send").addEventListener("click", () => send());
  $("#new-session").addEventListener("click", newSession);

  // память
  $("#mem-add-btn").addEventListener("click", async () => {
    const key = $("#mem-key").value.trim(), value = $("#mem-value").value.trim();
    if (!key || !value) { toast("Укажите ключ и значение", "err"); return; }
    try {
      await api("/api/memory", "POST", {
        category: $("#mem-cat").value, key, value });
      $("#mem-key").value = ""; $("#mem-value").value = "";
      toast("Факт сохранён", "ok");
      refreshFacts();
    } catch (e) { toast(e.message, "err"); }
  });
  let memSearchTimer = null;
  $("#mem-search").addEventListener("input", e => {
    clearTimeout(memSearchTimer);
    memSearchTimer = setTimeout(async () => {
      const q = e.target.value.trim();
      const list = $("#mem-list");
      if (!q) { refreshFacts(); return; }
      try {
        const { facts } = await api("/api/memory/recall?query=" + encodeURIComponent(q));
        list.innerHTML = facts.length
          ? facts.map(f => `
              <div class="fact-card">
                <span class="fact-badge">${esc(CATS[f.category] || f.category)}</span>
                <div class="fact-body">
                  <div class="fact-key">${esc(f.key)} · релевантность ${f.score}</div>
                  <div class="fact-value">${esc(f.value)}</div>
                </div>
              </div>`).join("")
          : `<div class="mem-empty">По запросу «${esc(q)}» ничего не нашлось.</div>`;
      } catch (err) { /* тихо */ }
    }, 350);
  });

  // настройки
  $("#save-settings").addEventListener("click", saveSettings);
});
