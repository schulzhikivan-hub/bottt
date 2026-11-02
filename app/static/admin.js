const $ = (s) => document.querySelector(s);
const esc = (s = "") => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const CAT = {job: "Работа", housing: "Жильё", marketplace: "Барахолка"};
const STATUS_RU = {draft: "черновик", pending: "на проверке", approved: "опубликовано", rejected: "отклонено", needs_fix: "на доработке",
                   hidden: "скрыто", closed: "закрыто автором", sold: "продано", expired: "срок истёк", archived: "удалено автором"};
const DIFF_LABELS = {title: "Название", description: "Описание", contact: "Контакт", price: "Пометка к цене", price_amount: "Сумма",
                     city: "Место", region_code: "Регион", district_id: "Район", details: "Подробности"};

function session() { return sessionStorage.getItem("adminSession") || ""; }
function role() { return sessionStorage.getItem("adminRole") || ""; }

async function api(path, options = {}) {
  const headers = {"X-Admin-Session": session()};
  if (options.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {...options, headers});
  if (r.status === 401) { logout(); throw new Error("Сессия истекла. Войдите снова"); }
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) throw new Error(typeof data?.detail === "string" ? data.detail : (data?.detail?.[0]?.msg || "Ошибка сервера (" + r.status + ")"));
  return data;
}
const post = (path, body) => api(path, {method: "POST", body: body === undefined ? undefined : JSON.stringify(body)});
function say(text) {
  $("#status").textContent = text;
  if (text && !/^(Загрузка|Обновлено)/.test(text)) {          // a result must be seen: a toast in the corner, not small grey text
    const t = document.createElement("div"); t.className = "atoast"; t.textContent = text; $("#toasts").appendChild(t); setTimeout(() => t.remove(), 4500);
  }
}
function ask(question) {
  const value = prompt(question, "");
  if (value === null) return null;
  if (value.trim().length < 3) { say("Причина должна быть не короче 3 символов"); return null; }
  return value.trim();
}
const REASON_RU = {fraud: "мошенничество", fake: "фейк", spam: "спам", offensive: "оскорбления", inappropriate: "неприемлемое", rights: "нарушение прав", minor: "несовершеннолетний", profile: "анкета/профиль"};
const PAY_RU = {paid: "оплачен", pending: "ожидает", refunded: "возвращён", failed: "ошибка", cancelled: "отменён"};
const askOpt = (q) => { const v = prompt(q + " (можно пропустить)", ""); return v === null ? null : v.trim(); };
const who = (name, id) => (name ? `${esc(name)} <span class="muted">(${esc(id)})</span>` : `ID ${esc(id)}`);
const fmt = (s) => (s ? String(s).slice(0, 16).replace("T", " ") : "");

// ---- вход: автоматически из Telegram или кнопкой на сайте ----
const tgApp = window.Telegram?.WebApp;
tgApp?.ready(); tgApp?.expand();
let loginPoll = null;

function saveAdmin(data) {
  sessionStorage.setItem("adminSession", data.token || data.admin_session);
  sessionStorage.setItem("adminRole", data.role);
  sessionStorage.setItem("adminId", data.tg_id);
}
async function autoLogin() {
  try {
    const r = await fetch("/api/admin/auth/telegram", {method: "POST", headers: {"X-Telegram-Init-Data": tgApp.initData}});
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Нет доступа");
    saveAdmin(data);
    startApp();
  } catch (e) { $("#loginError").textContent = e.message; $("#webLoginBtn").hidden = true; }
}
$("#webLoginBtn").onclick = async () => {
  $("#loginError").textContent = "Готовим вход…";
  try {
    const r = await fetch("/api/auth/start", {method: "POST"});
    const start = await r.json();
    if (!r.ok) throw new Error(start.detail || "Не удалось начать вход");
    const link = $("#webLoginLink"); link.href = start.url; link.hidden = false;
    window.open(start.url, "_blank");
    $("#loginError").textContent = "Подтвердите вход в Telegram. Страница обновится сама.";
    clearInterval(loginPoll);
    const started = Date.now();
    loginPoll = setInterval(async () => {
      if (Date.now() - started > 300000) { clearInterval(loginPoll); $("#loginError").textContent = "Время вышло. Нажмите кнопку ещё раз."; return; }
      try {
        const p = await (await fetch(`/api/auth/poll/${start.token}`)).json();
        if (p.status === "ok") {
          clearInterval(loginPoll);
          if (!p.admin_session) { $("#loginError").textContent = "У вашего аккаунта нет доступа к админ-панели."; return; }
          saveAdmin({admin_session: p.admin_session, role: p.role, tg_id: p.tg_id});
          startApp();
        } else if (p.status === "rejected") { clearInterval(loginPoll); $("#loginError").textContent = "Вход отклонён в Telegram."; }
        else if (p.status === "expired") { clearInterval(loginPoll); $("#loginError").textContent = "Ссылка устарела. Нажмите кнопку ещё раз."; }
      } catch (e) { /* пробуем снова */ }
    }, 2000);
  } catch (e) { $("#loginError").textContent = e.message; }
};

// ---- вкладки по ролям ----
const SUPPORT = ["support", "moderator", "admin", "super_admin"];
const MOD = ["moderator", "admin", "super_admin"];
const ADM = ["admin", "super_admin"];
const FIN = ["accountant", "admin", "super_admin"];
const SUP = ["super_admin"];
// Структура по ТЗ (§2): разделы слева, внутри раздела подразделы сверху. Один смысл = один раздел.
const SECTIONS = [
  {key: "home", icon: "📊", name: "Главная", tabs: ["dash"]},
  {key: "listings", icon: "📋", name: "Объявления", tabs: ["listings", "alllist", "batches"]},
  {key: "people", icon: "👥", name: "Люди", tabs: ["users", "companies", "resumes", "reviews", "admins"]},
  {key: "dating", icon: "💗", name: "Знакомства", tabs: ["dating", "datingall"]},
  {key: "support", icon: "🚩", name: "Жалобы и поддержка", tabs: ["reports", "support", "duty"]},
  {key: "catalog", icon: "🗂", name: "Каталог и контент", tabs: ["community", "guide", "texts"]},
  {key: "marketing", icon: "📣", name: "Маркетинг", tabs: ["marketing"]},
  {key: "money", icon: "💰", name: "Деньги", tabs: ["payments", "exchange", "refunds", "pricing", "reportsm"]},
  {key: "rules", icon: "📐", name: "Поиск и правила", tabs: ["rules", "stest"]},
  {key: "system", icon: "⚙️", name: "Система", tabs: ["mynotify", "audit", "errors", "security", "settings"]},
];
const TABS = [
  {key: "dash", label: "Рабочий стол", roles: ADM, title: "Главная"},
  {key: "listings", label: "На проверке", roles: MOD, title: "Объявления: очередь на проверке"},
  {key: "alllist", label: "Все объявления", roles: MOD, title: "Объявления: все, с правкой и решениями"},
  {key: "batches", label: "Массовые действия", roles: MOD, title: "Объявления: массовые действия (посчитать, подтвердить, следить, отменить)"},
  {key: "users", label: "Пользователи", roles: SUPPORT, title: "Люди: пользователи"},
  {key: "companies", label: "Компании и работодатели", roles: MOD, title: "Люди: компании и работодатели (проверка, приостановка, баланс)"},
  {key: "resumes", label: "Резюме", roles: MOD, title: "Люди: резюме соискателей"},
  {key: "reviews", label: "Отзывы", roles: MOD, title: "Люди: отзывы"},
  {key: "admins", label: "Сотрудники", roles: SUP, title: "Люди: сотрудники и права"},
  {key: "dating", label: "На проверке", roles: MOD, title: "Знакомства: анкеты и селфи на проверке"},
  {key: "datingall", label: "Все анкеты", roles: MOD, title: "Знакомства: все анкеты, правка и решения"},
  {key: "reports", label: "Жалобы", roles: SUPPORT, title: "Жалобы и поддержка: жалобы"},
  {key: "support", label: "Обращения и оспаривания", roles: SUPPORT, title: "Жалобы и поддержка: обращения и оспаривания"},
  {key: "duty", label: "Дежурства", roles: SUPPORT, title: "Жалобы и поддержка: дежурства и эскалация"},
  {key: "community", label: "Сообщество", roles: ADM, title: "Каталог: группы сообщества"},
  {key: "guide", label: "Гид", roles: ADM, title: "Каталог: статьи гида"},
  {key: "texts", label: "Тексты и FAQ", roles: ADM, title: "Каталог: тексты, вопросы и ответы, настройки"},
  {key: "marketing", label: "Баннеры, рассылки, реклама, акции", roles: ADM, title: "Маркетинг: баннеры, рассылки, реклама, акции"},
  {key: "payments", label: "Платежи и промокоды", roles: FIN, title: "Деньги: платежи и промокоды"},
  {key: "exchange", label: "Обменник KH", roles: FIN, title: "Деньги: обменник звёзд Telegram на KH (валюта сайта)"},
  {key: "refunds", label: "Возвраты", roles: FIN, title: "Деньги: возвраты"},
  {key: "pricing", label: "Цены на услуги", roles: ADM, title: "Деньги: цены услуг"},
  {key: "reportsm", label: "Отчёты и VIP", roles: FIN, title: "Деньги: отчёты, VIP, подарки"},
  {key: "rules", label: "Правила", roles: ADM, title: "Правила: выдача, платные места, сроки, хранение"},
  {key: "stest", label: "Тест поиска и словари", roles: MOD, title: "Поиск: тест, синонимы, стоп-слова, журнал запросов"},
  {key: "mynotify", label: "Мои уведомления", roles: SUPPORT, title: "Система: о чём мне сообщает бот"},
  {key: "audit", label: "Журнал действий", roles: ADM, title: "Система: журнал действий"},
  {key: "errors", label: "Ошибки у людей", roles: ADM, title: "Система: ошибки, которые видели пользователи"},
  {key: "security", label: "Безопасность", roles: SUP, title: "Система: безопасность, функции, просмотры данных, входы"},
  {key: "settings", label: "Выгрузки", roles: ADM, title: "Система: выгрузки и мои входы"},
];
const SECTION_HELP = {
  home: "Сводка по работе сервиса и задачам, требующим внимания.",
  listings: "Проверка новых объявлений, исправления и массовые операции.",
  people: "Пользователи, работодатели, резюме, отзывы и права сотрудников.",
  dating: "Проверка анкет знакомств и управление опубликованными профилями.",
  support: "Жалобы, обращения пользователей и график дежурств.",
  catalog: "Справочники, статьи и тексты, которые видят пользователи.",
  marketing: "Баннеры, рассылки, рекламные размещения и акции.",
  money: "Платежи, возвраты, обмен KH и тарифы.",
  rules: "Настройки поиска, словари и правила размещения.",
  system: "Журнал действий, ошибки, безопасность и настройки."
};
const TAB_SECTION = Object.fromEntries(SECTIONS.flatMap((s) => s.tabs.map((k) => [k, s.key])));
const secTabs = (sec) => SECTIONS.find((s) => s.key === sec).tabs.map((k) => TABS.find((t) => t.key === k)).filter((t) => t && t.roles.includes(role()));
const lastTab = (sec) => { const k = sessionStorage.getItem("adminTab:" + sec); return secTabs(sec).find((t) => t.key === k) || secTabs(sec)[0]; };
let current = "";
function startApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#logout").hidden = false;
  $("#who").textContent = ({super_admin: "Суперадмин", admin: "Админ", moderator: "Модератор", support: "Поддержка"}[role()] || role()) + " · " + (sessionStorage.getItem("adminId") || "");
  const mineSections = SECTIONS.filter((sec) => secTabs(sec.key).length);
  $("#tabs").innerHTML = mineSections.map((sec) => `<button data-sec="${sec.key}" title="${esc(sec.name)}"><span class="ic">${sec.icon}</span><span class="lb">${esc(sec.name)}</span><em hidden data-sbadge="${sec.key}"></em></button>`).join("");
  $("#tabs").querySelectorAll("button").forEach((b) => b.onclick = () => { document.body.classList.remove("drawer"); $("#burger").setAttribute("aria-expanded", "false"); show(lastTab(b.dataset.sec).key); });
  show(lastTab(mineSections[0].key).key);
  refreshBadges();
}
// ---- оформление: тема, узкое меню, ящик на телефоне ----
(function chrome() {
  const saved = localStorage.getItem("adminTheme");
  const dark = saved ? saved === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  if (localStorage.getItem("adminRail") === "1") document.body.classList.add("rail");
  $("#themeBtn").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; localStorage.setItem("adminTheme", next);
  };
  $("#burger").onclick = () => {
    if (window.matchMedia("(max-width:860px)").matches) {
      document.body.classList.toggle("drawer");
      $("#burger").setAttribute("aria-expanded", document.body.classList.contains("drawer") ? "true" : "false");
    } else {
      document.body.classList.toggle("rail");
      localStorage.setItem("adminRail", document.body.classList.contains("rail") ? "1" : "0");
    }
  };
  $("#scrim").onclick = () => {
    document.body.classList.remove("drawer");
    $("#burger").setAttribute("aria-expanded", "false");
  };
})();

// ---- палитра команд: Ctrl/⌘+K или «/» ----
let palIndex = 0, palItems = [];
let palReturnFocus = null;
function palOpen() { palReturnFocus = document.activeElement; $("#pal").classList.remove("hidden"); $("#palQ").setAttribute("aria-expanded", "true"); $("#palQ").value = ""; palRender(); setTimeout(() => $("#palQ").focus(), 30); }
function palClose() { $("#pal").classList.add("hidden"); $("#palQ").setAttribute("aria-expanded", "false"); if (palReturnFocus?.focus) palReturnFocus.focus(); palReturnFocus = null; }
function palRender() {
  const q = $("#palQ").value.trim().toLowerCase();
  const tabs = TABS.filter((t) => t.key && t.roles.includes(role())).map((t) => {
    const rest = [t.label];
    return {ic: SECTIONS.find((x) => x.key === TAB_SECTION[t.key])?.icon || "•", name: t.label, hint: t.title, run: () => show(t.key)};
  });
  const actions = [
    {ic: "KH", name: "Выдать KH человеку", hint: "найдите человека и откройте управление", run: () => { $("#palQ").value = "?"; palRender(); }},
    {ic: "🌓", name: "Сменить тему", hint: "светлая / тёмная", run: () => $("#themeBtn").click()},
    {ic: "🔄", name: "Обновить раздел", hint: "перезагрузить данные", run: () => reload()},
  ];
  let list = [...tabs, ...actions].filter((x) => !q || (x.name + " " + x.hint).toLowerCase().includes(q.replace(/^\?/, "")));
  if (q.startsWith("?")) list = [];
  if (q) list.push({ic: "🔎", name: `Искать везде: «${$("#palQ").value.trim().replace(/^\?/, "")}»`, hint: "объявления, люди, обращения, платежи", run: () => { $("#gQuery").value = $("#palQ").value.trim().replace(/^\?/, ""); $("#gSearch").requestSubmit(); }});
  palItems = list.slice(0, 12);
  palIndex = Math.min(palIndex, Math.max(0, palItems.length - 1));
  $("#palList").innerHTML = palItems.map((x, i) => `<li class="${i === palIndex ? "on" : ""}" id="pal-option-${i}" role="option" aria-selected="${i === palIndex}" data-pi="${i}"><span class="ic" aria-hidden="true">${x.ic}</span><b>${esc(x.name)}</b><small>${esc(x.hint)}</small></li>`).join("") || '<li role="option" aria-disabled="true"><small>Ничего не нашлось</small></li>';
  $("#palQ").setAttribute("aria-activedescendant", palItems.length ? `pal-option-${palIndex}` : "");
}
function palGo(i) { const it = palItems[i]; if (!it) return; palClose(); it.run(); }
$("#palBtn").onclick = palOpen;
$("#palQ").addEventListener("input", () => { palIndex = 0; palRender(); });
$("#palList").addEventListener("click", (e) => { const li = e.target.closest("[data-pi]"); if (li) palGo(Number(li.dataset.pi)); });
$("#pal").addEventListener("click", (e) => { if (e.target.id === "pal") palClose(); });
document.addEventListener("keydown", (e) => {
  if (!session()) return;
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palOpen(); return; }
  if (e.key === "/" && !typing && $("#pal").classList.contains("hidden")) { e.preventDefault(); palOpen(); return; }
  if (e.key === "Escape" && !$("#pal").classList.contains("hidden")) { palClose(); return; }
  if (e.key === "Tab" && !$("#pal").classList.contains("hidden")) { e.preventDefault(); $("#palQ").focus(); return; }
  if (e.key === "Escape" && document.body.classList.contains("drawer")) {
    document.body.classList.remove("drawer");
    $("#burger").setAttribute("aria-expanded", "false");
    return;
  }
  if ($("#pal").classList.contains("hidden")) return;
  if (e.key === "ArrowDown") { e.preventDefault(); palIndex = Math.min(palItems.length - 1, palIndex + 1); palRender(); }
  if (e.key === "ArrowUp") { e.preventDefault(); palIndex = Math.max(0, palIndex - 1); palRender(); }
  if (e.key === "Enter") { e.preventDefault(); palGo(palIndex); }
});
async function logout() {
  try { await fetch("/api/admin/auth/logout", {method: "POST", headers: {"X-Admin-Session": session()}}); } catch (e) { /* ничего */ }
  sessionStorage.clear();
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#logout").hidden = true;
  $("#webLoginBtn").hidden = false;
}
$("#logout").onclick = logout;

const BADGE_KEYS = {listings: "listings", reports: "reports", support: "support", resumes: "resumes", companies: "employers", dating: "dating", errors: "errors", refunds: "refunds"};
async function refreshBadges() {
  try {
    const c = await api("/api/admin/counters");
    const set = (k, n, late) => { const el = document.querySelector(`[data-badge="${k}"]`); if (el) { el.textContent = n; el.hidden = !n; el.classList.toggle("warn", !!late); } };
    for (const [tab, key] of Object.entries(BADGE_KEYS)) set(tab, c[key] || 0, (key === "listings" && c.listings_wait_h >= 24) || (key === "support" && c.support_wait_h >= 24));
    for (const sec of SECTIONS) {            // a section shows the sum of what waits inside it
      const n = sec.tabs.reduce((sum, t) => sum + (BADGE_KEYS[t] && t !== "errors" ? (c[BADGE_KEYS[t]] || 0) : 0), 0);
      const el = document.querySelector(`[data-sbadge="${sec.key}"]`);
      if (el) { el.textContent = n; el.hidden = !n; el.classList.toggle("warn", (sec.key === "listings" && c.listings_wait_h >= 24) || (sec.key === "support" && c.support_wait_h >= 24)); }
    }
    document.title = (c.listings + c.support + c.reports > 0 ? `(${c.listings + c.support + c.reports}) ` : "") + "KOREA HUB · админка";
  } catch (e) { /* счётчики не обязательны */ }
}
setInterval(() => { if (session()) refreshBadges(); }, 60000);

// ---------- общий поиск ----------
$("#gSearch").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#gQuery").value.trim();
  if (!q) return;
  try {
    const r = await api("/api/admin/search?q=" + encodeURIComponent(q));
    current = "search";
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
    $("#title").textContent = "Результаты: " + q;
    const L = r.listings.map((x) => `<div class="card"><b>#${x.id} ${esc(x.title)}</b> <span class="badge">${esc(STATUS_RU[x.status] || x.status)}</span> · ${esc(x.city)} · автор ${esc(x.author_id)}
      <div><button class="act gray" data-gl="${x.id}">Открыть</button><button class="act gray" data-gu="${esc(x.author_id)}">Автор</button></div><div data-gbox></div></div>`).join("");
    const U = r.users.map((u) => `<div class="card"><b>${esc(u.name || "без имени")}</b> · ID ${esc(u.tg_id)} · ${esc(u.city)}${u.blocked ? " · <span class='bad'>заблокирован</span>" : ""}
      <div><button class="act gray" data-gu="${esc(u.tg_id)}">Управление</button></div></div>`).join("");
    const T = r.tickets.map((t) => `<div class="card">Обращение #${t.id} · ${esc(t.kind)} · ${esc(t.status)} · пользователь ${esc(t.user)}</div>`).join("");
    const P = r.payments.map((t) => `<div class="card">Платёж #${t.id} · ${t.stars} ⭐ · ${esc(t.status)} · пользователь ${esc(t.user)}</div>`).join("");
    $("#main").innerHTML = (L || U || T || P) ? (L ? "<h3>Объявления</h3>" + L : "") + (U ? "<h3>Люди</h3>" + U : "") + (T ? "<h3>Обращения</h3>" + T : "") + (P ? "<h3>Платежи</h3>" + P : "")
      : '<div class="card muted">Ничего не найдено.</div>';
  } catch (err) { say(err.message); }
});
$("#main").addEventListener("click", async (e) => {
  const gl = e.target.closest("[data-gl]");
  if (gl) {
    try { const x = await api("/api/admin/listings/" + gl.dataset.gl); gl.closest(".card").querySelector("[data-gbox]").innerHTML = listingCardHtml(x); } catch (err) { say(err.message); }
  }
  const gopen = e.target.closest("[data-gl-open]");
  if (gopen) { e.preventDefault(); const box = gopen.closest(".card").querySelector("[data-ctx]"); try { const x = await api("/api/admin/listings/" + gopen.dataset.glOpen); box.innerHTML = listingCardHtml(x); } catch (err) { say(err.message); } }
  const gu = e.target.closest("[data-gu]");
  if (gu) { e.preventDefault(); openUserCard(gu.dataset.gu); }
});

function renderSubnav(tab) {
  const sec = TAB_SECTION[tab], tabs = sec ? secTabs(sec) : [];
  const bar = $("#subnav");
  bar.hidden = tabs.length < 2;
  bar.innerHTML = tabs.map((t) => `<button class="${t.key === tab ? "on" : ""}" data-sub="${t.key}">${esc(t.label)}<em hidden data-badge="${t.key}"></em></button>`).join("");
  bar.querySelectorAll("button").forEach((b) => b.onclick = () => show(b.dataset.sub));
  bar.querySelector(".on")?.scrollIntoView({inline: "center", block: "nearest"});
}
function show(tab) {
  const def = TABS.find((t) => t.key === tab);
  current = tab;
  const sec = TAB_SECTION[tab];
  if (sec) sessionStorage.setItem("adminTab:" + sec, tab);
  document.querySelectorAll("#tabs button").forEach((b) => { const active = b.dataset.sec === sec; b.classList.toggle("active", active); if (active) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  const hint = $("#sectionHint"); hint.textContent = SECTION_HELP[sec] || ""; hint.hidden = !hint.textContent;
  renderSubnav(tab);
  $("#title").textContent = def.title;
  say("Загрузка…");
  $("#main").innerHTML = "";
  LOADERS[tab](def).then(() => { say("Обновлено " + new Date().toLocaleTimeString("ru-RU")); refreshBadges(); }).catch((e) => say(e.message));
}
const reload = () => { if (LOADERS[current]) show(current); else if (current === "search") $("#gSearch").requestSubmit(); };

// ---- главная: центр управления ----
let dashDays = 30, dashMetric = "users";
const fmtN = (n) => new Intl.NumberFormat("ru-RU").format(n);
function spark(vals, color = "var(--brand)") {
  const max = Math.max(1, ...vals), w = 100, h = 34;
  const pts = vals.map((v, i) => [vals.length < 2 ? 0 : i * w / (vals.length - 1), h - 3 - (v / max) * (h - 8)]);
  const line = pts.map(([x, y], i) => (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1)).join(" ");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path d="${line} L${w} ${h} L0 ${h} Z" fill="${color}" opacity=".12"/><path d="${line}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}
function delta(vals) {
  const half = Math.floor(vals.length / 2), a = vals.slice(0, half).reduce((x, y) => x + y, 0), b = vals.slice(half).reduce((x, y) => x + y, 0);
  if (!a && !b) return '<span class="delta flat">без изменений</span>';
  if (!a) return '<span class="delta up">▲ новое</span>';
  const p = Math.round((b - a) / a * 100);
  return `<span class="delta ${p > 0 ? "up" : p < 0 ? "down" : "flat"}">${p > 0 ? "▲" : p < 0 ? "▼" : "•"} ${Math.abs(p)}% к прошлому периоду</span>`;
}
const METRICS = {users: ["Новые люди", "var(--brand)"], listings: ["Новые объявления", "var(--brand2)"], stars: ["Выручка, ⭐", "#f2a20c"], applications: ["Отклики", "#e5484d"]};
function bigChart(days, key) {
  const vals = days.map((d) => d[key] || 0), max = Math.max(1, ...vals), W = 800, H = 230, padL = 4, padB = 26, padT = 14;
  const x = (i) => padL + i * (W - padL * 2) / Math.max(1, vals.length - 1), y = (v) => padT + (H - padT - padB) * (1 - v / max);
  const line = vals.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
  const color = METRICS[key][1];
  const grid = [0, .5, 1].map((f) => `<line x1="0" x2="${W}" y1="${y(max * f)}" y2="${y(max * f)}" stroke="var(--line)" stroke-dasharray="4 5"/><text x="2" y="${y(max * f) - 4}" fill="var(--muted)" font-size="11">${Math.round(max * f)}</text>`).join("");
  const labels = [0, Math.floor(vals.length / 2), vals.length - 1].map((i) => `<text x="${x(i)}" y="${H - 6}" fill="var(--muted)" font-size="11" text-anchor="${i === 0 ? "start" : i === vals.length - 1 ? "end" : "middle"}">${esc(days[i].date.slice(5))}</text>`).join("");
  return `<div class="chartbox" id="chartBox"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" id="chartSvg">${grid}${labels}
    <defs><linearGradient id="gA" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".35"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
    <path d="${line} L${x(vals.length - 1)} ${H - padB} L${x(0)} ${H - padB} Z" fill="url(#gA)"/><path d="${line}" fill="none" stroke="${color}" stroke-width="2.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
    <line id="chartX" y1="${padT}" y2="${H - padB}" stroke="${color}" stroke-width="1" opacity="0" vector-effect="non-scaling-stroke"/><circle id="chartDot" r="5" fill="${color}" stroke="var(--surface)" stroke-width="2" opacity="0"/></svg><div class="tip" id="chartTip"></div></div>`;
}
function bindChart(days, key) {
  const box = $("#chartBox"), svg = $("#chartSvg");
  if (!box) return;
  const vals = days.map((d) => d[key] || 0), max = Math.max(1, ...vals), W = 800, H = 230, padL = 4, padB = 26, padT = 14;
  const move = (ev) => {
    const r = svg.getBoundingClientRect(), px = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / r.width * W;
    const i = Math.max(0, Math.min(vals.length - 1, Math.round((px - padL) / ((W - padL * 2) / Math.max(1, vals.length - 1)))));
    const cx = padL + i * (W - padL * 2) / Math.max(1, vals.length - 1), cy = padT + (H - padT - padB) * (1 - vals[i] / max);
    const line = $("#chartX"), dot = $("#chartDot"), tip = $("#chartTip");
    line.setAttribute("x1", cx); line.setAttribute("x2", cx); line.style.opacity = 1;
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.style.opacity = 1;
    tip.style.left = (cx / W * r.width) + "px"; tip.style.top = (cy / H * r.height) + "px"; tip.style.opacity = 1;
    tip.innerHTML = `<b>${fmtN(vals[i])}</b> · ${esc(days[i].date.slice(5))}`;
  };
  const leave = () => { $("#chartTip").style.opacity = 0; $("#chartX").style.opacity = 0; $("#chartDot").style.opacity = 0; };
  svg.addEventListener("mousemove", move); svg.addEventListener("touchmove", move, {passive: true}); svg.addEventListener("mouseleave", leave);
}
const actorName = (a) => (a === "system" || a === "bot-command" ? "Система" : /^\d+$/.test(a || "") ? "Сотрудник …" + a.slice(-4) : a || "—");
const ACT_ICON = {listing: "📋", user: "👤", payment: "💳", report: "🚩", ad: "📣", company: "🏬", dating: "💗", rule: "📐", promo: "🎁", banner: "🏳️", grant: "🎁", auth: "🔐", broadcast: "📨", admin: "🧑‍💼"};
const ACT_TEXT = (a) => {
  const m = {"listing.approve": "одобрил объявление", "listing.reject": "отклонил объявление", "user.block": "заблокировал человека", "user.unblock": "разблокировал человека",
    "payment.refund": "сделал возврат", "user.stars": "выдал звёзды", "rule.change": "изменил правило", "company.approve": "подтвердил компанию", "company.reject": "отказал компании",
    "company.suspend": "приостановил компанию", "ad.create": "создал рекламу", "grant": "выдал услугу", "auth.login": "вошёл в панель", "user.account_reset": "сбросил вход человека",
    "dating.approve": "одобрил анкету", "dating.reject": "отклонил анкету", "report.resolve": "закрыл жалобу"};
  return m[a.action] || a.action;
};
function openAudit(a) {
  const t = a.object_type, id = a.object_id;
  if (t === "listing" && /^\d+$/.test(id)) return palShow("search", id);
  if (t === "user") return openUserCard(id);
  if (t === "company") return show("companies");
  if (t === "payment") return show("payments");
  return show("audit");
}
function palShow(kind, q) { $("#gQuery").value = q; $("#gSearch").requestSubmit(); }
const QUEUES = [  // [вкладка, значок, название, ключ счётчика, ключ ожидания, подпись]
  ["listings", "📋", "Объявления на проверке", "listings", "listings_wait_h", "новые и правки"], ["reports", "🚩", "Жалобы", "reports", null, "ждут решения"],
  ["dating", "💗", "Анкеты знакомств", "dating", null, "анкеты и селфи"], ["resumes", "🧑‍💼", "Резюме", "resumes", null, "на проверке"],
  ["companies", "🏬", "Компании и работодатели", "employers", null, "ждут проверки"], ["support", "💬", "Обращения", "support", "support_wait_h", "и оспаривания"],
  ["refunds", "↩️", "Возвраты", "refunds", null, "новые и в работе"], ["errors", "🐞", "Ошибки у людей", "errors", null, "за сутки"],
];
function ring(pct, level, label, sub) {
  const r = 34, c = 2 * Math.PI * r, v = Math.max(0, Math.min(100, pct ?? 0));
  const col = {ok: "var(--ok)", warn: "var(--warn)", bad: "var(--bad)", na: "var(--muted)"}[level] || "var(--ok)";
  return `<div class="gauge"><svg viewBox="0 0 84 84"><circle cx="42" cy="42" r="${r}" fill="none" stroke="var(--surface2)" stroke-width="9"/>
    <circle cx="42" cy="42" r="${r}" fill="none" stroke="${col}" stroke-width="9" stroke-linecap="round" stroke-dasharray="${(c * v / 100).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 42 42)"/>
    <text x="42" y="47" text-anchor="middle" font-size="17" font-weight="800" fill="var(--text)">${pct == null ? "—" : Math.round(v) + "%"}</text></svg><b>${label}</b><small>${sub}</small></div>`;
}
const gb = (n) => (n == null ? "—" : (n / 1073741824).toFixed(n >= 10737418240 ? 0 : 1) + " ГБ");
const dur = (s) => { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d} дн. ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`; };
let sysTimer = null;
async function fillSystemCard() {
  const box = $("#sysBox");
  if (!box) { clearInterval(sysTimer); return; }
  try {
    const [m, sys] = await Promise.all([api("/api/admin/system/metrics"), api("/api/admin/system").catch(() => ({}))]);
    box.innerHTML = `<div class="gauges">${ring(m.cpu.pct, m.cpu.level, "Процессор", `нагрузка ${m.cpu.load1} · ядер ${m.cpu.cores}`)}${ring(m.memory.pct, m.memory.level, "Память", `свободно ${gb(m.memory.available)} из ${gb(m.memory.total)}`)}
      ${ring(m.disk.pct, m.disk.level, "Диск", `свободно ${gb(m.disk.free)} из ${gb(m.disk.total)}`)}</div>
      <div class="facts"><span>⏱ Сервер работает: <b>${dur(m.uptime_s)}</b></span><span>🧩 Приложение: <b>${dur(m.app_uptime_s)}</b>, ${gb(m.memory.app_rss)} памяти</span><span>🗄 База: <b>${m.db_bytes == null ? "—" : (m.db_bytes / 1048576).toFixed(0) + " МБ"}</b></span>
      <span>🤖 Бот: <b class="${sys.bot_ok ? "good" : "bad"}">${sys.bot_ok ? "отвечает" : "молчит"}</b></span><span>🔒 Сертификат: <b>${sys.cert_days == null ? "—" : sys.cert_days + " дн."}</b></span>
      <span>Нагрузка за 5 и 15 минут: <b>${m.cpu.load5} · ${m.cpu.load15}</b></span></div>`;
    const bad = [m.cpu, m.memory, m.disk].some((x) => x.level === "bad"), warn = [m.cpu, m.memory, m.disk].some((x) => x.level === "warn");
    const dot = $("#pulseDot"), txt = $("#pulseText");
    if (dot) { dot.className = "dot " + (bad ? "bad" : warn ? "warn" : ""); txt.textContent = bad ? "Сервер перегружен" : warn ? "Нагрузка повышена" : "Сервер в порядке"; }
  } catch (err) { box.innerHTML = '<p class="muted">Нагрузку не удалось получить.</p>'; }
}
async function loadDash() {
  $("#main").innerHTML = '<div class="skel"></div><div class="skel" style="margin-top:12px;height:140px"></div>';
  const [s, a, c, queue, audit, sus] = await Promise.all([api("/api/admin/stats"), api(`/api/admin/analytics?days=${dashDays}`), api("/api/admin/counters").catch(() => ({})),
    api("/api/admin/listings"), api("/api/admin/audit?limit=12").catch(() => []), api("/api/admin/suspicious").catch(() => ({reported: [], burst: [], shared_contact: []}))]);
  const f = a.funnel, days = a.days, today = days[days.length - 1] || {}, yest = days[days.length - 2] || {};
  const rows = QUEUES.map(([tab, ic, name, key, waitKey, hint]) => ({tab, ic, name, hint, n: c[key] || 0, wait: waitKey ? c[waitKey] || 0 : 0}));
  const open = rows.reduce((n, r) => n + r.n, 0);
  const tod = (title, key, go, suffix = "") => { const t = today[key] || 0, y = yest[key] || 0, d = t - y; return `<button class="today" data-go="${go}"><small>${title}</small><b>${fmtN(t)}${suffix}</b><span class="${d > 0 ? "up" : d < 0 ? "down" : "flat"}">${d > 0 ? "▲ +" + d : d < 0 ? "▼ " + d : "как вчера"}</span></button>`; };
  $("#main").innerHTML = `
    <div class="dhead"><div><h2>${new Date().getHours() < 12 ? "Доброе утро" : new Date().getHours() < 18 ? "Добрый день" : "Добрый вечер"}</h2><p class="muted">${open ? `Ждут решения: <b>${open}</b>` : "Очередей нет"} · людей на сайте: <b>${fmtN(s.users)}</b> · платящих: <b>${s.payers}</b></p></div>
      <div class="quick2"><button class="pulse2" id="pulseBtn"><span class="dot" id="pulseDot"></span><span id="pulseText">проверяем…</span></button><button data-go="listings">📋 Очередь</button><button data-go="users">👥 Найти человека</button><button id="quickPal">⌘ Команды</button></div></div>
    <div class="todaybar">${tod("Новые люди сегодня", "users", "users")}${tod("Новые объявления", "listings", "listings")}${tod("Отклики", "applications", "alllist")}${tod("Выручка сегодня", "stars", "reportsm", " ⭐")}</div>
    <div class="cols2">
      <div class="card"><div class="sect2"><h3>Очереди: что делать сейчас</h3></div>
        <table class="queues"><tr><th></th><th>Очередь</th><th>Сейчас</th><th>Ждёт</th><th></th></tr>${rows.map((r) => `<tr class="${r.n ? "" : "zero"}"><td>${r.ic}</td><td><b>${r.name}</b><br><small class="muted">${r.hint}</small></td>
          <td><span class="qn ${r.wait >= 24 ? "hot" : r.n ? "warm" : ""}">${r.n}</span></td><td>${r.wait ? `<span class="${r.wait >= 24 ? "bad" : ""}">${r.wait} ч</span>` : "—"}</td><td><button class="act ${r.n ? "" : "gray"}" data-go="${r.tab}">${r.n ? "Взять →" : "Открыть"}</button></td></tr>`).join("")}</table></div>
      <div class="card"><div class="sect2"><h3>Сервер</h3><button class="act gray" data-go="errors">Ошибки</button></div><div id="sysBox"><p class="muted">Загрузка…</p></div></div>
    </div>
    ${sus.reported.length || sus.burst.length || sus.shared_contact.length ? `<div class="card susp"><h3>⚠ Подозрительное</h3><div class="cols3">
      <div><b>Несколько жалоб на человека</b>${sus.reported.map((x) => `<div class="drun"><a href="#" data-gu="${esc(x.tg_id)}">${esc(x.name || "ID …" + x.tg_id.slice(-4))}</a><span class="bad">${x.reports} жалоб</span></div>`).join("") || '<p class="muted">нет</p>'}</div>
      <div><b>Всплеск публикаций за час</b>${sus.burst.map((x) => `<div class="drun"><a href="#" data-gu="${esc(x.tg_id)}">ID …${esc(x.tg_id.slice(-4))}</a><span class="bad">${x.listings} объявл.</span></div>`).join("") || '<p class="muted">нет</p>'}</div>
      <div><b>Один контакт у разных людей</b>${sus.shared_contact.map((x) => `<div class="drun"><span>${esc(x.contact)}</span><span class="bad">${x.accounts} аккаунта</span></div>`).join("") || '<p class="muted">нет</p>'}</div></div></div>` : ""}
    <div class="card chartcard"><div class="head"><h3 style="margin:0">Динамика</h3><div style="display:flex;gap:8px;flex-wrap:wrap"><div class="seg" id="dashDays">${[7, 30, 90].map((d) => `<button class="${d === dashDays ? "on" : ""}" data-dd="${d}">${d} дн.</button>`).join("")}</div>
      <div class="seg" id="dashMetric">${Object.entries(METRICS).map(([k, [n]]) => `<button class="${k === dashMetric ? "on" : ""}" data-dm="${k}">${n}</button>`).join("")}</div></div></div>
      ${bigChart(days, dashMetric)}<p class="muted" style="margin:6px 4px 0">За период: людей ${fmtN(a.totals.users)}, объявлений ${fmtN(a.totals.listings)}, откликов ${fmtN(a.totals.applications)}, выручка ${fmtN(a.totals.stars)} ⭐. Проведите по графику, чтобы увидеть день.</p></div>
    <div class="cols">
      <div class="card"><h3>Путь человека: от просмотра до оплаты</h3>${[["👁 Просмотры", f.views, "listings"], ["💬 Чаты", f.chats, "listings"], ["📨 Отклики", f.applications, "alllist"], ["⭐ Оплаты", f.paid, "payments"]].map(([name, v, go], i, arr) => {
        const top = Math.max(1, f.views), prev = i ? arr[i - 1][1] : null;
        return `<button class="fstep" data-go="${go}"><span class="lab">${name}</span><span class="bar" style="width:${Math.max(6, Math.round(v / top * 100) * 0.62)}%">${fmtN(v)}</span><span class="cv">${prev ? (prev ? Math.round(v / prev * 1000) / 10 : 0) + "% от шага выше" : ""}</span></button>`; }).join("")}</div>
      <div class="card"><h3>Что происходит</h3><div class="feed">${audit.slice(0, 9).map((x, i) => `<button data-feed="${i}"><span class="fi">${ACT_ICON[x.object_type] || "•"}</span><span class="ft"><b>${esc(actorName(x.actor))}</b> ${esc(ACT_TEXT(x))} <span class="muted">${esc(x.object_type)} ${esc(x.object_id)}</span><small>${esc(fmt(x.created_at))}</small></span></button>`).join("") || '<p class="muted">Пока тихо.</p>'}</div></div>
    </div>
    <div class="cols">
      <div class="card"><h3>Деньги по услугам</h3>${a.stars_by_service.length ? a.stars_by_service.slice(0, 7).map((x) => { const top = Math.max(1, a.stars_by_service[0].stars); return `<button class="hbar" data-go="reportsm"><span class="nm">${esc(x.service)}</span><span class="tr"><i style="width:${Math.round(x.stars / top * 100)}%"></i></span><span class="vl">${x.stars} ⭐</span></button>`; }).join("") : '<p class="muted">Продаж не было.</p>'}
        <p class="muted">Всего: ${fmtN(s.revenue_stars_total)} ⭐ · за неделю ${s.week.stars} ⭐ (${s.week.sales} продаж)</p></div>
      <div class="card"><h3>Откуда приходят</h3>${(a.sources || []).length ? a.sources.slice(0, 7).map((x) => { const top = Math.max(1, a.sources[0].users); return `<div class="hbar" style="cursor:default"><span class="nm">${esc(x.source)}</span><span class="tr"><i style="width:${Math.round(x.users / top * 100)}%"></i></span><span class="vl">${x.users}</span></div>`; }).join("") : '<p class="muted">Меток пока нет. Ссылка для рекламы: t.me/ИМЯ_БОТА?start=src_МЕТКА</p>'}</div>
    </div>
    <div class="card"><h3>Быстрая проверка очереди</h3>${queue.length ? `<table><tr><th>№</th><th>Раздел</th><th>Название</th><th>Город</th><th></th></tr>${queue.slice(0, 6).map((x) => `
      <tr data-id="${x.id}"><td>${x.id}</td><td>${esc(CAT[x.category] || x.category)}</td><td><a href="#" data-gl-open="${x.id}">${esc(x.title)}</a><div data-ctx></div></td><td>${esc(x.city)}</td>
      <td><button class="act ok" data-quick="approve">Одобрить</button><button class="act no" data-quick="reject">Отклонить</button></td></tr>`).join("")}</table>` : '<p class="muted">Очередь пуста.</p>'}</div>`;
  window.__auditRows = audit;
  bindChart(days, dashMetric);
  clearInterval(sysTimer);
  fillSystemCard();
  sysTimer = setInterval(fillSystemCard, 15000);
}
async function fillPulse() {
  try {
    const x = await api("/api/admin/system");
    const bad = !x.db || !x.bot_ok, warn = (x.disk_free_mb != null && x.disk_free_mb < 2000) || (x.cert_days != null && x.cert_days < 14) || x.client_errors_24h > 5;
    const dot = $("#pulseDot"), txt = $("#pulseText");
    if (!dot) return;
    dot.className = "dot " + (bad ? "bad" : warn ? "warn" : "");
    txt.textContent = bad ? `Проблема: ${!x.db ? "база недоступна" : "бот не отвечает"}` : warn ? "Есть замечания к системе" : "Система в порядке";
    $("#pulseBtn").title = `База: ${x.db ? "ок" : "нет"} · Бот: ${x.bot_age_s == null ? "нет сигнала" : x.bot_age_s + " с назад"} · Диск: ${x.disk_free_mb == null ? "—" : Math.round(x.disk_free_mb / 1024) + " ГБ"} · Ошибки за сутки: ${x.client_errors_24h}`;
  } catch (e) { const t = $("#pulseText"); if (t) t.textContent = "Состояние неизвестно"; }
}
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go && current === "dash") { const t = go.dataset.go; if (TABS.some((x) => x.key === t)) show(t); return; }
  if (e.target.closest("#quickPal")) { palOpen(); return; }
  if (e.target.closest("#pulseBtn")) { say($("#pulseBtn").title); return; }
  const dd = e.target.closest("[data-dd]");
  if (dd) { dashDays = Number(dd.dataset.dd); loadDash(); return; }
  const dm = e.target.closest("[data-dm]");
  if (dm) { dashMetric = dm.dataset.dm; loadDash(); return; }
  const feed = e.target.closest("[data-feed]");
  if (feed) { openAudit(window.__auditRows[Number(feed.dataset.feed)]); }
});
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-quick]");
  if (!btn) return;
  const id = btn.closest("[data-id]").dataset.id;
  try {
    if (btn.dataset.quick === "approve") await post(`/api/admin/listings/${id}/approve`);
    else { const reason = ask("Причина отказа (автор её увидит):"); if (!reason) return; await post(`/api/admin/listings/${id}/reject?reason=${encodeURIComponent(reason)}`); }
    reload();
  } catch (err) { say(err.message); }
});

// ---- управление объявлением ----
function factsOf(x) {
  const d = x.details || {};
  const bits = [];
  if (x.price_amount != null) bits.push(new Intl.NumberFormat("ru-RU").format(x.price_amount) + " ₩");
  if (x.category === "job") {
    bits.push(...[d.company, d.position, x.schedule, x.employment].filter(Boolean));
    if ((x.visas || []).length) bits.push("визы " + x.visas.join(", "));
    if (x.housing_provided) bits.push("жильё");
    if (x.meals_provided) bits.push("питание");
  } else if (x.category === "housing") {
    bits.push(...[x.housing_type, x.deposit ? "залог " + x.deposit : "", x.area_m2 ? x.area_m2 + " м²" : "", d.station].filter(Boolean));
  } else {
    bits.push(...[x.goods_category, x.condition, d.brand, d.model, d.year, d.mileage_km ? d.mileage_km + " км" : "",
                  d.service_area ? "выезд: " + d.service_area : "", d.experience_years != null ? "опыт " + d.experience_years + " лет" : ""].filter(Boolean));
  }
  return bits.join(" · ") || "—";
}
let TEMPLATES = [];
function listingCardHtml(x, opts = {}) {
  const edit = x.status === "approved" && x.has_edit;
  const diff = edit ? Object.entries(x.pending_changes || {}).map(([k, v]) =>
    `<li><b>${esc(DIFF_LABELS[k] || k)}:</b> ${esc(typeof v === "object" ? JSON.stringify(v) : String(v))}</li>`).join("") : "";
  const photos = (x.photos || []).filter((p) => p.status !== "removal").map((p) =>
    `<a href="${esc(p.url)}" target="_blank"><img src="${esc(p.thumb)}" alt="" style="${p.status === "pending" ? "outline:2px solid orange" : ""}"></a>`).join("");
  const waiting = (x.photos || []).filter((p) => p.status === "pending" || p.status === "removal").length;
  const tpl = `<select data-template><option value="">Шаблон причины…</option>${TEMPLATES.map((t) => `<option value="${esc(t.key)}">${esc(t.text.slice(0, 50))}</option>`).join("")}</select>`;
  const buttons = (edit || x.status === "pending")
    ? `${tpl}<button class="act ok" data-act="approve">${edit ? "Одобрить правку" : "Одобрить"}</button>
       <button class="act warn" data-act="needs_fix">${edit ? "Отклонить правку" : "На доработку"}</button>
       <button class="act no" data-act="reject">Отклонить</button>`
    : x.status === "approved" ? '<button class="act no" data-act="hide">Скрыть</button>'
    : x.status === "hidden" ? '<button class="act ok" data-act="show">Показать</button>' : "";
  return `<div class="card" data-id="${x.id}">
    ${opts.select ? `<label style="float:right"><input type="checkbox" data-pick> выбрать</label>` : ""}
    <b>#${x.id} ${esc(x.title)}</b> <span class="badge">${esc(STATUS_RU[x.status] || x.status)}${edit ? " · правка" : ""}</span>
    <div class="meta">${esc(CAT[x.category] || x.category)} · ${esc(x.city)} · ${esc(x.price || "цена не указана")}</div>
    <div class="meta">${esc(factsOf(x))}</div>
    ${(x.flags || []).length ? `<div style="color:#b45309;font-weight:600">⚠ ${(x.flags || []).map(esc).join(" · ")}</div>` : ""}
    ${edit ? `<div><b>Что меняется:</b><ul>${diff || "<li>только фото</li>"}</ul></div>` : `<p>${esc(x.description || "")}</p>`}
    <div class="thumbs">${photos || '<span class="muted">Фото нет</span>'}${waiting ? ` <span class="muted">(ждут решения: ${waiting})</span>` : ""}</div>
    ${photos ? '<button class="act gray" data-banphotos>🚫 Запретить эти фото</button>' : ""}
    <p class="muted">Контакт: ${esc(x.contact || "—")} · Автор: <a href="#" data-gu="${esc(x.author_id || "")}">${who(x.author_name, x.author_id || "—")}</a>${x.author_rejects ? ` · <span class="bad">отказов у автора: ${x.author_rejects}</span>` : ""} · просмотров: ${x.views || 0}${x.note ? " · комментарий: " + esc(x.note) : ""}</p>
    ${buttons}${extraButtons(x)}<div data-editbox></div></div>`;
}
const hoursSince = (iso) => (iso ? Math.max(0, Math.floor((Date.now() - new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime()) / 3600000)) : 0);
function extraButtons(x) {
  const wait = x.status === "pending" || x.has_edit ? `<span class="wait ${hoursSince(x.created_at) >= 24 ? "late" : ""}">ждёт ${hoursSince(x.created_at)} ч</span> ` : "";
  const undo = ["approved", "rejected", "needs_fix", "hidden"].includes(x.status) ? '<button class="act gray" data-undo>↶ Отменить решение</button>' : "";
  const claimed = claimsMap[x.id] ? `<span class="wait late">🔒 в работе: ${esc(claimsMap[x.id])}</span> ` : '<button class="act gray" data-claim>🔒 Взять в работу</button>';
  return `<div class="row" style="margin-top:6px">${wait}${claimed}<button class="act gray" data-edit>✏ Править</button><button class="act gray" data-preview="${x.id}">👁 Предпросмотр</button>${undo}</div>`;
}
let claimsMap = {};
async function loadClaims(kind) { try { claimsMap = await api("/api/admin/claims?kind=" + kind); } catch (e) { claimsMap = {}; } }
$("#main").addEventListener("click", async (e) => {
  const card = e.target.closest("[data-id]");
  if (!card) return;
  const id = card.dataset.id;
  try {
    if (e.target.closest("[data-claim]")) { await post("/api/admin/claim", {kind: "listing", id}); say("Вы взяли объявление в работу на 15 минут"); claimsMap[id] = "вы"; e.target.closest("[data-claim]").outerHTML = '<span class="wait late">🔒 в работе: вы</span>'; }
    if (e.target.closest("[data-undo]")) { if (!confirm("Отменить последнее решение по этому объявлению?")) return; await post(`/api/admin/listings/${id}/undo`); say("Решение отменено"); reload(); }
    if (e.target.closest("[data-edit]")) {
      const box = card.querySelector("[data-editbox]");
      if (box.innerHTML) { box.innerHTML = ""; return; }
      const x = await api("/api/admin/listings/" + id);
      box.innerHTML = `<div class="editbox"><input data-ef="title" value="${esc(x.title)}" maxlength="120"><textarea data-ef="description" rows="5" maxlength="3000">${esc(x.description || "")}</textarea>
        <input data-ef="city" value="${esc(x.city)}" maxlength="100"><input data-ef="price" value="${esc(x.price || "")}" maxlength="80" placeholder="Цена (текст)"><input data-ef="contact" value="${esc(x.contact || "")}" maxlength="160">
        <button class="act ok" data-esave>Сохранить правку</button> <span class="muted">Автор получит уведомление о правке</span></div>`;
    }
    if (e.target.closest("[data-esave]")) {
      const body = {};
      card.querySelectorAll("[data-ef]").forEach((el) => { body[el.dataset.ef] = el.value; });
      const r = await api(`/api/admin/listings/${id}/edit`, {method: "PUT", body: JSON.stringify(body)});
      say(r.changed.length ? "Сохранено: " + r.changed.join(", ") : "Изменений нет"); reload();
    }
  } catch (err) { say(err.message); }
});
function reasonFrom(card, act) {
  const key = card.querySelector("[data-template]")?.value;
  if (key) return TEMPLATES.find((t) => t.key === key)?.text || "";
  return ask(act === "reject" ? "Причина отказа (автор её увидит):" : act === "hide" ? "Причина, почему скрыть (автор её увидит):" : "Что исправить (автор её увидит):");
}
$("#main").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-banphotos]");
  if (!b) return;
  const card = b.closest("[data-id]");
  if (!(await Promise.resolve(confirm("Фото будут удалены, и такие же загрузить больше нельзя. Продолжить?")))) return;
  try { const r = await post(`/api/admin/listings/${card.dataset.id}/ban-photos`); say(`Запрещено фото: ${r.banned}`); reload(); } catch (err) { say(err.message); }
});
async function listingAction(e) {
  const btn = e.target.closest("[data-act]");
  if (!btn || !btn.closest("[data-id]")) return;
  const card = btn.closest("[data-id]");
  const act = btn.dataset.act;
  let url = `/api/admin/listings/${card.dataset.id}/${act}`;
  if (act !== "approve" && act !== "show") {
    const reason = reasonFrom(card, act);
    if (!reason) return;
    url += "?reason=" + encodeURIComponent(reason);
  }
  try { await post(url); reload(); } catch (err) { say(err.message); }
}

// ---- модерация: очередь, поиск, массовые действия ----
let queueFilter = {category: "", city: "", sort: "old"};
$("#main").addEventListener("submit", (e) => {
  if (e.target.id !== "queueFilter") return;
  e.preventDefault();
  queueFilter = Object.fromEntries(new FormData(e.target).entries());
  reload();
});
async function loadListings() {
  await loadClaims("listing");
  TEMPLATES = await api("/api/admin/templates");
  const qs = new URLSearchParams(Object.entries(queueFilter).filter(([, v]) => v));
  const rows = await api("/api/admin/listings?" + qs);
  $("#main").innerHTML = `
    <form id="queueFilter" class="filters"><select name="category"><option value="">Все разделы</option><option value="job" ${queueFilter.category === "job" ? "selected" : ""}>Работа</option><option value="housing" ${queueFilter.category === "housing" ? "selected" : ""}>Жильё</option><option value="marketplace" ${queueFilter.category === "marketplace" ? "selected" : ""}>Барахолка</option></select>
      <input name="city" placeholder="Город" value="${esc(queueFilter.city)}" style="width:140px"><select name="sort"><option value="old" ${queueFilter.sort === "old" ? "selected" : ""}>Сначала старые</option><option value="new" ${queueFilter.sort === "new" ? "selected" : ""}>Сначала новые</option></select>
      <button class="act" type="submit">Применить</button></form>
    <div class="card"><b>Найти объявление по номеру</b>
      <div class="row"><input id="findId" type="number" min="1" placeholder="Например: 125" style="width:140px"><button class="act" id="findBtn">Найти</button></div><div id="found"></div></div>
    <div class="toolbar"><label><input type="checkbox" id="pickAll"> выбрать все</label>
      <button class="act ok" id="bulkOk">Одобрить выбранные</button>
      <select id="bulkTpl"><option value="">Шаблон для отказа…</option>${TEMPLATES.map((t) => `<option value="${esc(t.key)}">${esc(t.text.slice(0, 55))}</option>`).join("")}</select>
      <button class="act no" id="bulkNo">Отклонить выбранные</button><span class="muted" id="bulkInfo">Всего: ${rows.length}</span></div>
    <div id="queue">${rows.length ? rows.map((x) => listingCardHtml(x, {select: true})).join("") : '<div class="card muted">Очередь модерации пуста.</div>'}</div>`;
  $("#findBtn").onclick = async () => {
    const id = $("#findId").value;
    if (!id) return;
    try { $("#found").innerHTML = listingCardHtml(await api(`/api/admin/listings/${id}`)); }
    catch (err) { $("#found").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  };
  $("#pickAll").onchange = (e) => document.querySelectorAll("#queue [data-pick]").forEach((c) => { c.checked = e.target.checked; });
  const picked = () => [...document.querySelectorAll("#queue [data-pick]:checked")].map((c) => Number(c.closest("[data-id]").dataset.id));
  const run = async (decision, body = {}) => {
    const ids = picked();
    if (!ids.length) { say("Отметьте объявления"); return; }
    try {
      const r = await post("/api/admin/listings/bulk", {ids, decision, ...body});
      say(`Готово: ${r.done.length}` + (r.failed.length ? `, не удалось: ${r.failed.map((f) => "#" + f.id + " " + f.error).join("; ")}` : ""));
      reload();
    } catch (err) { say(err.message); }
  };
  $("#bulkOk").onclick = () => run("approve");
  $("#bulkNo").onclick = () => {
    const template = $("#bulkTpl").value;
    if (template) return run("reject", {template});
    const reason = ask("Причина отказа для всех выбранных:");
    if (reason) run("reject", {reason});
  };
}
$("#main").addEventListener("click", listingAction);

// ---- вакансии, жильё, барахолка ----
async function loadSection(def) {
  await loadClaims("listing");
  TEMPLATES = TEMPLATES.length ? TEMPLATES : await api("/api/admin/templates");
  $("#main").innerHTML = `<div class="toolbar"><select id="fStatus"><option value="">Любой статус</option>
    ${Object.entries(STATUS_RU).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
    <input id="fQuery" placeholder="Слово в названии или тексте"><button class="act" id="fGo">Показать</button><span class="muted" id="fTotal"></span></div><div id="list"></div>`;
  const go = async () => {
    const q = new URLSearchParams({category: def.cat, limit: "50"});
    if ($("#fStatus").value) q.set("status", $("#fStatus").value);
    if ($("#fQuery").value.trim()) q.set("q", $("#fQuery").value.trim());
    const r = await api("/api/admin/listing-list?" + q);
    $("#fTotal").textContent = "Найдено: " + r.total;
    $("#list").innerHTML = r.items.length ? r.items.map((x) => listingCardHtml(x)).join("") : '<div class="card muted">Ничего нет.</div>';
  };
  $("#fGo").onclick = () => go().catch((e) => say(e.message));
  await go();
}

// ---- знакомства ----
async function loadDating() {
  const rows = await api("/api/admin/dating?status=pending");
  $("#main").innerHTML = rows.length ? rows.map((p) => `
    <div class="card" data-pid="${p.id}">
      <b>Анкета #${p.id}${p.name ? " · " + esc(p.name) : ""}</b> · ${p.age} лет · ${p.gender === "f" ? "ж" : "м"} · ${esc(p.city)}
      <p>${esc(p.bio)}</p>
      <div class="row"><img data-photo="${p.id}" alt="" style="max-width:220px;border-radius:10px" ${p.has_photo ? "" : "hidden"}>
      ${p.selfie_status === "pending" ? `<div><div class="muted">Фото-подтверждение (видите только вы)</div><img data-selfie="${p.id}" alt="" style="max-width:220px;border-radius:10px"></div>` : ""}</div>
      <p>${p.status === "pending" ? '<button class="act ok" data-dec="approve">Опубликовать</button><button class="act no" data-dec="reject">Отклонить</button>' : ""}
      ${p.selfie_status === "pending" ? '<button class="act ok" data-dec="verify">✔ Тот же человек</button><button class="act warn" data-dec="reject_selfie">Селфи не подходит</button>' : ""}${p.selfie_status === "approved" ? '<span class="badge">✔ проверено</span>' : ""}</p>
    </div>`).join("") : '<div class="card muted">Анкет на проверке нет.</div>';
  for (const img of document.querySelectorAll("img[data-photo]")) {
    if (img.hidden) continue;
    const r = await fetch(`/api/admin/dating/${img.dataset.photo}/photo`, {headers: {"X-Admin-Session": session()}});
    if (r.ok) img.src = URL.createObjectURL(await r.blob());
  }
  for (const img of document.querySelectorAll("img[data-selfie]")) {
    const r = await fetch(`/api/admin/dating/${img.dataset.selfie}/selfie`, {headers: {"X-Admin-Session": session()}});
    if (r.ok) img.src = URL.createObjectURL(await r.blob());
  }
}
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-dec]");
  if (!btn) return;
  let q = "";
  if (btn.dataset.dec === "reject") { const why = ask("Причина отказа (автор анкеты её увидит)"); if (!why) return; q = "?reason=" + encodeURIComponent(why); }
  try { await post(`/api/admin/dating/${btn.closest("[data-pid]").dataset.pid}/${btn.dataset.dec}${q}`); reload(); } catch (err) { say(err.message); }
});

// ---- работодатели ----
async function loadEmployers() {
  const rows = await api("/api/admin/employers?status=pending");
  $("#main").innerHTML = rows.length ? rows.map((u) => `
    <div class="card" data-eid="${esc(u.tg_id)}"><b>${esc(u.company_name)}</b> · рег. номер ${esc(u.registration_no)}
      <div class="meta">Контакт: ${esc(u.contact_person || "не указан")} · <a href="#" data-gu="${esc(u.tg_id)}">управление человеком (ID ${esc(u.tg_id)})</a></div>
      <button class="act ok" data-emp="approve">Подтвердить</button><button class="act no" data-emp="reject">Отклонить</button></div>`).join("")
    : '<div class="card muted">Заявок на проверке нет.</div>';
}
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-emp]");
  if (!btn) return;
  let q = "";
  if (btn.dataset.emp === "reject") { const why = ask("Причина отказа (человек её увидит):"); if (!why) return; q = "?reason=" + encodeURIComponent(why); }
  try { await post(`/api/admin/employers/${btn.closest("[data-eid]").dataset.eid}/${btn.dataset.emp}${q}`); reload(); } catch (err) { say(err.message); }
});

// ---- жалобы с контекстом ----
async function loadReports() {
  const rows = await api("/api/admin/reports?status=new");
  let summary = "";
  if (ADM.includes(role())) {
    try {
      const r = await api("/api/admin/reports-summary?days=30");
      summary = `<div class="card"><h3>Жалобы за 30 дней</h3><div class="kv"><b>Признано обоснованными</b><span>${r.justified_pct == null ? "—" : r.justified_pct + "%"}</span>
        <b>По причинам</b><span>${r.by_reason.map((x) => esc(x.reason) + ": " + x.n).join(" · ") || "—"}</span>
        <b>По объектам</b><span>${r.by_type.map((x) => esc(x.type) + ": " + x.n).join(" · ") || "—"}</span>
        <b>Чаще всего</b><span>${r.top.map((x) => esc(x.type) + " " + esc(x.id) + " (" + x.n + ")").join(" · ") || "—"}</span></div></div>`;
    } catch (err) { /* сводка необязательна */ }
  }
  $("#main").innerHTML = summary + (rows.length ? rows.map((r) => `
    <div class="card" data-rid="${r.id}"><b>Жалоба #${r.id}</b> · причина: <b>${esc(REASON_RU[r.reason] || r.reason)}</b>
      <div class="meta">${esc(fmt(r.created_at))} · от ${who(r.reporter_name, r.reporter)} · на ${esc({listing: "объявление", user: "человека", review: "отзыв", dating: "анкету", message: "сообщение"}[r.target_type] || r.target_type)}:
        ${r.target_link && r.target_link.startsWith("listing:") ? `<a href="#" data-gl-open="${r.target_link.slice(8)}">${esc(r.target_label)}</a> <button class="act gray" data-preview="${r.target_link.slice(8)}">👁 Предпросмотр</button>` : r.target_link && r.target_link.startsWith("user:") ? `<a href="#" data-gu="${esc(r.target_link.slice(5))}">${esc(r.target_label)}</a>` : esc(r.target_label || r.target_id)}</div>
      <p>${esc(r.comment || "без комментария")}</p><div data-ctx></div>
      <button class="act gray" data-rep="context">Показать контекст</button>
      <button class="act ok" data-rep="resolve">Принять (скрыть / заблокировать)</button>
      <button class="act no" data-rep="reject">Отклонить жалобу</button></div>`).join("") : '<div class="card muted">Новых жалоб нет.</div>');
}
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-rep]");
  if (!btn) return;
  const card = btn.closest("[data-rid]");
  try {
    if (btn.dataset.rep === "context") {
      const c = await api(`/api/admin/reports/${card.dataset.rid}/context`);
      card.querySelector("[data-ctx]").innerHTML = `<div class="meta">${c.listing ? `Объявление #${c.listing.id} «${esc(c.listing.title)}» (${esc(c.listing.status)}), автор ${esc(c.listing.author)}` : ""}</div>` +
        (c.messages.length ? `<table>${c.messages.map((m) => `<tr><td>${m.from === "reporter" ? "жалующийся" : "на кого жалоба"}</td><td>${esc(m.text)}</td><td>${esc(fmt(m.at))}</td></tr>`).join("")}</table>` : '<p class="muted">Переписки между ними нет.</p>');
      return;
    }
    const note = askOpt(btn.dataset.rep === "resolve" ? "Комментарий жалующемуся (что сделали)" : "Почему жалоба отклонена");
    if (note === null) return;
    await post(`/api/admin/reports/${card.dataset.rid}/${btn.dataset.rep}${note ? "?reason=" + encodeURIComponent(note) : ""}`);
    reload();
  } catch (err) { say(err.message); }
});

// ---- поддержка: обращения и оспаривания ----
async function loadSupport() {
  await loadClaims("ticket");
  const rows = await api("/api/admin/tickets?status=open");
  const answered = await api("/api/admin/tickets?status=answered");
  const card = (t) => `<div class="card" data-tid="${t.id}"><label style="float:right"><input type="checkbox" data-tpick> выбрать</label><b>${t.kind === "appeal" ? "Оспаривание" : "Обращение"} #${t.id}</b> · пользователь ${esc(t.user)} · <button class="act gray" data-gu="${esc(t.user)}">Управление</button>
    <span class="wait ${hoursSince(t.updated_at) >= 24 && t.status === "open" ? "late" : ""}">${t.status === "open" ? "ждёт " + hoursSince(t.updated_at) + " ч" : esc(fmt(t.updated_at))}</span>
    ${claimsMap[t.id] ? `<span class="wait late">🔒 ${esc(claimsMap[t.id])}</span>` : ""}
    <p>${esc(t.preview)}</p><button class="act gray" data-tk="open">Открыть</button><div data-tbox></div></div>`;
  $("#main").innerHTML = `<div class="toolbar"><button class="act gray" id="tBulkClose">Закрыть выбранные</button></div><h3>Ждут ответа (${rows.length})</h3>` + (rows.length ? rows.map(card).join("") : '<div class="card muted">Нет.</div>') +
    `<h3>Отвечено (${answered.length})</h3>` + (answered.length ? answered.map(card).join("") : '<div class="card muted">Нет.</div>');
}
$("#main").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-tk]");
  if (!b) return;
  const card = b.closest("[data-tid]");
  const id = card.dataset.tid;
  const box = card.querySelector("[data-tbox]");
  try {
    if (b.dataset.tk === "open") {
      try { await post("/api/admin/claim", {kind: "ticket", id}); } catch (err) { say(err.message); return; }   // два сотрудника не отвечают одному и тому же
      const t = await api(`/api/admin/tickets/${id}`);
      box.innerHTML = t.messages.map((m) => `<div class="meta"><b>${m.staff ? "Поддержка" : "Пользователь"}</b> ${esc(fmt(m.at))}</div><p style="white-space:pre-wrap">${esc(m.text)}</p>`).join("") +
        (t.listing ? `<div class="meta">Объявление #${t.listing.id} «${esc(t.listing.title)}» · статус: ${esc(STATUS_RU[t.listing.status] || t.listing.status)}${t.listing.note ? " · причина: " + esc(t.listing.note) : ""}</div>` : "") +
        `<textarea data-treply rows="3" style="width:100%" placeholder="Ответ пользователю"></textarea>
         <button class="act ok" data-tk="reply">Ответить</button>${t.listing ? '<button class="act warn" data-tk="restore">Вернуть объявление на проверку</button>' : ""}<button class="act gray" data-tk="close">Закрыть</button>`;
      return;
    }
    if (b.dataset.tk === "reply") {
      const text = card.querySelector("[data-treply]").value.trim();
      if (text.length < 2) { say("Напишите ответ"); return; }
      await post(`/api/admin/tickets/${id}/reply`, {text});
    }
    if (b.dataset.tk === "restore") await post(`/api/admin/tickets/${id}/restore-listing`);
    if (b.dataset.tk === "close") await post(`/api/admin/tickets/${id}/close`);
    reload();
  } catch (err) { say(err.message); }
});

$("#main").addEventListener("click", async (e) => {
  if (e.target.id === "tBulkClose") {
    const ids = [...document.querySelectorAll("[data-tpick]:checked")].map((c) => c.closest("[data-tid]").dataset.tid);
    if (!ids.length) { say("Отметьте обращения"); return; }
    try { const r = await post("/api/admin/tickets-bulk-close", {ids}); say("Закрыто: " + r.done); reload(); } catch (err) { say(err.message); }
  }
  if (e.target.id === "rBulkOk") {
    const ids = [...document.querySelectorAll("[data-rpick]:checked")].map((c) => c.closest("[data-owner]").dataset.owner);
    if (!ids.length) { say("Отметьте резюме"); return; }
    try { const r = await post("/api/admin/resumes-bulk/approve", {ids}); say("Опубликовано: " + r.done); reload(); } catch (err) { say(err.message); }
  }
});

// ---- тексты, FAQ, курс, автомодерация ----
async function loadTexts() {
  const [st, faq, staff] = await Promise.all([api("/api/admin/app-settings"), api("/api/admin/faq"), api("/api/admin/staff-stats")]);
  $("#main").innerHTML = `
    <div class="card"><h3>Тексты в приложении</h3>
      <label>Заголовок приветствия<input data-set="welcome_title" value="${esc(st.welcome_title)}" maxlength="200"></label>
      <label>Текст приветствия (показывается один раз новому пользователю)<textarea data-set="welcome_text" rows="5">${esc(st.welcome_text)}</textarea></label>
      <label>Подсказка в окне поддержки<textarea data-set="support_note" rows="2">${esc(st.support_note)}</textarea></label></div>
    <div class="card"><h3>Курс воны</h3><p class="muted">Сколько рублей и долларов в одной воне. Пусто — не показывать пересчёт цены.</p>
      <label>₽ за 1 ₩ (например 0.065)<input data-set="krw_rub" value="${esc(st.krw_rub)}"></label>
      <label>$ за 1 ₩ (например 0.00072)<input data-set="krw_usd" value="${esc(st.krw_usd)}"></label></div>
    <div class="card"><h3>Автоматическая проверка</h3>
      <p class="muted">Новые объявления проверенных авторов публикуются сразу, если нет подозрений (дубли текста и фото, общий контакт, недавние отказы). Правки уже опубликованных объявлений всегда идут к модератору.</p>
      <label><input type="checkbox" data-setbool="automod_enabled" ${st.automod_enabled === "1" ? "checked" : ""}> Включить</label>
      <label>Достаточно опубликованных объявлений у автора<input data-set="automod_min_published" type="number" min="1" max="50" value="${esc(st.automod_min_published)}"></label></div>
    <button class="act ok" id="saveTexts">Сохранить</button>
    <div class="card"><h3>Вопросы и ответы</h3>${faq.map((f) => `<div class="card" data-faq="${f.id}">
      <input data-fq value="${esc(f.question)}"><textarea data-fa rows="3">${esc(f.answer)}</textarea>
      <label><input type="checkbox" data-fact ${f.active ? "checked" : ""}> показывать</label>
      <button class="act ok" data-faqsave>Сохранить</button><button class="act no" data-faqdel>Удалить</button></div>`).join("") || '<p class="muted">Пока пусто.</p>'}
      <h4>Добавить</h4><input id="newFq" placeholder="Вопрос"><textarea id="newFa" rows="3" placeholder="Ответ"></textarea><button class="act ok" id="addFaq">Добавить</button></div>
    <div class="card"><h3>Работа сотрудников (30 дней)</h3>${staff.length ? `<table><tr><th>Кто</th><th>Решений</th><th>Ответов</th></tr>${staff.map((x) => `<tr><td>${esc(x.actor)}</td><td>${x.decisions}</td><td>${x.replies}</td></tr>`).join("")}</table>` : '<p class="muted">Пока нет данных.</p>'}</div>`;
}
$("#main").addEventListener("click", async (e) => {
  const t = e.target;
  try {
    if (t.id === "saveTexts") {
      const values = {};
      $("#main").querySelectorAll("[data-set]").forEach((el) => { values[el.dataset.set] = el.value; });
      $("#main").querySelectorAll("[data-setbool]").forEach((el) => { values[el.dataset.setbool] = el.checked ? "1" : "0"; });
      await api("/api/admin/app-settings", {method: "PUT", body: JSON.stringify({values})});
      say("Сохранено");
    }
    if (t.id === "addFaq") {
      await post("/api/admin/faq", {question: $("#newFq").value.trim(), answer: $("#newFa").value.trim(), position: 0, active: true});
      reload();
    }
    const card = t.closest("[data-faq]");
    if (card && t.hasAttribute("data-faqsave")) {
      await api(`/api/admin/faq/${card.dataset.faq}`, {method: "PUT", body: JSON.stringify({question: card.querySelector("[data-fq]").value.trim(),
        answer: card.querySelector("[data-fa]").value.trim(), position: 0, active: card.querySelector("[data-fact]").checked})});
      say("Сохранено");
    }
    if (card && t.hasAttribute("data-faqdel")) { await api(`/api/admin/faq/${card.dataset.faq}`, {method: "DELETE"}); reload(); }
  } catch (err) { say(err.message); }
});

// ---- резюме ----
let resFilter = "pending", resQuery = "";
async function loadResumes() {
  const rows = await api(`/api/admin/resumes-all?status=${resFilter}&q=${encodeURIComponent(resQuery)}`);
  window.__res = Object.fromEntries(rows.map((r) => [r.owner, r]));
  const ST = {pending: "на проверке", approved: "опубликовано", rejected: "отклонено", hidden: "скрыто"};
  $("#main").innerHTML = `<div class="card"><div class="row">${[["pending", "На проверке"], ["approved", "Опубликованные"], ["hidden", "Скрытые"], ["rejected", "Отклонённые"], ["all", "Все"]]
    .map(([k, n]) => `<button class="act ${k === resFilter ? "" : "gray"}" data-resf="${k}">${n}</button>`).join("")}
    <input id="resQ" placeholder="Поиск: должность, город, ID" value="${esc(resQuery)}" style="min-width:200px"><button class="act gray" id="resGo">Найти</button></div></div>` +
    (resFilter === "pending" && rows.length ? '<div class="toolbar"><button class="act ok" id="rBulkOk">Опубликовать выбранные</button></div>' : "") +
    (rows.length ? rows.map((r) => `<div class="card" data-owner="${esc(r.owner)}">${r.status === "pending" ? '<label style="float:right"><input type="checkbox" data-rpick> выбрать</label>' : ""}<b>${esc(r.position)}</b> · ${esc(r.city)} · виза ${esc(r.visa || "—")} · от ${r.salary_from ?? "—"} ₩ · <span class="badge">${esc(ST[r.status] || r.status)}</span>
    <div class="meta">${esc(r.name || "")} ${esc(r.owner)} · ${esc(fmt(r.updated))}${r.note ? " · " + esc(r.note) : ""}</div><p>${esc(r.experience)}</p><p>${esc(r.about)}</p>
    ${r.status !== "approved" ? '<button class="act ok" data-cv="approve">Опубликовать</button>' : '<button class="act gray" data-cv="hide">Скрыть</button>'}${r.status !== "rejected" ? '<button class="act no" data-cv="reject">Отклонить</button>' : ""}
    <button class="act" data-resedit="${esc(r.owner)}">✏ Править</button> <button class="act gray" data-gu="${esc(r.owner)}">Управление человеком</button></div>`).join("") : '<div class="card muted">Резюме нет.</div>');
}
function openResumeDrawer(owner) {
  const r = (window.__res || {})[owner]; if (!r) return;
  openDrawer(`Резюме · ${esc(r.name || owner)}`, `<div class="gridform" data-resbox="${esc(owner)}">
    <label>Должность<input data-rf="position" value="${esc(r.position)}" maxlength="80"></label><label>Город<input data-rf="city" value="${esc(r.city)}"></label>
    <label>Виза<input data-rf="visa" value="${esc(r.visa || "")}" maxlength="8"></label><label>Зарплата от, ₩<input data-rf="salary_from" type="number" value="${r.salary_from ?? ""}"></label>
    <label class="wide">Опыт<textarea data-rf="experience" rows="3" maxlength="300">${esc(r.experience)}</textarea></label>
    <label class="wide">О себе<textarea data-rf="about" rows="4" maxlength="700">${esc(r.about)}</textarea></label></div>
    <div class="row" data-resbox="${esc(owner)}"><button class="act ok" data-ressave>💾 Сохранить</button><button class="act no" data-resdel>🗑 Удалить резюме…</button></div>`);
}
$("#main").addEventListener("click", async (e) => {
  try {
    const f = e.target.closest("[data-resf]");
    if (f) { resFilter = f.dataset.resf; reload(); return; }
    if (e.target.id === "resGo") { resQuery = $("#resQ").value.trim(); reload(); return; }
    const ed = e.target.closest("[data-resedit]");
    if (ed) { openResumeDrawer(ed.dataset.resedit); return; }
    const box = e.target.closest("[data-resbox]");
    if (!box) return;
    const owner = box.dataset.resbox;
    if (e.target.closest("[data-ressave]")) {
      const body = {};
      document.querySelectorAll("#drawer [data-rf]").forEach((el) => { const v = el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value; if (v !== null) body[el.dataset.rf] = v; });
      body.reason = askOpt("Что поправили (человек увидит)") || "";
      const r = await api(`/api/admin/resumes/${owner}/edit`, {method: "PUT", body: JSON.stringify(body)});
      say("Сохранено: " + (r.changed.join(", ") || "без изменений")); closeDrawer(); reload();
    }
    if (e.target.closest("[data-resdel]")) {
      const why = ask("Причина удаления (человек её увидит)"); if (!why) return;
      if (!confirm("Удалить резюме?")) return;
      await api(`/api/admin/resumes/${owner}?reason=${encodeURIComponent(why)}`, {method: "DELETE"}); say("Удалено"); closeDrawer(); reload();
    }
  } catch (err) { say(err.message); }
});
$("#main").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-cv]");
  if (!b) return;
  const owner = b.closest("[data-owner]").dataset.owner;
  try {
    let q = "";
    if (b.dataset.cv === "reject") { const why = ask("Причина отказа (её увидит человек)"); if (!why) return; q = "?reason=" + encodeURIComponent(why); }
    await post(`/api/admin/resumes/${owner}/${b.dataset.cv}${q}`);
    reload();
  } catch (err) { say(err.message); }
});

// ---- отзывы ----
async function loadReviews() {
  const [shown, hidden] = await Promise.all([api("/api/admin/reviews?status=published"), api("/api/admin/reviews?status=hidden")]);
  const row = (r, act, label) => `<div class="card" data-revid="${r.id}"><b class="rating">${"★".repeat(r.rating)}</b> · «${esc(r.listing_title || "#" + r.listing_id)}» · о ${who(r.target_name, r.target)} · от ${who(r.reviewer_name, r.reviewer)}
    <div class="meta">${esc(fmt(r.created_at))}${r.tags ? " · " + esc(r.tags) : ""}</div><p>${esc(r.text || "(без текста)")}</p>${r.reply ? `<p class="muted"><b>Ответ автора:</b> ${esc(r.reply)}</p>` : ""}<button class="act gray" data-gu="${esc(r.reviewer)}">Автор отзыва</button> <button class="act gray" data-preview="${r.listing_id}">👁 Объявление</button> <button class="act" data-revedit>✏ Править текст</button> <button class="act ${act === "hide" ? "no" : "ok"}" data-rev="${act}">${label}</button></div>`;
  window.__revs = Object.fromEntries([...shown, ...hidden].map((r) => [r.id, r]));
  $("#main").innerHTML = `<h3>Опубликованы</h3>` + (shown.length ? shown.map((r) => row(r, "hide", "Скрыть")).join("") : '<div class="card muted">Пока нет.</div>') +
    `<h3>Скрытые</h3>` + (hidden.length ? hidden.map((r) => row(r, "show", "Вернуть")).join("") : '<div class="card muted">Нет.</div>') +
    `<h3>Отзывы о компаниях</h3>` + await companyReviewsAdmin();
}
$("#main").addEventListener("click", async (e) => {
  try {
    const ed = e.target.closest("[data-revedit]");
    if (ed) {
      const id = ed.closest("[data-revid]").dataset.revid, r = (window.__revs || {})[id]; if (!r) return;
      openDrawer(`Отзыв #${id}`, `<p class="muted">Меняются только слова (убрать грубость, телефон). Оценку ставит покупатель, она остаётся: ${"★".repeat(r.rating)}</p>
        <div class="gridform" data-revbox="${id}"><label class="wide">Текст отзыва<textarea data-rvf="text" rows="4" maxlength="500">${esc(r.text)}</textarea></label>
        <label class="wide">Ответ автора объявления<textarea data-rvf="reply" rows="3" maxlength="500">${esc(r.reply || "")}</textarea></label></div>
        <div class="row" data-revbox="${id}"><button class="act ok" data-revsave>💾 Сохранить</button><button class="act no" data-revdel>🗑 Удалить отзыв</button></div>`);
      return;
    }
    const bx = e.target.closest("[data-revbox]");
    if (bx) {
      const id = bx.dataset.revbox;
      if (e.target.closest("[data-revsave]")) {
        const body = {}; document.querySelectorAll("#drawer [data-rvf]").forEach((el) => { body[el.dataset.rvf] = el.value; });
        const r = await api(`/api/admin/reviews/${id}/edit`, {method: "PUT", body: JSON.stringify(body)}); say("Сохранено: " + (r.changed.join(", ") || "без изменений")); closeDrawer(); reload();
      }
      if (e.target.closest("[data-revdel]") && confirm("Удалить отзыв насовсем? Скрыть можно без удаления.")) { await api(`/api/admin/reviews/${id}`, {method: "DELETE"}); say("Удалено"); closeDrawer(); reload(); }
      return;
    }
  } catch (err) { say(err.message); return; }
  const b = e.target.closest("[data-rev]");
  if (!b) return;
  try { await post(`/api/admin/reviews/${b.closest("[data-revid]").dataset.revid}/${b.dataset.rev}`); reload(); } catch (err) { say(err.message); }
});

// ---- предпросмотр объявления: так его увидят люди ----
let previewId = null;
async function openPreview(id) {
  previewId = id;
  $("#previewTitle").textContent = "Предпросмотр #" + id;
  const r = await fetch(`/api/admin/listings/${id}/preview?with_changes=${$("#previewChanges").checked}`, {headers: {"X-Admin-Session": session()}});
  if (!r.ok) { say((await r.json().catch(() => ({}))).detail || "Не удалось открыть предпросмотр"); return; }
  $("#previewFrame").srcdoc = await r.text();
  $("#previewModal").hidden = false;
}
$("#previewClose").onclick = () => { $("#previewModal").hidden = true; $("#previewFrame").srcdoc = ""; };
$("#previewChanges").onchange = () => { if (previewId) openPreview(previewId); };
$("#previewModal").addEventListener("click", (e) => { if (e.target.id === "previewModal") $("#previewClose").click(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#previewModal").hidden) $("#previewClose").click(); });
$("#main").addEventListener("click", (e) => {
  const b = e.target.closest("[data-preview]");
  if (b) openPreview(b.dataset.preview).catch((err) => say(err.message));
});


// ---- блокировка: пресеты, срок и что затрагивает ----
async function openBlockDialog(id) {
  const cfg = await api("/api/admin/block-presets");
  const old = document.getElementById("blockBox"); if (old) old.remove();
  const box = document.createElement("div");
  box.id = "blockBox"; box.className = "pmodal";
  box.innerHTML = `<div class="pbox" style="height:auto;max-height:90vh;overflow:auto"><div class="pbar"><b>Блокировка человека</b><button class="act gray" id="bkClose">Закрыть</button></div>
    <div style="padding:14px"><div class="filters" id="bkPresets">${cfg.presets.map((p) => `<button class="act gray" data-bkp="${p.key}">${esc(p.title)}</button>`).join("")}</div>
      <div class="gridform"><label>Срок<select id="bkDays"><option value="1">1 день</option><option value="3">3 дня</option><option value="7">7 дней</option><option value="30">30 дней</option>
        ${cfg.can_permanent ? '<option value="0">бессрочно</option>' : ""}</select></label>
      <label>Причина (человек её не увидит, записывается в журнал)<input id="bkReason" maxlength="300"></label></div>
      <p class="muted">Что затрагивает блокировка:</p>
      ${Object.entries(cfg.cascade_text).map(([k, t]) => `<label style="display:block;margin:4px 0"><input type="checkbox" data-bkc="${k}"> ${esc(t)}</label>`).join("")}
      <div class="row"><button class="act no" id="bkGo">Заблокировать</button></div><p class="muted" id="bkErr"></p></div></div>`;
  document.body.appendChild(box);
  const apply = (p) => {
    $("#bkReason").value = p.reason; $("#bkDays").value = p.days == null ? "0" : String(p.days);
    if (p.days != null && ![1, 3, 7, 30].includes(p.days)) $("#bkDays").insertAdjacentHTML("beforeend", `<option value="${p.days}" selected>${p.days} дн.</option>`);
    box.querySelectorAll("[data-bkc]").forEach((c) => { c.checked = Boolean(p.cascade[c.dataset.bkc]); });
  };
  apply(cfg.presets.find((p) => p.key === "rules") || cfg.presets[0]);
  box.querySelectorAll("[data-bkp]").forEach((b) => { b.onclick = () => apply(cfg.presets.find((p) => p.key === b.dataset.bkp)); });
  $("#bkClose").onclick = () => box.remove();
  $("#bkGo").onclick = async () => {
    const d = Number($("#bkDays").value), cascade = {};
    box.querySelectorAll("[data-bkc]").forEach((c) => { cascade[c.dataset.bkc] = c.checked; });
    try {
      await post(`/api/admin/users/${id}/block-preset`, {permanent: d === 0, days: d || null, reason: $("#bkReason").value, cascade});
      box.remove(); say("Заблокирован"); openUserCard(id);
    } catch (err) { $("#bkErr").textContent = err.message; }
  };
}
// ---- управление человеком ----
async function openUserCard(id) {
  try {
    const u = await api(`/api/admin/users/${encodeURIComponent(id)}/card`);
    current = "usercard";
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
    $("#title").textContent = "Управление: " + (u.name || "ID " + u.tg_id);
    const blocked = u.blocked ? `<span class="bad">заблокирован${u.blocked_until ? " до " + esc(fmt(u.blocked_until)) : " бессрочно"}</span>${u.block_reason ? " · " + esc(u.block_reason) : ""}` : '<span class="good">активен</span>';
    $("#main").innerHTML = `<div class="row"><button class="act gray" data-backlist>← К списку людей</button></div><div class="card" data-uid="${esc(u.tg_id)}"><div class="kv">
      <b>ID</b><span>${esc(u.tg_id)}</span><b>Имя</b><span>${esc(u.name || "—")}</span><b>Город</b><span>${esc(u.city)}</span><b>Виза</b><span>${esc(u.visa || "—")}</span>
      <b>Статус</b><span>${blocked}${u.deleted ? " · аккаунт удалён" : ""}${u.trusted ? " · ✔ проверенный автор" : ""}${u.employer_status === "approved" ? " · работодатель " + esc(u.company) : ""}</span>
      <b>Регистрация</b><span>${esc(fmt(u.created_at))}</span><b>Был в сети</b><span>${esc(fmt(u.last_seen_at) || "—")}</span><b>Согласие с правилами</b><span>${u.consent ? "да" : "нет"}</span>
      <b>Объявлений</b><span>${u.listings.length}</span><b>Откликов</b><span>${u.applications}</span><b>Чатов</b><span>${u.chats}</span><b>В избранном</b><span>${u.favorites}</span>
      <b>Жалоб на него / от него</b><span>${u.reports_against} / ${u.reports_made}</span><b>Отзывов получено / написано</b><span>${u.reviews_got} / ${u.reviews_written}</span>
      <b>Личный баланс</b><span>${u.balance} KH</span>
      <b>Знакомства</b><span>${u.dating ? `анкета #${u.dating.id} · ${esc(u.dating.status)}${u.dating.paused ? " · на паузе" : ""}${u.dating.selfie === "approved" ? " · ✔ подтверждена" : ""}` : "анкеты нет"}</span>
      <b>Компании</b><span>${u.companies.length ? u.companies.map((c) => `${esc(c.name)} (${esc(c.role)}, ${esc(c.status)})`).join("; ") : "нет"}</span>
      <b>Вход на сайт</b><span>${u.email ? esc(u.email) + (u.has_password ? " · пароль задан" : " · без пароля") : "не настроен (только Telegram)"}</span>
      <b>Заплатил</b><span>${u.stars_paid} ⭐</span><b>Резюме</b><span>${u.has_resume ? "есть" : "нет"}</span></div>
      <div style="margin-top:10px">${u.blocked ? '<button class="act ok" data-ux="unblock">Разблокировать</button>' : '<button class="act no" data-ux="blockterms">🚫 Заблокировать…</button>'}
        <button class="act ok" data-ux="stars">KH Выдать / списать KH</button>
        ${u.email || u.has_password ? '<button class="act warn" data-ux="resetpw">Сбросить пароль</button><button class="act warn" data-ux="resetmail">Сбросить e-mail и пароль</button>' : ""}
        <button class="act gray" data-ux="${u.trusted ? "untrust" : "trust"}">${u.trusted ? "Снять «проверенный автор»" : "Отметить «проверенный автор»"}</button></div></div>
      <div class="card"><h3>Править профиль</h3><div class="gridform" id="ueForm">
        <label>Имя<input data-ue="display_name" value="${esc(u.name || "")}" maxlength="40"></label>
        <label>Виза<select data-ue="visa"><option value="">не указана</option>${["E-9", "E-7", "F-2", "F-4", "F-5", "F-6", "D-2", "D-4", "H-2", "C-4", "G-1"].map((v) => `<option value="${v}" ${u.visa === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <label>Город<select data-ue="region_code"><option value="">оставить: ${esc(u.city)}</option></select></label>
        ${u.employer_status === "approved" ? `<label>Компания<input data-ue="company_name" value="${esc(u.company || "")}" maxlength="160"></label>` : ""}</div>
        <label><input type="checkbox" id="ueAvatar"> убрать фото профиля</label><br>
        <button class="act ok" id="ueSave">Сохранить правку</button> <span class="muted">Человек получит уведомление</span></div>
      <div class="card"><h3>Платежи</h3>${u.payments.length ? `<table><tr><th>№</th><th>Услуга</th><th>⭐</th><th>Статус</th><th>Когда</th></tr>${u.payments.map((x) => `<tr><td>${x.id}</td><td>${esc(x.service)}</td><td>${x.stars}</td><td>${esc(PAY_RU[x.status] || x.status)}</td><td>${esc(x.at)}</td></tr>`).join("")}</table>` : '<p class="muted">Платежей нет.</p>'}</div>
      <div class="card"><h3>Заметки сотрудников</h3>${u.notes.map((n) => `<div class="meta"><b>${esc(n.author)}</b> ${esc(fmt(n.at))} <button class="act gray" data-delnote="${n.id}">убрать</button></div><p>${esc(n.text)}</p>`).join("") || '<p class="muted">Заметок нет.</p>'}
        <textarea id="noteText" rows="2" maxlength="500" placeholder="Например: предупреждён о телефонах в описании"></textarea><button class="act ok" id="noteAdd">Добавить заметку</button></div>
      <div class="card"><h3>Объявления</h3>${u.listings.map((x) => `<div>#${x.id} ${esc(x.title)} <span class="badge">${esc(STATUS_RU[x.status] || x.status)}</span> <button class="act gray" data-gl="${x.id}">Открыть</button><div data-gbox></div></div>`).join("") || '<p class="muted">Нет.</p>'}</div>
      <div class="card"><h3>Обращения в поддержку</h3>${u.tickets.map((t) => `<div>#${t.id} · ${esc(t.kind)} · ${esc(t.status)}</div>`).join("") || '<p class="muted">Нет.</p>'}</div>`;
    say("");
    fetch("/api/geo").then((r) => r.json()).then((g) => { const sel = document.querySelector('[data-ue="region_code"]'); if (sel) sel.insertAdjacentHTML("beforeend", g.regions.map((r) => `<option value="${esc(r.code)}">${esc(r.name)}</option>`).join("")); }).catch(() => {});
  } catch (err) { say(err.message); }
}
$("#main").addEventListener("click", async (e) => {
  if (e.target.id === "ueSave") {
    const holder0 = document.querySelector('#main > .card[data-uid]');
    const body = {clear_avatar: $("#ueAvatar").checked};
    document.querySelectorAll("[data-ue]").forEach((el) => { if (el.dataset.ue === "region_code") { if (el.value) body.region_code = el.value; } else body[el.dataset.ue] = el.value; });
    try { const r = await api(`/api/admin/users/${holder0.dataset.uid}/edit`, {method: "PUT", body: JSON.stringify(body)}); say(r.changed.length ? "Сохранено: " + r.changed.join("; ") : "Изменений нет"); openUserCard(holder0.dataset.uid); }
    catch (err) { say(err.message); }
    return;
  }
  const holder = document.querySelector('#main > .card[data-uid]');
  if (e.target.id === "noteAdd" && holder) {
    try { await post(`/api/admin/users/${holder.dataset.uid}/notes`, {text: $("#noteText").value.trim()}); openUserCard(holder.dataset.uid); } catch (err) { say(err.message); }
  }
  const dn = e.target.closest("[data-delnote]");
  if (dn && holder) { try { await api(`/api/admin/notes/${dn.dataset.delnote}`, {method: "DELETE"}); openUserCard(holder.dataset.uid); } catch (err) { say(err.message); } }
});

// ---- пользователи ----
let userFilter = {q: "", flt: "", sort: "new"}, userLimit = 30;
// ---- люди: таблица с колонками, сохранёнными видами и общим доступом ----
const USER_COLS = {name: ["Имя", (u) => `${u.name ? esc(u.name) : "<i>без имени</i>"}${u.trusted ? " ✔" : ""}`], tg_id: ["ID", (u) => esc(u.tg_id)], city: ["Город", (u) => esc(u.city)],
  status: ["Статус", (u) => u.deleted ? "удалён" : u.blocked ? `<b class="bad">блок${u.blocked_until ? " до " + esc(fmt(u.blocked_until).slice(0, 10)) : ""}</b>` : '<span class="good">активен</span>'],
  created: ["Регистрация", (u) => esc(fmt(u.created_at).slice(0, 10))], seen: ["Был в сети", (u) => esc(u.last_seen_at ? fmt(u.last_seen_at) : "—")],
  listings: ["Объявл.", (u) => u.listings], reports: ["Жалобы", (u) => u.reports_against ? `<b class="bad">${u.reports_against}</b>` : "0"],
  balance: ["Баланс KH", (u) => u.balance], company: ["Компания", (u) => esc(u.employer_status === "approved" ? u.company : "—")], consent: ["Правила", (u) => (u.consent ? "приняты" : "нет")]};
const USER_COLS_DEFAULT = ["name", "city", "status", "created", "seen", "listings", "reports"];
let userCols = (() => { try { const c = JSON.parse(localStorage.getItem("adminUserCols") || "null"); return Array.isArray(c) && c.length ? c.filter((k) => USER_COLS[k]) : USER_COLS_DEFAULT; } catch (e) { return USER_COLS_DEFAULT; } })();
async function loadUsers() {
  const views = await api("/api/admin/views?tab=users").catch(() => ({mine: [], shared: []}));
  const all = [...views.mine, ...views.shared];
  $("#main").innerHTML = `<form id="userForm" class="filters"><input name="q" placeholder="Имя, ID, город или e-mail" value="${esc(userFilter.q)}" style="width:200px">
    <select name="flt">${[["", "Все"], ["blocked", "Заблокированные"], ["employers", "Работодатели"], ["trusted", "Проверенные авторы"], ["new7", "Новые за неделю"], ["deleted", "Удалённые"]].map(([k, v]) => `<option value="${k}" ${userFilter.flt === k ? "selected" : ""}>${v}</option>`).join("")}</select>
    <select name="sort"><option value="new" ${userFilter.sort === "new" ? "selected" : ""}>Сначала новые</option><option value="seen" ${userFilter.sort === "seen" ? "selected" : ""}>По последнему визиту</option></select>
    <button class="act" type="submit">Показать</button><span class="muted" id="userTotal"></span></form>
    <div class="filters"><select id="viewPick"><option value="">Сохранённые виды…</option>${views.mine.map((v) => `<option value="${v.id}">${esc(v.name)}${v.shared ? " (общий)" : ""}</option>`).join("")}${views.shared.length ? '<optgroup label="Общие от коллег">' + views.shared.map((v) => `<option value="${v.id}">${esc(v.name)} · ${esc(v.owner)}</option>`).join("") + "</optgroup>" : ""}</select>
      <button class="act gray" id="viewSave">💾 Сохранить этот вид</button><button class="act gray" id="viewShare">👥 Поделиться</button><button class="act gray" id="viewDel">🗑</button>
      <details><summary class="act gray" style="display:inline-block;list-style:none">☰ Колонки</summary><div class="card" style="position:absolute;z-index:6">${Object.entries(USER_COLS).map(([k, [n]]) => `<label style="display:block"><input type="checkbox" data-col="${k}" ${userCols.includes(k) ? "checked" : ""}> ${n}</label>`).join("")}</div></details></div>
    <div id="usersList"></div>`;
  window.__views = all;
  const qs = new URLSearchParams({limit: String(userLimit), ...Object.fromEntries(Object.entries(userFilter).filter(([, v]) => v))});
  const r = await api("/api/admin/users-list?" + qs);
  $("#userTotal").textContent = "Найдено: " + r.total;
  $("#usersList").innerHTML = (r.items.length ? `<table><tr>${userCols.map((k) => `<th>${USER_COLS[k][0]}</th>`).join("")}<th></th></tr>${r.items.map((u) => `<tr data-uid="${esc(u.tg_id)}">${userCols.map((k) => `<td>${USER_COLS[k][1](u)}</td>`).join("")}
    <td style="white-space:nowrap"><button class="act gray" data-ux="card">Управление</button>${u.blocked ? '<button class="act ok" data-ux="unblock">Разблок.</button>' : '<button class="act no" data-ux="blockterms">Блок…</button>'}</td></tr>`).join("")}</table>`
    : '<div class="card muted">Никого не нашли.</div>') + (r.total > r.items.length ? '<button class="act gray" id="userMore">Показать ещё</button>' : "");
}
$("#main").addEventListener("change", (e) => {
  const c = e.target.closest("[data-col]");
  if (c) {
    userCols = [...document.querySelectorAll("[data-col]")].filter((x) => x.checked).map((x) => x.dataset.col);
    if (!userCols.length) userCols = USER_COLS_DEFAULT;
    localStorage.setItem("adminUserCols", JSON.stringify(userCols)); reload(); return;
  }
  if (e.target.id === "viewPick" && e.target.value) {
    const v = (window.__views || []).find((x) => String(x.id) === e.target.value);
    if (v) { userFilter = {q: "", flt: "", sort: "new", ...(v.state.filter || {})}; if (v.state.cols) { userCols = v.state.cols.filter((k) => USER_COLS[k]); localStorage.setItem("adminUserCols", JSON.stringify(userCols)); } userLimit = 30; reload(); }
  }
});
$("#main").addEventListener("click", async (e) => {
  try {
    if (e.target.id === "viewSave") {
      const name = prompt("Как назвать этот вид?", ""); if (!name) return;
      await post("/api/admin/views", {tab: "users", name, state: {filter: userFilter, cols: userCols}, shared: false}); say("Вид сохранён"); reload();
    }
    const pick = $("#viewPick");
    if (e.target.id === "viewShare" && pick?.value) {
      const v = window.__views.find((x) => String(x.id) === pick.value);
      if (!v?.mine) { say("Делиться можно только своим видом"); return; }
      await api(`/api/admin/views/${v.id}/share`, {method: "PUT", body: JSON.stringify({shared: !v.shared})}); say(v.shared ? "Вид снова личный" : "Теперь вид видят все сотрудники"); reload();
    }
    if (e.target.id === "viewDel" && pick?.value && confirm("Удалить этот вид?")) { await api(`/api/admin/views/${pick.value}`, {method: "DELETE"}); say("Удалён"); reload(); }
  } catch (err) { say(err.message); }
});
$("#main").addEventListener("submit", (e) => {
  if (e.target.id !== "userForm") return;
  e.preventDefault();
  userFilter = Object.fromEntries(new FormData(e.target).entries()); userLimit = 30;
  reload();
});
$("#main").addEventListener("click", (e) => { if (e.target.id === "userMore") { userLimit += 30; reload(); } });
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ux]");
  if (!btn) return;
  const card = btn.closest("[data-uid]");
  const id = card.dataset.uid;
  try {
    if (btn.dataset.ux === "card") { openUserCard(id); return; }
    if (btn.dataset.ux === "blockterms") { openBlockDialog(id); return; }
    if (btn.dataset.ux === "stars") {
      const n = Number(prompt("Сколько KH выдать? (минус — списать ошибочно начисленное)", "50"));
      if (!n) return;
      const why = ask("Причина (человек её увидит):");
      if (!why) return;
      const r = await post(`/api/admin/users/${id}/stars`, {stars: n, reason: why});
      say(`Готово. Баланс человека: ${r.balance} KH`); openUserCard(id); return;
    }
    if (btn.dataset.ux === "resetpw" || btn.dataset.ux === "resetmail") {
      const why = ask("Причина сброса (запишется в журнал):");
      if (!why) return;
      await post(`/api/admin/users/${id}/account-reset`, {what: btn.dataset.ux === "resetpw" ? "password" : "both", reason: why});
      say("Сброшено: человек зайдёт через Telegram и задаст данные заново"); openUserCard(id); return;
    }
    if (btn.dataset.ux === "blockfor") {
      const days = Number(prompt("На сколько дней заблокировать? (1–365)", "7"));
      if (!days) return;
      const why = ask("Причина блокировки (пользователь её не увидит):");
      if (!why) return;
      await post(`/api/admin/users/${id}/block-for`, {reason: why, days});
      say(`Заблокирован на ${days} дн.`); if (current === "usercard") openUserCard(id); else reload();
      return;
    }
    if (btn.dataset.ux === "activity") {
      const a = await api(`/api/admin/users/${id}/activity`);
      const fmtObj = (o) => Object.entries(o).map(([k, v]) => `${esc(STATUS_RU[k] || k)}: ${v}`).join(", ") || "нет";
      card.querySelector("[data-act-box]").innerHTML = `<div class="meta">Объявления: ${fmtObj(a.listings)}<br>Отклики: ${a.applications_total} (${fmtObj(a.applications)})<br>
        Жалоб на него: ${a.reports_against} · жалоб подано: ${a.reports_made}<br>Следит за поисками: ${a.saved_searches.length ? a.saved_searches.map((s) => esc(s.name) + " " + esc(JSON.stringify(s.filters))).join("; ") : "нет"}</div>`;
      return;
    }
    if (btn.dataset.ux === "trust" || btn.dataset.ux === "untrust") {
      await post(`/api/admin/users/${id}/trust/${btn.dataset.ux === "trust" ? "on" : "off"}`);
      if (current === "usercard") openUserCard(id); else reload();
      return;
    }
    const reason = ask(btn.dataset.ux === "block" ? "Причина блокировки (пользователь её не увидит):" : "Причина разблокировки:");
    if (!reason) return;
    await post(`/api/admin/users/${id}/${btn.dataset.ux}`, {reason});
    if (current === "usercard") openUserCard(id); else reload();
  } catch (err) { say(err.message); }
});

// ---- сообщество ----
async function loadCommunity() {
  const rows = await api("/api/admin/groups");
  const row = (g = {}) => `<div class="card" data-gid="${g.id || ""}"><div class="gridform">
    <label>Флаг<input data-f="flag" value="${esc(g.flag || "")}" maxlength="8"></label>
    <label>Название<input data-f="name" value="${esc(g.name || "")}" placeholder="Название"></label>
    <label>Ссылка на группу<input data-f="telegram_link" value="${esc(g.telegram_link || "")}" placeholder="https://t.me/…"></label>
    <label>Участников<input data-f="members_count" type="number" min="0" value="${g.members_count || 0}"></label>
    <label>Раздел<select data-f="kind"><option value="nation" ${g.kind !== "city" ? "selected" : ""}>национальность</option><option value="city" ${g.kind === "city" ? "selected" : ""}>город</option></select></label>
    <label>Описание<input data-f="description" value="${esc(g.description || "")}" placeholder="Описание"></label></div>
    <button class="act" data-gsave>${g.id ? "Сохранить" : "Добавить"}</button>${g.id ? '<button class="act no" data-gdel>Удалить</button>' : ""}</div>`;
  $("#main").innerHTML = `<p class="muted">Ссылки только на группы в Telegram (https://t.me/…). Группа без ссылки в приложении показана серой.</p>
    <h3>Новая группа</h3>${row()}<h3>Все группы (${rows.length})</h3>${rows.map(row).join("")}`;
}
$("#main").addEventListener("click", async (e) => {
  const tr = e.target.closest("[data-gid]");
  if (!tr) return;
  const body = Object.fromEntries([...tr.querySelectorAll("[data-f]")].map((i) => [i.dataset.f, i.type === "number" ? Number(i.value || 0) : i.value.trim()]));
  try {
    if (e.target.closest("[data-gsave]")) {
      if (tr.dataset.gid) await api(`/api/admin/groups/${tr.dataset.gid}/full`, {method: "PUT", body: JSON.stringify(body)});
      else await post("/api/admin/groups", body);
      say("Сохранено"); reload();
    }
    if (e.target.closest("[data-gdel]") && confirm("Удалить группу?")) { await api(`/api/admin/groups/${tr.dataset.gid}`, {method: "DELETE"}); reload(); }
  } catch (err) { say(err.message); }
});

// ---- гид ----
async function loadGuide() {
  const rows = await api("/api/admin/articles");
  const SECTIONS = {visas: "Визы и документы", registration: "Регистрация и адрес", medicine: "Медицина", banks: "Банки и связь", housing: "Жильё", work: "Работа и права", emergency: "Экстренные ситуации"};
  const form = (a = {}) => `<div class="card" data-aid="${a.id || ""}">
    <div class="row"><select data-f="section">${Object.entries(SECTIONS).map(([k, v]) => `<option value="${k}" ${a.section === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
    <input data-f="title" value="${esc(a.title || "")}" placeholder="Заголовок" style="flex:1;min-width:200px"></div>
    <textarea data-f="body" rows="5" placeholder="Текст статьи">${esc(a.body || "")}</textarea>
    <div class="row"><input data-f="source_url" value="${esc(a.source_url || "")}" placeholder="Официальный источник https://…" style="flex:1;min-width:240px">
    <label>Проверено <input data-f="checked_at" type="date" value="${esc((a.checked_at || "").slice(0, 10))}"></label>
    <label><input data-f="published" type="checkbox" ${a.published ? "checked" : ""}> опубликовано</label></div>
    <p class="muted">Опубликовать можно только со ссылкой на источник и датой проверки. Факты о визах и документах берите с официальных сайтов.</p>
    <button class="act" data-asave>${a.id ? "Сохранить" : "Добавить статью"}</button>${a.id ? '<button class="act no" data-adel>Удалить</button>' : ""}</div>`;
  $("#main").innerHTML = `<h3>Новая статья</h3>${form()}<h3>Все статьи (${rows.length})</h3>${rows.map(form).join("")}`;
}
$("#main").addEventListener("click", async (e) => {
  const card = e.target.closest("[data-aid]");
  if (!card) return;
  const get = (k) => card.querySelector(`[data-f="${k}"]`);
  try {
    if (e.target.closest("[data-asave]")) {
      const body = {section: get("section").value, title: get("title").value.trim(), body: get("body").value, source_url: get("source_url").value.trim(),
                    checked_at: get("checked_at").value ? get("checked_at").value + "T00:00:00Z" : null, published: get("published").checked};
      if (card.dataset.aid) await api(`/api/admin/articles/${card.dataset.aid}`, {method: "PUT", body: JSON.stringify(body)});
      else await post("/api/admin/articles", body);
      say("Сохранено"); reload();
    }
    if (e.target.closest("[data-adel]") && confirm("Удалить статью?")) { await api(`/api/admin/articles/${card.dataset.aid}`, {method: "DELETE"}); reload(); }
  } catch (err) { say(err.message); }
});

// ---- рассылки и баннеры ----
async function loadMarketing() {
  const cal = await api("/api/admin/calendar?days=60").catch(() => []);
  const [banners, casts, geo, ads, camps] = await Promise.all([api("/api/admin/banners"), api("/api/admin/broadcasts"), fetch("/api/geo").then((r) => r.json()).then((g) => g.regions),
    api("/api/admin/ads").catch(() => []), api("/api/admin/campaigns").catch(() => [])]);
  const price = await api("/api/admin/pricing").catch(() => []);
  const adLists = await api("/api/admin/ads/lists").catch(() => ({banned_words: "", banned_hosts: "", allowed_hosts: ""}));
  const SERVICES = Object.fromEntries(price.map((x) => [x.code, x.title]));
  if (!price.length) Object.assign(SERVICES, {dating_vip: "VIP-анкета", dating_invisible: "Невидимка", dating_viewers: "Кто видел анкету", dating_super: "Супер-симпатия"});
  $("#main").innerHTML = `
    <div class="card"><h3>Календарь рекламы и акций (60 дней)</h3>${cal.length ? `<table><tr><th>Что</th><th>Где / для кого</th><th>С</th><th>До</th><th>Конфликты</th></tr>${cal.map((i) => `<tr><td>${i.kind === "ad" ? "📣" : "🎁"} ${esc(i.title)}</td><td>${esc(i.where)} · ${esc(i.audience)}</td><td>${esc(fmt(i.from))}</td><td>${esc(fmt(i.to))}</td>
      <td>${i.conflicts.length ? '<span class="bad">' + i.conflicts.map(esc).join("<br>") + "</span>" : '<span class="good">нет</span>'}</td></tr>`).join("")}</table>` : '<p class="muted">Ничего не запланировано.</p>'}</div>
    <div class="card"><h3>Реклама в лентах</h3><p class="muted">Всегда с пометкой «Реклама», не выше третьего места, одна на экран. 18+ видят только люди с подтверждённым возрастом. Реклама на объявление исчезает вместе с ним.</p>
      <div class="row"><input id="adTitle" placeholder="Заголовок" maxlength="80"><input id="adText" placeholder="Текст" maxlength="200" style="flex:1;min-width:200px">
      <input id="adLink" placeholder="Ссылка https://… или номер объявления" style="min-width:220px">
      <select id="adPlace"><option value="both">Лента и главная</option><option value="list">Лента</option><option value="home">Главная</option></select>
      <label><input type="checkbox" id="adAdult"> 18+</label><input id="adDays" type="number" min="1" max="365" placeholder="дней" style="width:80px"><input id="adBudget" type="number" min="1" placeholder="бюджет, показов" style="width:130px">
      <input id="adTitleB" placeholder="Вариант B: заголовок (A/B-тест)" maxlength="80"><input id="adTextB" placeholder="Вариант B: текст" maxlength="200"><button class="act" id="adAdd">Добавить</button></div>
      <details><summary class="muted">Запрещённые темы и ссылки</summary>
        <label>Запрещённые слова (через запятую)<textarea id="adBanW" rows="2">${esc(adLists.banned_words)}</textarea></label>
        <label>Запрещённые сайты (через запятую)<textarea id="adBanH" rows="2">${esc(adLists.banned_hosts)}</textarea></label>
        <label>Разрешённые сайты — если заполнено, другие нельзя<textarea id="adAllH" rows="2">${esc(adLists.allowed_hosts)}</textarea></label>
        <button class="act" id="adListsSave">Сохранить списки</button></details>
      <table><tr><th>№</th><th>Заголовок</th><th>Показы / бюджет</th><th>Клики</th><th>CTR</th><th>Идёт</th><th></th></tr>${ads.map((a) => `<tr data-adid="${a.id}"><td>${a.id}</td><td>${esc(a.title)}${a.adult ? " 🔞" : ""}</td>
        <td>${a.shows}${a.max_shows ? " / " + a.max_shows : ""}${a.ab ? `<br><small>A: ${a.ab.shows_a}/${a.ab.clicks_a} · B: ${a.ab.shows_b}/${a.ab.clicks_b}${a.ab.winner ? " · победил " + a.ab.winner.toUpperCase() : " · идёт тест"}</small>` : ""}</td><td>${a.clicks}</td><td>${a.ctr}%</td><td>${a.live ? "да" : "нет"}</td>
        <td><button class="act ${a.active ? "gray" : "ok"}" data-adtoggle>${a.active ? "Выключить" : "Включить"}</button><button class="act no" data-addel>Удалить</button></td></tr>`).join("")}</table></div>
    <div class="card"><h3>Акции (бесплатные услуги)</h3><p class="muted">Выдаётся с пометкой «по акции» и без подъёма в ленте. Досрочная остановка не отбирает уже выданное. У человека одновременно одна акция на услугу.</p>
      <div class="row"><input id="pcTitle" placeholder="Название акции" maxlength="80"><select id="pcService">${Object.entries(SERVICES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
      <input id="pcDays" type="number" min="1" max="30" value="3" style="width:70px" title="дней услуги">
      <select id="pcAud"><option value="all">Все</option><option value="new">Новые (14 дней)</option><option value="dating">С анкетой</option><option value="sellers">С объявлениями</option></select>
      <select id="pcMode"><option value="claim">Человек берёт сам</option><option value="auto">Выдать всем сразу (только анкеты)</option></select>
      <input id="pcStart" type="number" min="0" placeholder="старт через, ч" style="width:110px"><button class="act" id="pcAdd">Создать</button></div>
      <table><tr><th>№</th><th>Акция</th><th>Кому</th><th>Режим</th><th>Взяли</th><th>Статус</th><th></th></tr>${camps.map((c) => `<tr data-pcid="${c.id}"><td>${c.id}</td><td>${esc(c.title)} · ${esc(SERVICES[c.service_code] || c.service_code)} ${c.days} дн.</td>
        <td>${esc(c.audience_title)}</td><td>${c.mode === "auto" ? "всем сразу" : "по желанию"}</td><td>${c.claims}</td><td>${c.running ? "идёт" : c.active ? "ещё не началась" : "остановлена"}</td>
        <td>${c.active ? '<button class="act no" data-pcstop>Остановить</button>' : ""}</td></tr>`).join("")}</table></div>
    <div class="card"><h3>Баннер на главной</h3><p class="muted">Показывается вверху главной (не больше трёх одновременно).</p>
      <div class="row"><input id="bText" placeholder="Текст баннера" maxlength="160" style="flex:1;min-width:220px"><input id="bLink" placeholder="Ссылка https://… (необязательно)">
      <input id="bDays" type="number" min="1" max="365" placeholder="дней" style="width:80px"><button class="act" id="bAdd">Добавить</button></div>
      <table><tr><th>№</th><th>Текст</th><th>Ссылка</th><th>До</th><th></th></tr>${banners.map((b) => `<tr data-bid="${b.id}"><td>${b.id}</td><td>${esc(b.text)}</td><td>${esc(b.link)}</td><td>${esc(fmt(b.ends_at))}</td>
        <td><button class="act ${b.active ? "gray" : "ok"}" data-btoggle>${b.active ? "Выключить" : "Включить"}</button><button class="act no" data-bdel>Удалить</button></td></tr>`).join("")}</table></div>
    <div class="card"><h3>Рассылка</h3><p class="muted">Получат: согласившиеся с правилами, не заблокированные и не отключившие уведомления. Сначала черновик с числом получателей, потом подтверждение.
      Отправка идёт постепенно (около 25 сообщений за раз), её можно остановить.</p>
      <textarea id="cText" rows="3" maxlength="500" placeholder="Текст рассылки (до 500 символов)"></textarea>
      <div class="row"><select id="cRegion"><option value="">Вся Корея</option>${geo.map((r) => `<option value="${esc(r.code)}">${esc(r.name)}</option>`).join("")}</select>
      <select id="cAud"><option value="all">Все</option><option value="employers">Работодатели</option><option value="authors">Кто публиковал объявления</option><option value="active30">Заходили за 30 дней</option><option value="inactive30">Не заходили 30 дней</option></select>
      <label>Отправить в <input id="cWhen" type="datetime-local"></label> <span class="muted">пусто — черновик, запустите вручную</span>
      <button class="act" id="cMake">Создать</button></div>
      <table><tr><th>№</th><th>Текст</th><th>Кому</th><th>Статус</th><th>Отправлено</th><th></th></tr>${casts.map((c) => `<tr data-cid="${c.id}"><td>${c.id}</td><td>${esc(c.text.slice(0, 70))}</td><td>${esc(c.region_code || "вся Корея")} · ${esc({all: "все", employers: "работодатели", authors: "авторы", active30: "активные", inactive30: "неактивные"}[c.audience] || c.audience)}</td>
        <td>${esc(c.status === "scheduled" ? "запланирована на " + fmt(c.send_at) : c.status)}</td><td>${c.sent}/${c.total}${c.failed ? ` (ошибок ${c.failed})` : ""}</td><td>
        ${["draft", "scheduled"].includes(c.status) ? '<button class="act ok" data-csend>Отправить сейчас</button>' : ""}${["draft", "scheduled", "sending"].includes(c.status) ? '<button class="act no" data-ccancel>Остановить</button>' : ""}</td></tr>`).join("")}</table></div>`;
  $("#adAdd").onclick = async () => {
    try {
      const link = $("#adLink").value.trim(), isId = /^\d+$/.test(link);
      await post("/api/admin/ads", {title: $("#adTitle").value.trim(), text: $("#adText").value.trim(), link: isId ? "" : link, listing_id: isId ? Number(link) : null,
        place: $("#adPlace").value, adult_only: $("#adAdult").checked, days: $("#adDays").value ? Number($("#adDays").value) : null,
        max_shows: $("#adBudget").value ? Number($("#adBudget").value) : null, title_b: $("#adTitleB").value.trim(), text_b: $("#adTextB").value.trim()}); reload();
    } catch (err) { say(err.message); }
  };
  $("#adListsSave").onclick = async () => {
    try { await api("/api/admin/ads/lists", {method: "PUT", body: JSON.stringify({banned_words: $("#adBanW").value, banned_hosts: $("#adBanH").value, allowed_hosts: $("#adAllH").value})}); say("Списки сохранены"); }
    catch (err) { say(err.message); }
  };
  $("#pcAdd").onclick = async () => {
    try { await post("/api/admin/campaigns", {title: $("#pcTitle").value.trim(), service_code: $("#pcService").value, days: Number($("#pcDays").value || 3),
      audience: $("#pcAud").value, mode: $("#pcMode").value, start_in_hours: Number($("#pcStart").value || 0)}); reload(); }
    catch (err) { say(err.message); }
  };
  $("#bAdd").onclick = async () => {
    try { await post("/api/admin/banners", {text: $("#bText").value.trim(), link: $("#bLink").value.trim(), days: $("#bDays").value ? Number($("#bDays").value) : null}); reload(); }
    catch (err) { say(err.message); }
  };
  $("#cMake").onclick = async () => {
    try { const when = $("#cWhen").value ? new Date($("#cWhen").value).toISOString() : null;
      const c = await post("/api/admin/broadcasts", {text: $("#cText").value.trim(), region_code: $("#cRegion").value || null, audience: $("#cAud").value, send_at: when});
      say(c.status === "scheduled" ? `Запланировано: получателей ${c.total}` : `Черновик создан: получателей ${c.total}`); reload(); }
    catch (err) { say(err.message); }
  };
}
$("#main").addEventListener("click", async (e) => {
  try {
    const b = e.target.closest("[data-bid]");
    if (b && e.target.closest("[data-btoggle]")) { await api(`/api/admin/banners/${b.dataset.bid}/toggle`, {method: "PUT"}); reload(); }
    if (b && e.target.closest("[data-bdel]")) { await api(`/api/admin/banners/${b.dataset.bid}`, {method: "DELETE"}); reload(); }
    const ad = e.target.closest("[data-adid]");
    if (ad && e.target.closest("[data-adtoggle]")) { await api(`/api/admin/ads/${ad.dataset.adid}/toggle`, {method: "PUT"}); reload(); }
    if (ad && e.target.closest("[data-addel]") && confirm("Удалить рекламу?")) { await api(`/api/admin/ads/${ad.dataset.adid}`, {method: "DELETE"}); reload(); }
    const pc = e.target.closest("[data-pcid]");
    if (pc && e.target.closest("[data-pcstop]") && confirm("Остановить акцию? Уже выданное останется.")) { await api(`/api/admin/campaigns/${pc.dataset.pcid}/stop`, {method: "PUT"}); reload(); }
    const c = e.target.closest("[data-cid]");
    if (c && e.target.closest("[data-csend]")) {
      if (!confirm("Отправить рассылку всем получателям?")) return;
      await post(`/api/admin/broadcasts/${c.dataset.cid}/send`); reload();
    }
    if (c && e.target.closest("[data-ccancel]")) { await post(`/api/admin/broadcasts/${c.dataset.cid}/cancel`); reload(); }
  } catch (err) { say(err.message); }
});

// ---- платежи и промокоды ----
let payFilter = {q: "", status: "", service: "", since: "", until: ""}, payLimit = 30;
$("#main").addEventListener("click", (e) => { if (e.target.id === "payMore") { payLimit += 30; reload(); } });
const PAY_COLS = {
  id: ["№", (p) => p.id, (p) => p.id],
  user: ["Кто", (p) => `<a href="#" data-gu="${esc(p.user)}">${who(p.name, p.user)}</a>`, (p) => (p.name || p.user)],
  listing: ["Объявление", (p) => (p.listing_id ? `<a href="#" data-preview="${p.listing_id}">#${p.listing_id}</a>` : "—"), (p) => p.listing_id || 0],
  service: ["Услуга", (p) => `${esc(p.service_title)}${p.promo ? ` · код ${esc(p.promo)}` : ""}`, (p) => p.service_title],
  stars: ["⭐ / KH", (p) => p.stars, (p) => p.stars],
  when: ["Когда", (p) => esc(fmt(p.paid_at || p.created_at)), (p) => String(p.paid_at || p.created_at)],
  status: ["Статус", (p) => esc(PAY_RU[p.status] || p.status), (p) => p.status],
  promo: ["Промокод", (p) => esc(p.promo || "—"), (p) => p.promo || ""],
};
const PAY_DEFAULT = ["id", "user", "listing", "service", "stars", "when", "status"];
async function loadPayments() {
  const qs = new URLSearchParams({limit: String(payLimit), ...Object.fromEntries(Object.entries(payFilter).filter(([, v]) => v))});
  const [pl, promos, an] = await Promise.all([api("/api/admin/payments-list?" + qs), api("/api/admin/promos"), api("/api/admin/analytics?days=30")]);
  const sold = an.days.filter((d) => d.payments).reverse();
  const kitP = listKit("payments", PAY_COLS, PAY_DEFAULT, () => payFilter, (x) => { payFilter = {q: "", status: "", service: "", since: "", until: "", ...x}; payLimit = 30; });
  kitP.groupFns = {status: (p) => PAY_RU[p.status] || p.status, service: (p) => p.service_title, user: (p) => who(p.name, p.user).replace(/<[^>]+>/g, ""), day: (p) => String(p.paid_at || p.created_at).slice(0, 10)};
  const bar = await kitToolbar(kitP, {status: "статус", service: "услуга", user: "человек", day: "день"});
  const svcOptions = Object.entries(pl.services).map(([k, v]) => `<option value="${esc(k)}" ${payFilter.service === k ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#main").innerHTML = `
    <div class="card"><h3>Заработано звёзд за 30 дней: ${an.totals.stars} ⭐</h3>
      ${sold.length ? `<table><tr><th>День</th><th>Продаж</th><th>⭐</th></tr>${sold.map((d) => `<tr><td>${esc(d.date)}</td><td>${d.payments}</td><td>${d.stars}</td></tr>`).join("")}</table>` : '<p class="muted">Продаж пока не было.</p>'}
      ${an.stars_by_service.length ? `<h3 style="margin-top:12px">По услугам</h3><table><tr><th>Услуга</th><th>⭐</th></tr>${an.stars_by_service.map((x) => `<tr><td>${esc(x.service)}</td><td>${x.stars}</td></tr>`).join("")}</table>` : ""}</div>
    <div class="card"><h3>Промокоды</h3><div class="row"><input id="pCode" placeholder="КОД" maxlength="24" style="width:130px;text-transform:uppercase">
      <input id="pPercent" type="number" min="1" max="90" placeholder="% скидки" style="width:100px"><input id="pFixed" type="number" min="1" placeholder="или звёзд" style="width:100px">
      <input id="pMax" type="number" min="1" placeholder="лимит" style="width:90px"><input id="pDays" type="number" min="1" placeholder="дней" style="width:80px"><button class="act" id="pAdd">Создать</button></div>
      <table><tr><th>Код</th><th>Скидка</th><th>Использовано</th><th>До</th><th></th></tr>${promos.map((p) => `<tr data-pid="${p.id}"><td>${esc(p.code)}</td>
      <td>${p.percent ? "−" + p.percent + "%" : "−" + p.fixed_stars + " KH"}</td><td>${p.used}${p.max_uses ? "/" + p.max_uses : ""}</td><td>${esc(fmt(p.valid_until) || "без срока")}</td>
      <td><button class="act ${p.active ? "gray" : "ok"}" data-ptoggle>${p.active ? "Выключить" : "Включить"}</button></td></tr>`).join("")}</table></div>
    <form id="payFilter" class="filters"><input name="q" placeholder="Имя или ID" value="${esc(payFilter.q)}" style="width:140px">
      <select name="status"><option value="">Любой статус</option>${Object.entries(PAY_RU).map(([k, v]) => `<option value="${k}" ${payFilter.status === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select name="service"><option value="">Любая услуга</option>${svcOptions}</select><input name="since" type="date" value="${esc(payFilter.since)}"><input name="until" type="date" value="${esc(payFilter.until)}">
      <button class="act" type="submit">Показать</button><span class="muted">Найдено: ${pl.total} · оплачено: ${pl.paid_stars} ⭐</span></form>
    ${bar}${pl.items.length ? kitTable(kitP, pl.items, (p) => `data-pay="${p.id}"`, (p) => (p.status === "paid" ? '<button class="act no" data-refund>Вернуть</button>' : ""))
      : '<div class="card muted">По этим условиям платежей нет.</div>'}
    ${pl.total > pl.items.length ? `<button class="act gray" id="payMore">Показать ещё</button>` : ""}`;
  $("#pAdd").onclick = async () => {
    const num = (id) => ($(id).value ? Number($(id).value) : null);
    try { await post("/api/admin/promos", {code: $("#pCode").value.trim(), percent: num("#pPercent"), fixed_stars: num("#pFixed"), max_uses: num("#pMax"), days: num("#pDays")}); reload(); }
    catch (err) { say(err.message); }
  };
}
$("#main").addEventListener("submit", (e) => {
  if (e.target.id !== "payFilter") return;
  e.preventDefault();
  payFilter = Object.fromEntries(new FormData(e.target).entries());
  reload();
});
$("#main").addEventListener("click", async (e) => {
  try {
    const t = e.target.closest("[data-ptoggle]");
    if (t) { await api(`/api/admin/promos/${t.closest("[data-pid]").dataset.pid}/toggle`, {method: "PUT"}); reload(); }
    const btn = e.target.closest("[data-refund]");
    if (!btn) return;
    const id = btn.closest("[data-pay]").dataset.pay;
    if (!confirm("Вернуть оплату " + id + "? Звёзды вернутся пользователю, услуга отключится.")) return;
    const why = askOpt("Причина возврата (её увидит человек)");
    if (why === null) return;
    await post(`/api/admin/payments/${id}/refund${why ? "?reason=" + encodeURIComponent(why) : ""}`); reload();
  } catch (err) { say(err.message); }
});

// ---- цены ----
async function loadPricing() {
  const rows = await api("/api/admin/pricing");
  $("#main").innerHTML = `<table><tr><th>Услуга</th><th>Раздел</th><th>Цена, KH</th><th>Дней</th><th>Активна</th><th></th></tr>
    ${rows.map((p) => `<tr data-code="${esc(p.code)}"><td>${esc(p.title)}</td><td>${esc(CAT[p.category] || p.category)}</td>
      <td><input type="number" min="1" max="10000" value="${p.stars}" data-stars style="width:90px"></td><td>${p.duration_days}</td>
      <td><input type="checkbox" data-active ${p.is_active ? "checked" : ""}></td><td><button class="act" data-save>Сохранить</button></td></tr>`).join("")}</table>`;
}
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-save]");
  if (!btn) return;
  const row = btn.closest("[data-code]");
  try {
    await api(`/api/admin/pricing/${row.dataset.code}`, {method: "PUT", body: JSON.stringify({stars: Number(row.querySelector("[data-stars]").value), is_active: row.querySelector("[data-active]").checked})});
    say("Цена сохранена (запись в журнале)");
  } catch (err) { say(err.message); }
});

// ---- журнал ----
async function loadErrors() {
  const [groups, rows] = await Promise.all([api("/api/admin/client-errors/groups"), api("/api/admin/client-errors")]);
  $("#main").innerHTML = `<div class="card"><div class="row" style="justify-content:space-between"><div><h3 style="margin:0">Ошибки, которые видели люди</h3>
      <p class="muted" style="margin:4px 0 0">Приложение само присылает сюда ошибки. «Код» совпадает с тем, что человек видит на экране. Исправили причину — сбросьте, чтобы не мешали.</p></div>
      <div><button class="act gray" data-errclear="older">Сбросить старше недели</button><button class="act no" data-errclear="all">Сбросить всё</button></div></div></div>
    <div class="card"><h3>Группы: одна и та же проблема</h3>${groups.length ? `<table><tr><th>Где</th><th>Статус</th><th>Сообщение</th><th>Раз</th><th>Людей</th><th>Последний раз</th><th></th></tr>${groups.map((g, i) => `<tr data-eg="${i}"><td>${esc(g.where)}</td><td>${g.status || ""}</td><td>${esc(g.message)}</td>
      <td><b>${g.count}</b></td><td>${g.people}</td><td>${esc(fmt(g.last))}</td><td><button class="act gray" data-errclear="group">Сбросить группу</button></td></tr>`).join("")}</table>` : '<p class="muted">Ошибок нет. 🎉</p>'}</div>
    <div class="card"><h3>Последние отдельно</h3>${rows.length ? `<table><tr><th>Время</th><th>Человек</th><th>Где</th><th>Статус</th><th>Сообщение</th><th>Код</th><th>Телефон</th><th></th></tr>
    ${rows.map((r) => `<tr data-eid="${r.id}"><td>${esc(fmt(r.at))}</td><td>${esc(r.user || "гость")}</td><td>${esc(r.where)}</td><td>${r.status || ""}</td><td>${esc(r.message)}</td><td>${esc(r.request_id)}</td><td>${esc(r.agent)}</td><td><button class="act gray" data-errdel>✕</button></td></tr>`).join("")}</table>` : '<p class="muted">Пусто.</p>'}</div>`;
  window.__errGroups = groups;
}
$("#main").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-errclear],[data-errdel]");
  if (!b) return;
  try {
    if (b.dataset.errdel !== undefined) { await api(`/api/admin/client-errors/${b.closest("[data-eid]").dataset.eid}`, {method: "DELETE"}); reload(); return; }
    const mode = b.dataset.errclear;
    if (mode === "all" && !confirm("Сбросить весь список ошибок?")) return;
    const body = {mode};
    if (mode === "group") { const g = window.__errGroups[Number(b.closest("[data-eg]").dataset.eg)]; Object.assign(body, {where: g.where, status: g.status, message: g.message}); }
    if (mode === "older") body.days = 7;
    const r = await post("/api/admin/client-errors/clear", body); say(`Сброшено: ${r.deleted}`); reload();
  } catch (err) { say(err.message); }
});
async function loadAudit() {
  $("#main").innerHTML = `<form id="auditForm" class="filters"><input name="who" placeholder="Кто (ID)" style="width:110px"><input name="action" placeholder="Действие, напр. listing.reject" style="width:200px">
    <input name="object_type" placeholder="Объект, напр. listing" style="width:150px"><input name="object_id" placeholder="№ объекта" style="width:100px"><input name="q" placeholder="Слово в деталях" style="width:160px">
    <input name="since" type="date"><input name="until" type="date"><button class="act" type="submit">Показать</button></form><div id="auditList"></div>`;
  const go = async () => {
    const q = new URLSearchParams({limit: "200"});
    new FormData($("#auditForm")).forEach((v, k) => { if (String(v).trim()) q.set(k, String(v).trim()); });
    const rows = await api("/api/admin/audit?" + q);
    $("#auditList").innerHTML = rows.length ? `<table><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Детали</th></tr>
      ${rows.map((r) => `<tr><td>${esc(fmt(r.created_at))}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td>${esc(r.object_type)} ${esc(r.object_id)}</td><td>${esc(r.details)}</td></tr>`).join("")}</table>`
      : '<div class="card muted">Записей по этим условиям нет.</div>';
  };
  $("#auditForm").onsubmit = (e) => { e.preventDefault(); go().catch((err) => say(err.message)); };
  await go();
}
// ---- выгрузки ----
async function loadSettings() {
  let mine = [];
  try { mine = await api("/api/admin/my-sessions"); } catch (err) { mine = []; }
  $("#main").innerHTML = `<div class="card"><h3>Выгрузка в таблицу (CSV)</h3><p class="muted">Файлы открываются в Excel и Google Таблицах. В выгрузке пользователей нет имён и контактов. Каждая выгрузка записывается в журнал.</p>
    ${["users:Пользователи", "payments:Платежи", "listings:Объявления"].map((x) => { const [k, v] = x.split(":"); return `<button class="act" data-export="${k}">⬇ ${v}</button>`; }).join("")}</div>
    <div class="card"><h3>Мои входы</h3><p class="muted">Активных входов: ${mine.length}. Если потеряли телефон или вошли на чужом устройстве, закройте все.</p>
      <button class="act no" id="logoutAll">Выйти на всех устройствах</button></div>`;
  const la = document.getElementById("logoutAll");
  if (la) la.onclick = async () => { if (!confirm("Закрыть все ваши входы, включая этот?")) return; try { await post("/api/admin/logout-all"); logout(); } catch (err) { say(err.message); } };
}
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-export]");
  if (!btn) return;
  try {
    const r = await fetch(`/api/admin/export/${btn.dataset.export}.csv`, {headers: {"X-Admin-Session": session()}});
    if (!r.ok) throw new Error((await r.json()).detail || "Не удалось выгрузить");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = `korea-hub-${btn.dataset.export}.csv`;
    a.click();
    say("Файл выгружен");
  } catch (err) { say(err.message); }
});

// ---- сотрудники (только суперадминистратор) ----
async function fillStaffExtras() {
  try {
    const d = await api("/api/admin/staff-detail");
    $("#staffDetail").innerHTML = d.length ? `<table><tr><th>Сотрудник</th><th>Одобрил</th><th>Отклонил</th><th>На доработку</th><th>Скрыл</th><th>Ответов</th><th>Жалоб</th><th>Доля отказов</th></tr>
      ${d.map((x) => `<tr><td>${esc(x.actor)}</td><td>${x.approved}</td><td>${x.rejected}</td><td>${x.fixes}</td><td>${x.hidden}</td><td>${x.replies}</td><td>${x.reports}</td><td>${x.reject_pct == null ? "—" : x.reject_pct + "%"}</td></tr>`).join("")}</table>` : "Пока нет данных.";
    const l = await api("/api/admin/staff-logins");
    $("#staffLogins").innerHTML = l.length ? `<table><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Детали</th></tr>${l.map((x) => `<tr><td>${esc(fmt(x.at))}</td><td>${esc(x.actor)}</td><td>${esc(x.action)}</td><td>${esc(x.details)}</td></tr>`).join("")}</table>` : "Записей нет.";
  } catch (err) { /* необязательно */ }
}
$("#main").addEventListener("click", async (e) => {
  const out = e.target.closest("[data-staff-out]");
  if (!out) return;
  const id = out.closest("[data-staff]").dataset.staff;
  if (!confirm(`Закрыть все входы сотрудника ${id}? Ему придётся войти снова.`)) return;
  try { const r = await post(`/api/admin/staff/${id}/force-logout`); say("Закрыто сессий: " + r.closed); } catch (err) { say(err.message); }
});
async function loadAdmins() {
  const rows = await api("/api/admin/admins");
  const ROLES = ["support", "moderator", "accountant", "admin", "super_admin"];
  $("#main").innerHTML = `<p class="muted">Роли: support — жалобы, пользователи и блокировки; moderator — + модерация объявлений, анкет и работодателей;
    admin — + цены, платежи, контент, рассылки, статистика, журнал; super_admin — + сотрудники.</p>
    <table><tr><th>Telegram ID</th><th>Роль</th><th>Доступ</th><th></th></tr>
    ${rows.map((a) => `<tr data-staff="${esc(a.tg_id)}"><td>${esc(a.tg_id)}${a.elevated_until ? `<br><small class="muted">временно до ${esc(fmt(a.elevated_until))}, потом ${esc(a.base_role || "")}</small>` : ""}</td><td><select data-role>${ROLES.map((r) => `<option value="${r}" ${r === a.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td><input type="checkbox" data-active ${a.active ? "checked" : ""}></td><td><button class="act" data-staff-save>Сохранить</button> <button class="act gray" data-staff-out>Выйти со всех устройств</button> ${a.role !== "super_admin" && a.role !== "admin" ? '<button class="act warn" data-staff-up>Повысить на срок</button>' : ""}</td></tr>`).join("")}</table>
    <div class="card"><h3>Работа сотрудников за 30 дней</h3><div id="staffDetail" class="muted">Загрузка…</div></div>
    <div class="card"><h3>Входы и действия с доступом</h3><div id="staffLogins" class="muted">Загрузка…</div></div>
    <p><input id="newStaffId" inputmode="numeric" placeholder="Telegram ID сотрудника"><select id="newStaffRole">${ROLES.map((r) => `<option>${r}</option>`).join("")}</select>
    <button class="act" id="addStaff">Добавить</button></p>`;
  fillStaffExtras();
}
$("#main").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-staff]");
  try {
    if (e.target.closest("[data-staff-up]")) {
      const role = prompt("Какая роль? admin или moderator", "admin"); if (!role) return;
      const days = Number(prompt("На сколько дней? (1–14)", "7")); if (!days) return;
      const why = ask("Причина (запишется в журнал):"); if (!why) return;
      await post(`/api/admin/admins/${row.dataset.staff}/elevate`, {role: role.trim(), days, reason: why}); say("Права выданы, вернутся сами"); reload(); return;
    }
    if (e.target.closest("[data-staff-save]")) {
      await api(`/api/admin/admins/${row.dataset.staff}`, {method: "PUT", body: JSON.stringify({role: row.querySelector("[data-role]").value, active: row.querySelector("[data-active]").checked})});
      say("Сохранено (запись в журнале)"); reload();
    }
    if (e.target.id === "addStaff") {
      const id = $("#newStaffId").value.trim();
      if (!/^\d{3,20}$/.test(id)) { say("Введите числовой Telegram ID"); return; }
      await api(`/api/admin/admins/${id}`, {method: "PUT", body: JSON.stringify({role: $("#newStaffRole").value, active: true})});
      reload();
    }
  } catch (err) { say(err.message); }
});


// ---- правила: числа и переключатели, которые раньше жили в коде ----
async function loadRules() {
  const groups = await api("/api/admin/rules");
  $("#main").innerHTML = '<p class="muted">Меняется без релиза. Каждое изменение записывается в журнал: кто, когда, было → стало. «По умолчанию» возвращает заводское значение.</p>'
    + groups.map((g) => `<div class="card"><h3>${esc(g.group)}</h3>${g.rules.map((r) => `<div class="row" data-rule="${esc(r.key)}" style="align-items:center;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid #0001">
      <div style="flex:1;min-width:200px"><b>${esc(r.label)}</b>${r.hint ? `<div class="muted">${esc(r.hint)}</div>` : ""}<div class="muted">по умолчанию: ${r.kind === "bool" ? (r.default ? "включено" : "выключено") : r.default}</div></div>
      ${r.kind === "bool" ? `<label><input type="checkbox" data-rv ${r.value ? "checked" : ""}> ${r.value ? "вкл" : "выкл"}</label>`
        : `<input type="number" data-rv value="${r.value}" min="${r.min}" max="${r.max}" style="width:90px">`}
      <button class="act" data-rsave>Сохранить</button><button class="act gray" data-rreset>По умолчанию</button></div>`).join("")}</div>`).join("");
}
$("#main").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-rule]");
  if (!row) return;
  try {
    const key = row.dataset.rule, input = row.querySelector("[data-rv]");
    if (e.target.closest("[data-rsave]")) { await api(`/api/admin/rules/${key}`, {method: "PUT", body: JSON.stringify({value: input.type === "checkbox" ? input.checked : Number(input.value)})}); say("Сохранено"); reload(); }
    if (e.target.closest("[data-rreset]")) { await post(`/api/admin/rules/${key}/reset`); say("Возвращено значение по умолчанию"); reload(); }
  } catch (err) { say(err.message); }
});

// ---- отчёты по деньгам и история VIP ----
let reportDays = 30;
async function loadMoneyReports() {
  const [rep, vip] = await Promise.all([api(`/api/admin/reports/money?days=${reportDays}`), api("/api/admin/vip/history")]);
  const line = (r) => `<td>${r.paid}</td><td>${r.stars} ⭐</td><td>${r.from_balance} KH</td><td>${r.refunded} (${r.refunded_stars} ⭐)</td><td>${r.granted}</td><td>${r.saved_stars} ⭐</td>`;
  const head = "<tr><th></th><th>Оплат</th><th>Выручка</th><th>С баланса</th><th>Возвратов</th><th>Подарков</th><th>Сэкономлено людям</th></tr>";
  const SEC = {job: "Работа", housing: "Жильё", marketplace: "Барахолка", dating: "Знакомства", balance: "Пополнения балансов"};
  $("#main").innerHTML = `<div class="card"><div class="row"><b>Период:</b>${[7, 30, 90, 365].map((d) => `<button class="act ${d === reportDays ? "" : "gray"}" data-rdays="${d}">${d} дн.</button>`).join("")}</div>
    <h3>Итого</h3><table>${head}<tr><td><b>Все</b></td>${line(rep.total)}</tr></table><p class="muted">Платящих людей: ${rep.total.payers}</p>
    <h3>По разделам</h3><table>${head}${Object.entries(rep.by_section).map(([k, r]) => `<tr><td>${esc(SEC[k] || k)}</td>${line(r)}</tr>`).join("")}</table>
    <h3>По услугам</h3><table>${head}${Object.entries(rep.by_service).map(([k, r]) => `<tr><td>${esc(r.title)}</td>${line(r)}</tr>`).join("")}</table></div>
    <div class="card"><h3>VIP: история</h3><p class="muted">Активаций: ${vip.summary.activations ?? 0} · разных людей: ${vip.summary.unique ?? 0} · платных: ${vip.summary.paid ?? 0} · подарков: ${vip.summary.granted ?? 0} · выручка ${vip.summary.stars ?? 0} ⭐ · сэкономлено людям ${vip.summary.saved_stars ?? 0} ⭐</p>
      <table><tr><th>Когда</th><th>Кто</th><th>Раздел</th><th>Срок</th><th>Цена</th><th>Статус</th><th>Примечание</th></tr>${vip.items.map((r) => `<tr><td>${esc(fmt(r.at))}</td><td>${esc(r.name || r.user)}</td><td>${esc(SEC[r.module] || r.module)}</td>
        <td>${r.days} дн.</td><td>${r.stars}${r.from_balance ? " + " + r.from_balance + " с баланса" : ""} ⭐</td><td>${esc({paid: "оплачено", granted: "подарок", refunded: "возврат"}[r.status] || r.status)}</td><td>${esc(r.note)}</td></tr>`).join("")}</table></div>`;
}
$("#main").addEventListener("click", (e) => { const b = e.target.closest("[data-rdays]"); if (b) { reportDays = Number(b.dataset.rdays); reload(); } });

// ---- тест поиска ----
let stestMode = "people";
async function loadSearchTest() {
  $("#main").innerHTML = `<div class="card"><p class="muted">Введите запрос так, как его вводит человек. «Как видят люди» показывает настоящую выдачу и причину каждого места. «Как видит админ» дополнительно покажет, что не показано и почему.</p>
    <div class="row"><input id="stQ" placeholder="Запрос, например: повар" style="flex:1;min-width:200px">
    <select id="stMode"><option value="people" ${stestMode === "people" ? "selected" : ""}>Как видят люди</option><option value="admin" ${stestMode === "admin" ? "selected" : ""}>Как видит админ</option></select>
    <button class="act" id="stGo">Проверить</button></div><div id="stOut"></div></div>
    ${["admin", "super_admin"].includes(role()) ? `<div class="card"><h3>Синонимы и стоп-слова</h3><p class="muted">Синонимы: одна группа в строке, через «=» или запятую: <code>квартира = 원룸, студия</code>. Запрос на любое слово группы находит и остальные. Стоп-слова пропускаются в запросе: <code>в, на, и</code>.</p>
      <label>Синонимы<textarea id="sdSyn" rows="5" placeholder="квартира = 원룸, студия"></textarea></label><label>Стоп-слова (через запятую)<input id="sdStop" style="width:100%"></label>
      <button class="act" id="sdSave">Сохранить</button> <span class="muted">Действует сразу, проверьте выше.</span></div>` : ""}`;
  if ($("#stOut")) api("/api/admin/search-log?days=7").then((d) => {
    $("#stOut").insertAdjacentHTML("afterend", `<div class="card" id="slCard"><h3>Что ищут люди (7 дней, без личных данных)</h3><p class="muted">Запросов: ${d.total}. Хранится 90 дней, номера, почта и @имена вырезаются.</p>
      <div class="cols"><div><b>Частые</b>${d.top.map((x) => `<div class="drun" style="display:flex;justify-content:space-between"><span>${esc(x.q)}</span><span class="muted">${x.n}</span></div>`).join("") || '<p class="muted">Пока пусто</p>'}</div>
      <div><b>Ничего не нашли (что добавить?)</b>${d.empty.map((x) => `<div style="display:flex;justify-content:space-between"><span>${esc(x.q)}</span><span class="muted">${x.n}</span></div>`).join("") || '<p class="muted">Таких нет</p>'}</div></div>
      <button class="act gray" id="slReindex">Пересобрать текст поиска</button>${role() === "super_admin" ? '<button class="act gray" id="slCsv">⬇ Выгрузка (суперадмин)</button>' : ""}</div>`);
    $("#slReindex").onclick = async () => { try { const r = await post("/api/admin/search/reindex"); say(`Обновлено объявлений: ${r.updated}` + (r.version ? `, версия #${r.version}` : "")); reload(); } catch (err) { say(err.message); } };
    loadIndexVersions();
    if ($("#slCsv")) $("#slCsv").onclick = async () => { const r = await fetch("/api/admin/search-log/export", {headers: {"X-Admin-Session": session()}}); const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = "search-log.csv"; a.click(); };
  }).catch(() => {});
  if ($("#sdSyn")) api("/api/admin/search-dict").then((d) => { $("#sdSyn").value = d.synonyms; $("#sdStop").value = d.stopwords; }).catch(() => {});
  if ($("#sdSave")) $("#sdSave").onclick = async () => { try { await api("/api/admin/search-dict", {method: "PUT", body: JSON.stringify({synonyms: $("#sdSyn").value, stopwords: $("#sdStop").value})}); say("Сохранено"); } catch (err) { say(err.message); } };
  const go = async () => {
    stestMode = $("#stMode").value;
    const r = await api(`/api/admin/search-test?q=${encodeURIComponent($("#stQ").value)}&mode=${stestMode}`);
    $("#stOut").innerHTML = `<p class="muted">Найдено: ${r.total}${r.corrected ? ` · показано по запросу «${esc(r.corrected)}»` : ""}</p>
      <table><tr><th>№</th><th>Объявление</th><th>Полоса</th><th>Почему здесь</th></tr>${r.items.map((i) => `<tr><td>${i.place}</td><td>#${i.id} ${esc(i.title)}<br><span class="muted">${esc(i.city)}</span></td><td>${esc(i.lane)}</td><td>${i.why.map(esc).join("<br>")}</td></tr>`).join("")}</table>
      ${r.not_shown ? `<h3>Не показано</h3><table><tr><th>Объявление</th><th>Статус</th><th>Почему</th></tr>${r.not_shown.map((i) => `<tr><td>#${i.id} ${esc(i.title)}</td><td>${esc(i.status)}</td><td>${esc(i.why)}</td></tr>`).join("")}</table>` : ""}`;
  };
  $("#stGo").onclick = () => go().catch((e) => say(e.message));
  $("#stQ").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#stGo").click(); });
}

// ---- компании ----
let coFilter = "pending";
async function loadCompanies() {
  const rows = await api(`/api/admin/companies?status=${coFilter}`);
  window.__cos = Object.fromEntries(rows.map((c) => [c.id, c]));
  const ST = {pending: "на проверке", verified: "проверена", rejected: "отклонена", suspended: "приостановлена"};
  $("#main").innerHTML = `<div class="card"><div class="row">${[["pending", "На проверке"], ["verified", "Проверенные"], ["suspended", "Приостановленные"], ["rejected", "Отклонённые"], ["all", "Все"]]
    .map(([k, n]) => `<button class="act ${k === coFilter ? "" : "gray"}" data-cof="${k}">${n}</button>`).join("")}</div>
    <table><tr><th>№</th><th>Компания</th><th>Рег. номер</th><th>Владелец</th><th>Объявл.</th><th>Команда</th><th>Баланс</th><th>Статус</th><th></th></tr>${rows.map((c) => `<tr data-coid="${c.id}"><td>${c.id}</td><td>${esc(c.name)}</td><td>${esc(c.reg_no)}</td>
      <td>${esc(c.owner_name || c.owner)}</td><td>${c.listings}</td><td>${c.members}</td><td>${c.balance} KH</td><td>${esc(ST[c.status] || c.status)}${c.note ? `<br><small>${esc(c.note)}</small>` : ""}</td>
      <td>${["pending", "suspended", "rejected"].includes(c.status) ? '<button class="act ok" data-codec="approve">Подтвердить</button>' : ""}
      ${["pending"].includes(c.status) ? '<button class="act no" data-codec="reject">Отказать</button>' : ""}${c.status === "verified" ? '<button class="act no" data-codec="suspend">Приостановить</button>' : ""}
      <button class="act gray" data-cocredit>+ баланс</button> <button class="act" data-coedit>✏ Править</button></td></tr>`).join("") || '<tr><td colspan="9" class="muted">Пусто</td></tr>'}</table></div>`;
}
$("#main").addEventListener("click", async (e) => {
  const f = e.target.closest("[data-cof]");
  if (f) { coFilter = f.dataset.cof; reload(); return; }
  const row = e.target.closest("[data-coid]");
  if (!row) {
    const bx = e.target.closest("[data-cobox]");
    if (!bx) return;
    try {
      const id = bx.dataset.cobox;
      if (e.target.closest("[data-cosave]")) {
        const body = {}; document.querySelectorAll("#drawer [data-cof2]").forEach((el) => { body[el.dataset.cof2] = el.value; });
        body.reason = askOpt("Что поправили (владелец увидит)") || "";
        const r = await api(`/api/admin/companies/${id}/edit`, {method: "PUT", body: JSON.stringify(body)}); say("Сохранено: " + (r.changed.join(", ") || "без изменений")); closeDrawer(); reload();
      }
      if (e.target.closest("[data-cologo]") && confirm("Убрать логотип компании?")) { await api(`/api/admin/companies/${id}/logo`, {method: "DELETE"}); say("Логотип убран"); }
    } catch (err) { say(err.message); }
    return;
  }
  try {
    const id = row.dataset.coid, d = e.target.closest("[data-codec]");
    if (e.target.closest("[data-coedit]")) {
      const c = (window.__cos || {})[id]; if (!c) return;
      openDrawer(`Компания #${c.id} · ${esc(c.name)}`, `<div class="gridform" data-cobox="${c.id}"><label>Название<input data-cof2="name" value="${esc(c.name)}" maxlength="120"></label>
        <label>Рег. номер<input data-cof2="reg_no" value="${esc(c.reg_no)}"></label><label>Город<input data-cof2="city" value="${esc(c.city || "")}"></label><label>Сайт<input data-cof2="website" value="${esc(c.website || "")}"></label>
        <label class="wide">Описание<textarea data-cof2="description" rows="4" maxlength="2000">${esc(c.description || "")}</textarea></label>
        <label class="wide">Комментарий владельцу<input data-cof2="note" value="${esc(c.note || "")}" maxlength="300"></label></div>
        <div class="row" data-cobox="${c.id}"><button class="act ok" data-cosave>💾 Сохранить</button><button class="act gray" data-cologo>Убрать логотип</button></div>
        <p class="muted">Владелец: <a href="#" data-gu="${esc(c.owner)}">${esc(c.owner_name || c.owner)}</a> · объявлений ${c.listings} · команда ${c.members} · баланс ${c.balance} KH</p>`);
      return;
    }
    if (d) {
      let reason = "";
      if (["reject", "suspend"].includes(d.dataset.codec)) { reason = prompt("Причина (её увидит владелец)"); if (!reason) return; }
      await post(`/api/admin/companies/${id}/${d.dataset.codec}`, {reason}); say("Готово"); reload();
    }
    if (e.target.closest("[data-cocredit]")) {
      const n = Number(prompt("Сколько KH зачислить на баланс компании?"));
      if (!n) return;
      const note = prompt("Комментарий (необязательно)") || "";
      await post(`/api/admin/companies/${id}/credit`, {stars: n, note}); say("Зачислено"); reload();
    }
  } catch (err) { say(err.message); }
});

// ---- возвраты ----
let refundFilter = "open";
const REF_ST = {new: "новая", in_progress: "в работе", approved: "возвращено", partially_refunded: "возвращено частично", rejected: "отказано", cancelled: "отменён"};
const REF_COLS = {
  id: ["№", (r) => r.id, (r) => r.id],
  payment: ["Платёж", (r) => `#${r.payment_id}<br><small class="muted">${esc(r.service)}</small>`, (r) => r.payment_id],
  user: ["Человек", (r) => `<a href="#" data-gu="${esc(r.user)}">${esc(r.user)}</a>`, (r) => r.user],
  paid: ["Сумма платежа", (r) => `${r.paid ?? "—"} ⭐${r.amount ? `<br><small>возвращено ${r.amount}</small>` : ""}`, (r) => r.paid ?? -1],
  reason: ["Причина", (r) => `${esc(r.reason)}${r.answer ? `<br><small class="muted">Ответ: ${esc(r.answer)}</small>` : ""}`, (r) => r.reason],
  status: ["Статус", (r) => esc(REF_ST[r.status] || r.status), (r) => r.status],
};
const REF_DEFAULT = ["id", "payment", "user", "paid", "reason", "status"];
async function loadRefunds() {
  const kitR = listKit("refunds", REF_COLS, REF_DEFAULT, () => ({status: refundFilter}), (x) => { refundFilter = x.status || "open"; });
  kitR.groupFns = {status: (r) => REF_ST[r.status] || r.status, service: (r) => r.service, user: (r) => r.user};
  const bar = await kitToolbar(kitR, {status: "статус", service: "услуга", user: "человек"});
  const rows = await api(`/api/admin/refunds?status=${refundFilter}`);
  const ST = {new: "новая", in_progress: "в работе", approved: "возвращено", partially_refunded: "возвращено частично", rejected: "отказано", cancelled: "отменён"};
  $("#main").innerHTML = `<div class="card"><div class="row">${[["open", "Открытые"], ["all", "Все"], ["approved", "Возвращённые"], ["rejected", "Отказы"]].map(([k, n]) => `<button class="act ${k === refundFilter ? "" : "gray"}" data-rff="${k}">${n}</button>`).join("")}
    <a class="act gray" style="text-decoration:none" href="#" id="refundCsv">⬇ Выгрузка CSV</a></div>
    ${bar}${rows.length ? kitTable(kitR, rows, (r) => `data-rid="${r.id}"`, (r) => `
      ${r.status === "new" ? '<button class="act gray" data-rf="take">Взять</button>' : ""}
      ${["new", "in_progress"].includes(r.status) ? '<button class="act ok" data-rf="approve-balance">На баланс (остаток)</button><button class="act warn" data-rf="approve-full">Всё в Telegram</button><button class="act no" data-rf="reject">Отказать</button>' : ""}
      ${["approved", "partially_refunded"].includes(r.status) && r.mode === "balance" ? '<button class="act gray" data-rf="cancel">Отменить (24 ч)</button>' : ""}`) : '<p class="muted">Заявок нет.</p>'}</div>`;
}
$("#main").addEventListener("click", async (e) => {
  const f = e.target.closest("[data-rff]");
  if (f) { refundFilter = f.dataset.rff; reload(); return; }
  if (e.target.id === "refundCsv") {
    e.preventDefault();
    const r = await fetch("/api/admin/refunds/export", {headers: {"X-Admin-Session": session()}});
    const url = URL.createObjectURL(await r.blob()); const a = document.createElement("a"); a.href = url; a.download = "refunds.csv"; a.click(); return;
  }
  const b = e.target.closest("[data-rf]");
  if (!b) return;
  const id = b.closest("[data-rid]").dataset.rid;
  try {
    const act = b.dataset.rf;
    if (act === "take") await post(`/api/admin/refunds/${id}/take`);
    if (act === "approve-balance" || act === "approve-full") {
      if (!confirm(act === "approve-full" ? "Вернуть ВСЮ сумму платежа в Telegram? Это нельзя отменить." : "Вернуть неиспользованную часть на баланс человека?")) return;
      await post(`/api/admin/refunds/${id}/approve`, {mode: act === "approve-full" ? "full" : "balance", comment: askOpt("Комментарий человеку") || ""});
    }
    if (act === "reject") { const why = ask("Причина отказа (человек её увидит):"); if (!why) return; await post(`/api/admin/refunds/${id}/reject`, {reason: why}); }
    if (act === "cancel") { if (!confirm("Забрать возврат обратно с баланса человека?")) return; await post(`/api/admin/refunds/${id}/cancel`); }
    say("Готово"); reload();
  } catch (err) { say(err.message); }
});

// ---- безопасность ----
async function loadSecurity() {
  api("/api/admin/security/ips").then((r) => { if ($("#ipMine")) { $("#ipMine").textContent = r.your_ip; $("#ipList").value = r.ips; } }).catch(() => {});
  const [flags, pii, logins] = await Promise.all([api("/api/admin/flags"), api("/api/admin/security/pii-views?limit=60"), api("/api/admin/security/logins?limit=40")]);
  $("#main").innerHTML = `<div class="card"><h3>Функции: включить без релиза</h3><p class="muted">«Только сотрудникам» — чтобы проверить на себе до запуска для всех. Каждое переключение записывается в журнал.</p>
    ${flags.map((f) => `<div class="row" style="justify-content:space-between;border-bottom:1px solid var(--line);padding:8px 0"><div><b>${esc(f.title)}</b><div class="muted">${esc(f.hint)}</div></div>
      <select data-flag="${esc(f.key)}">${Object.entries(f.states).map(([k, v]) => `<option value="${k}" ${k === f.state ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>`).join("")}</div>
    <div class="card"><h3>Доверенные адреса суперадмина</h3><p class="muted">Если заполнить, суперадмин сможет работать в панели только с этих адресов (через запятую). Ваш адрес сейчас: <b id="ipMine">…</b>. Он должен быть в списке.</p>
      <div class="row"><input id="ipList" placeholder="например: 203.0.113.5, 198.51.100.7" style="flex:1;min-width:240px"><button class="act" id="ipSave">Сохранить</button></div></div>
    <div class="card"><h3>Кто смотрел личные данные</h3>${pii.by_staff.length ? `<p class="muted">Всего в списке: ${pii.items.length}. Больше всех: ${pii.by_staff.slice(0, 3).map((x) => "…" + esc(x.staff.slice(-4)) + " — " + x.views).join(", ")}</p>` : '<p class="muted">Пока никто.</p>'}
      <table><tr><th>Когда</th><th>Сотрудник</th><th>Что открыл</th></tr>${pii.items.map((i) => `<tr><td>${esc(fmt(i.at))}</td><td>…${esc(i.staff.slice(-4))}</td><td>${esc(i.kind === "listing" ? "объявление №" : i.kind === "user" ? "человек " : i.kind)}${esc(i.id)}</td></tr>`).join("")}</table></div>
    <div class="card"><h3>Входы сотрудников</h3><table><tr><th>Когда</th><th>Кто</th><th>Как</th><th>Адрес</th><th>Устройство</th></tr>${logins.map((l) => `<tr><td>${esc(fmt(l.at))}</td><td>…${esc(l.staff.slice(-4))}</td><td>${esc(l.how)}</td><td>${esc(l.ip)}</td><td>${esc(l.device)}</td></tr>`).join("")}</table></div>`;
}
$("#main").addEventListener("click", async (e) => {
  if (e.target.id !== "ipSave") return;
  try { await api("/api/admin/security/ips", {method: "PUT", body: JSON.stringify({ips: $("#ipList").value})}); say("Сохранено"); } catch (err) { say(err.message); }
});
$("#main").addEventListener("change", async (e) => {
  const f = e.target.closest("[data-flag]");
  if (!f) return;
  try { await api(`/api/admin/flags/${f.dataset.flag}`, {method: "PUT", body: JSON.stringify({state: f.value})}); say("Сохранено"); }
  catch (err) { say(err.message); reload(); }
});

// ---- пакеты: массовые действия ----
let batchPrev = null;
async function loadBatches() {
  const recent = await api("/api/admin/batches");
  $("#main").innerHTML = `<div class="card"><h3>Новый пакет</h3><p class="muted">Действие применяется ко ВСЕМУ результату фильтра (до 500 объявлений), а не к одной странице. Сначала «Посчитать», потом подтвердите числом.</p>
    <div class="gridform"><label>Раздел<select id="bCat"><option value="">все</option><option value="job">Работа</option><option value="housing">Жильё</option><option value="marketplace">Барахолка</option></select></label>
    <label>Статус<select id="bStatus"><option value="pending">на проверке</option><option value="approved">опубликовано</option><option value="hidden">скрыто</option><option value="rejected">отклонено</option></select></label>
    <label>Слова в тексте<input id="bQ" maxlength="60"></label>
    <label>Действие<select id="bDec"><option value="approve">Одобрить</option><option value="reject">Отклонить</option><option value="hide">Скрыть</option><option value="show">Вернуть в показ</option></select></label>
    <label>Причина (для отказа и скрытия)<input id="bReason" maxlength="300"></label></div>
    <div class="row"><button class="act" id="bCount">Посчитать</button></div><div id="bOut"></div></div>
    <div class="card"><h3>Последние пакеты</h3>${recent.length ? `<table><tr><th>№</th><th>Действие</th><th>Готово</th><th>Статус</th><th></th></tr>${recent.map((b) => `<tr data-bid="${b.id}"><td>${esc(b.id)}</td><td>${esc(b.decision)}</td><td>${b.done}/${b.total}${b.failed.length ? ` · ошибок ${b.failed.length}` : ""}</td><td>${esc(b.status)}</td>
      <td>${b.can_undo ? '<button class="act gray" data-bundo>Отменить пакет</button>' : ""}</td></tr>`).join("")}</table>` : '<p class="muted">Пакетов ещё не было.</p>'}</div>`;
}
const batchTarget = () => ({category: $("#bCat").value || null, status: $("#bStatus").value, q: $("#bQ").value});
$("#main").addEventListener("click", async (e) => {
  try {
    if (e.target.id === "bCount") {
      batchPrev = await post("/api/admin/batches/preview", batchTarget());
      $("#bOut").innerHTML = `<p><b>Затронется: ${batchPrev.count}</b>${batchPrev.skipped.length ? ` · пропущено ${batchPrev.skipped.length} (${[...new Set(batchPrev.skipped.map((s) => s.why))].join("; ")})` : ""}</p>
        ${batchPrev.count ? `<div class="row"><input id="bConfirm" type="number" placeholder="Введите число ${batchPrev.count}" style="width:200px"><button class="act no" id="bStart">Запустить</button></div>` : ""}<div id="bProg"></div>`;
    }
    if (e.target.id === "bStart") {
      const b = await post("/api/admin/batches", {target: batchTarget(), decision: $("#bDec").value, reason: $("#bReason").value, confirm_count: Number($("#bConfirm").value)});
      let cur = b;
      $("#bProg").innerHTML = `<div class="card"><div style="height:10px;background:var(--surface2);border-radius:6px;overflow:hidden"><i id="bBar" style="display:block;height:100%;width:0;background:var(--grad)"></i></div><p id="bTxt" class="muted"></p><button class="act gray" id="bStop">Остановить</button></div>`;
      window.__bStop = false;
      while (cur.status === "running" && !window.__bStop) {
        cur = await post(`/api/admin/batches/${b.id}/step`);
        $("#bBar").style.width = Math.round(cur.done / cur.total * 100) + "%";
        $("#bTxt").textContent = `Готово ${cur.done} из ${cur.total}`;
      }
      if (window.__bStop) await post(`/api/admin/batches/${b.id}/stop`);
      say("Пакет завершён"); loadBatches();
    }
    if (e.target.id === "bStop") window.__bStop = true;
    const u = e.target.closest("[data-bundo]");
    if (u) {
      if (!confirm("Вернуть всё, что изменил этот пакет (кроме того, что потом поменяли отдельно)?")) return;
      const r = await post(`/api/admin/batches/${u.closest("[data-bid]").dataset.bid}/undo`);
      say(`Возвращено ${r.returned}, не тронуто ${r.left_as_is}`); loadBatches();
    }
  } catch (err) { say(err.message); }
});

// ---- дежурства ----
async function loadDuty() {
  const d = await api("/api/admin/duty");
  const who = (id) => (id ? "…" + esc(String(id).slice(-4)) : "никто");
  const canEdit = ["admin", "super_admin"].includes(role());
  $("#main").innerHTML = `<div class="cols" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr))">${Object.entries(d.areas).map(([k, a]) => `<div class="card"><small class="muted">Сейчас дежурит</small><h3 style="margin:2px 0">${esc(a.title)}</h3><b>${who(a.on_duty)}</b>
      ${a.on_duty ? "" : '<div class="muted">Никого: уведомления идут всем с нужной ролью</div>'}</div>`).join("")}</div>
    ${d.open_alerts.length ? `<div class="card"><h3>Ждут ответа</h3>${d.open_alerts.map((a) => `<div class="row" data-aid="${a.id}"><span class="badge">${a.level === 0 ? "дежурный" : a.level === 1 ? "вся область" : "суперадмины"}</span> ${esc(a.text)} <span class="muted">${esc(fmt(a.at))}</span><button class="act gray" data-ack>Принято</button></div>`).join("")}</div>` : ""}
    ${canEdit ? `<div class="card"><h3>Назначить смену</h3><div class="gridform"><label>Область<select id="dArea">${Object.entries(d.areas).map(([k, a]) => `<option value="${k}">${esc(a.title)}</option>`).join("")}</select></label>
      <label>Кто<select id="dWho">${d.admins.map((a) => `<option value="${esc(a.tg_id)}">…${esc(a.tg_id.slice(-4))} · ${esc(a.role)}</option>`).join("")}</select></label>
      <label>С<input id="dFrom" type="datetime-local"></label><label>До<input id="dTo" type="datetime-local"></label></div>
      <button class="act" id="dAdd">Назначить</button></div>` : ""}
    <div class="card"><h3>Ближайшие смены</h3>${d.shifts.length ? `<table><tr><th>Область</th><th>Кто</th><th>С</th><th>До</th><th></th></tr>${d.shifts.map((s) => `<tr data-sid="${s.id}"><td>${esc(d.areas[s.area]?.title || s.area)}</td><td>${who(s.tg_id)} <small class="muted">${esc(s.role)}</small></td>
      <td>${esc(fmt(s.from))}</td><td>${esc(fmt(s.to))}</td><td>${canEdit ? '<button class="act gray" data-sdel>Убрать</button>' : ""}</td></tr>`).join("")}</table>` : '<p class="muted">Смен нет.</p>'}
      <p class="muted">Если дежурный не открыл панель за ${"30"} минут, сообщение получает вся область, через час — суперадмины (время в «Правилах» → «Сотрудники»).</p></div>
    <div class="card"><h3>Кто что получает</h3><table><tr><th>Событие</th><th>Область</th><th>Роли</th></tr>${d.matrix.map((m) => `<tr><td>${esc(m.what)}</td><td>${esc(m.area)}</td><td>${m.roles.map(esc).join(", ")}</td></tr>`).join("")}</table></div>`;
}
$("#main").addEventListener("click", async (e) => {
  try {
    if (e.target.id === "dAdd") {
      await post("/api/admin/duty", {area: $("#dArea").value, tg_id: $("#dWho").value, starts_at: new Date($("#dFrom").value).toISOString(), ends_at: new Date($("#dTo").value).toISOString()});
      say("Смена назначена"); reload();
    }
    const del = e.target.closest("[data-sdel]");
    if (del && confirm("Убрать смену?")) { await api(`/api/admin/duty/${del.closest("[data-sid]").dataset.sid}`, {method: "DELETE"}); reload(); }
    const ack = e.target.closest("[data-ack]");
    if (ack) { await post(`/api/admin/alerts/${ack.closest("[data-aid]").dataset.aid}/ack`); reload(); }
  } catch (err) { say(err.message); }
});

// ---- боковая панель для правки (список остаётся рядом) ----
function openDrawer(title, html) {
  closeDrawer();
  const d = document.createElement("aside");
  d.id = "drawer"; d.className = "drawer";
  d.innerHTML = `<div class="dhd"><b>${title}</b><button class="act gray" data-drawer-close>✕ Закрыть</button></div><div class="dbody">${html}</div>`;
  $("#main").appendChild(d);
  return d;
}
function closeDrawer() { document.getElementById("drawer")?.remove(); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.getElementById("drawer") && $("#pal").classList.contains("hidden")) closeDrawer(); });
async function adminImage(url) {
  const r = await fetch(url, {headers: {"X-Admin-Session": session()}});
  return r.ok ? URL.createObjectURL(await r.blob()) : "";
}

// ---- объявления: один список со всем нужным ----
let allFilter = {category: "", status: "", q: "", author: "", flag: ""}, allLimit = 30;
const ST_BADGE = {approved: "ok", pending: "warn", rejected: "bad", hidden: "bad", needs_fix: "warn"};

const RUB = (n) => (n == null ? "—" : new Intl.NumberFormat("ru-RU").format(n) + " ₩");
const LIST_COLS = {
  id: ["№", (x) => x.id, (x) => x.id],
  category: ["Раздел", (x) => esc(CAT[x.category] || x.category), (x) => x.category],
  title: ["Название", (x) => `<b>${esc(x.title)}</b> <a href="#" data-inline="${x.id}:title" title="Править название">✏</a>${x.company_name ? `<br><small class="muted">🏬 ${esc(x.company_name)}</small>` : ""}`, (x) => (x.title || "").toLowerCase()],
  city: ["Город", (x) => esc(x.city), (x) => x.city],
  price: ["Цена", (x) => `${x.price_amount != null ? RUB(x.price_amount) : esc(x.price || "—")} <a href="#" data-inline="${x.id}:price_amount" title="Править цену">✏</a>`, (x) => x.price_amount ?? -1],
  status: ["Статус", (x) => `<span class="badge ${ST_BADGE[x.status] || ""}">${esc(STATUS_RU[x.status] || x.status)}</span>${x.has_edit ? ' <span class="badge warn">правка</span>' : ""}${(x.flags || []).length ? " ⚠" : ""}`, (x) => x.status],
  author: ["Автор", (x) => esc(x.author_name || "…" + String(x.author_id || "").slice(-4)), (x) => x.author_id],
  views: ["👁", (x) => x.views || 0, (x) => x.views || 0],
  created: ["Создано", (x) => esc(fmt(x.created_at).slice(0, 10)), (x) => x.created_at || ""],
  expires: ["Срок до", (x) => esc(fmt(x.expires_at).slice(0, 10) || "—"), (x) => x.expires_at || ""],
  promo: ["Продвижение", (x) => esc(x.promo || "—"), (x) => x.promo || ""],
};
const LIST_DEFAULT = ["id", "category", "title", "city", "price", "status", "author", "views", "created"];
async function loadAllListings() {
  await loadClaims("listing");
  TEMPLATES = TEMPLATES.length ? TEMPLATES : await api("/api/admin/templates");
  const f = allFilter;
  const kitL = listKit("listings", LIST_COLS, LIST_DEFAULT, () => allFilter, (x) => { allFilter = {category: "", status: "", flag: "", q: "", author: "", ...x}; allLimit = 30; });
  kitL.groupFns = {status: (x) => STATUS_RU[x.status] || x.status, category: (x) => CAT[x.category] || x.category, author: (x) => x.author_name || "…" + String(x.author_id || "").slice(-4), city: (x) => x.city};
  const bar = await kitToolbar(kitL, {status: "статус", category: "раздел", author: "автор", city: "город"});
  $("#main").innerHTML = `<form id="allForm" class="filters"><select name="category"><option value="">Все разделы</option>${Object.entries(CAT).map(([k, v]) => `<option value="${k}" ${f.category === k ? "selected" : ""}>${v}</option>`).join("")}</select>
    <select name="status"><option value="">Любой статус</option>${Object.entries(STATUS_RU).map(([k, v]) => `<option value="${k}" ${f.status === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
    <select name="flag">${[["", "Любые"], ["edit", "С правкой на проверке"], ["quarantine", "В карантине"], ["dispute", "В споре"], ["company", "От компаний"]].map(([k, v]) => `<option value="${k}" ${f.flag === k ? "selected" : ""}>${v}</option>`).join("")}</select>
    <input name="q" placeholder="Слово в названии или тексте" value="${esc(f.q)}" style="width:210px"><input name="author" placeholder="ID автора" value="${esc(f.author)}" style="width:120px">
    <button class="act" type="submit">Показать</button><span class="muted" id="allTotal"></span></form>${bar}<div id="allList"></div>`;
  const q = new URLSearchParams({limit: String(allLimit)});
  Object.entries(f).forEach(([k, v]) => { if (v) q.set(k, v); });
  const r = await api("/api/admin/listing-list?" + q);
  $("#allTotal").textContent = "Найдено: " + r.total;
  window.__alllist = Object.fromEntries(r.items.map((x) => [x.id, x]));
  $("#allList").innerHTML = (r.items.length ? kitTable(kitL, r.items, (x) => `class="clickrow" data-lrow="${x.id}"`, () => '<button class="act gray">Открыть</button>')
    : '<div class="card muted">Ничего не нашли.</div>') + (r.total > r.items.length ? '<button class="act gray" id="allMore">Показать ещё</button>' : "");
}
$("#main").addEventListener("submit", (e) => {
  if (e.target.id !== "allForm") return;
  e.preventDefault();
  allFilter = Object.fromEntries(new FormData(e.target).entries()); allLimit = 30; reload();
});
const FIELD_DEFS = {
  common: [["title", "Название", "text"], ["description", "Описание", "area"], ["city", "Место (город или район из списка)", "text"], ["price", "Пометка к цене", "text"], ["price_amount", "Сумма, ₩", "number"], ["contact", "Контакт", "text"]],
  job: [["salary_period", "Период зарплаты", "select", {month: "в месяц", day: "в день", hour: "в час"}], ["employment", "Занятость", "select", {full: "полная", part: "частичная", daily: "подённая", contract: "контракт"}],
        ["schedule", "График", "select", {day: "день", night: "ночь", shift: "сменный"}], ["housing_provided", "Жильё дают", "bool"], ["meals_provided", "Питание дают", "bool"]],
  housing: [["housing_type", "Тип жилья", "select", {apartment: "квартира", studio: "студия", room: "комната", dorm: "общежитие", officetel: "офистель", house: "дом"}], ["deposit", "Залог, ₩", "number"], ["area_m2", "Площадь, м²", "number"],
            ["rooms", "Комнат", "number"], ["pets_allowed", "Можно с животными", "bool"], ["furnished", "С мебелью", "bool"]],
  marketplace: [["condition", "Состояние", "select", {new: "новое", used: "б/у"}], ["negotiable", "Торг возможен", "bool"]],
};
function editFormHtml(x) {
  const defs = [...FIELD_DEFS.common, ...(FIELD_DEFS[x.category] || [])];
  const input = ([k, label, type, opts]) => {
    const v = x[k];
    if (type === "area") return `<label class="wide">${label}<textarea data-lf="${k}" rows="5">${esc(v || "")}</textarea></label>`;
    if (type === "bool") return `<label class="chk"><input type="checkbox" data-lf="${k}" ${v ? "checked" : ""}> ${label}</label>`;
    if (type === "select") return `<label>${label}<select data-lf="${k}"><option value="">—</option>${Object.entries(opts).map(([o, t]) => `<option value="${o}" ${v === o ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;
    return `<label>${label}<input data-lf="${k}" type="${type}" value="${esc(v ?? "")}"></label>`;
  };
  const visas = x.category === "job" ? `<div class="wide"><small class="muted">Визы, которые подходят</small><div class="filters">${["E-9", "E-7", "F-2", "F-4", "F-5", "F-6", "D-2", "D-4", "H-2", "C-4", "G-1"].map((v) => `<label class="chk"><input type="checkbox" data-lvisa="${v}" ${(x.visas || []).includes(v) ? "checked" : ""}> ${v}</label>`).join("")}</div></div>` : "";
  return `<div class="gridform lf">${defs.map(input).join("")}${visas}</div><div class="row"><button class="act ok" data-lfsave>💾 Сохранить правку</button><span class="muted">Применяется сразу, автор получит уведомление, изменения попадут в журнал.</span></div>`;
}
async function openListingDrawer(id) {
  try {
    const [x, h] = await Promise.all([api("/api/admin/listings/" + id), api(`/api/admin/listings/${id}/history`)]);
    x.flags = x.flags || [];
    const flags = `<div class="row"><b>Метки:</b> ${h.flags.quarantine ? '<span class="badge bad">🧪 карантин</span>' : ""}${h.flags.dispute ? '<span class="badge warn">⚖ спор</span>' : ""}${h.flags.company_id ? '<span class="badge">🏬 компания</span>' : ""}
      <button class="act gray" data-flagset="quarantine" data-on="${h.flags.quarantine ? 0 : 1}">${h.flags.quarantine ? "Снять карантин" : "В карантин"}</button><button class="act gray" data-flagset="dispute" data-on="${h.flags.dispute ? 0 : 1}">${h.flags.dispute ? "Закрыть спор" : "Открыть спор"}</button></div>`;
    const hist = `<h4>История</h4>${h.journal.map((j) => `<div class="drun"><span>${esc(j.action)} <small class="muted">${esc(actorName(j.who))}</small>${j.details ? `<br><small>${esc(j.details)}</small>` : ""}</span><span class="muted">${esc(fmt(j.at))}</span></div>`).join("") || '<p class="muted">Пока ничего.</p>'}
      ${h.reports.length ? `<h4>Жалобы (${h.reports.length})</h4>${h.reports.map((r) => `<div class="drun"><span>${esc(REASON_RU[r.reason] || r.reason)} <small class="muted">${esc(r.comment || "")}</small></span><span class="muted">${esc(r.status)}</span></div>`).join("")}` : ""}
      ${h.services.length ? `<h4>Платные услуги</h4>${h.services.map((s) => `<div class="drun"><span>${esc(s.kind)}${s.free ? " (по акции)" : ""} · ${esc(s.status)}</span><span class="muted">до ${esc(fmt(s.to).slice(0, 10))}</span></div>`).join("")}` : ""}`;
    openDrawer(`#${x.id} · ${esc(x.title)}`, `${listingCardHtml(x)}${flags}<h4>Редактирование</h4><div data-lid="${x.id}">${editFormHtml(x)}</div>${hist}`);
  } catch (err) { say(err.message); }
}
$("#main").addEventListener("click", async (e) => {
  if (e.target.closest("[data-backlist]")) { show("users"); return; }
  if (e.target.closest("[data-drawer-close]")) { closeDrawer(); return; }
  const row = e.target.closest("[data-lrow]");
  if (row) { openListingDrawer(row.dataset.lrow); return; }
  if (e.target.id === "allMore") { allLimit += 30; reload(); return; }
  const box = e.target.closest("[data-lid]");
  try {
    if (e.target.closest("[data-lfsave]") && box) {
      const id = box.dataset.lid, orig = (window.__alllist || {})[id] || await api("/api/admin/listings/" + id), body = {};
      box.querySelectorAll("[data-lf]").forEach((el) => {
        const k = el.dataset.lf, v = el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
        if (v !== null && v !== "" && v !== (orig[k] ?? (el.type === "checkbox" ? false : ""))) body[k] = v;
      });
      const visas = [...box.querySelectorAll("[data-lvisa]")].filter((c) => c.checked).map((c) => c.dataset.lvisa);
      if (box.querySelector("[data-lvisa]") && visas.join() !== (orig.visas || []).join()) body.visas = visas;
      if (!Object.keys(body).length) { say("Изменений нет"); return; }
      const r = await api(`/api/admin/listings/${id}/full`, {method: "PUT", body: JSON.stringify(body)});
      say("Сохранено: " + (r.changed.join(", ") || "без изменений")); openListingDrawer(id); return;
    }
    const fl = e.target.closest("[data-flagset]");
    if (fl) {
      const id = document.querySelector("#drawer [data-id]").dataset.id, why = ask("Причина (остаётся в журнале):"); if (!why) return;
      await post(`/api/admin/listings/${id}/flags`, {[fl.dataset.flagset]: fl.dataset.on === "1", reason: why}); say("Готово"); openListingDrawer(id);
    }
  } catch (err) { say(err.message); }
});

// ---- знакомства: все анкеты с правкой ----
let datFilter = {status: "", gender: "", flag: "", q: ""}, datLimit = 30;
const D_FLAG = [["", "Любые"], ["selfie", "Ждут проверки селфи"], ["paused", "На паузе"], ["suspect", "Под сомнением"], ["vip", "С VIP"], ["invisible", "Невидимки"]];
const DAT_COLS = {
  id: ["№", (p) => p.id, (p) => p.id],
  name: ["Имя", (p) => `<b>${esc(p.name || "без имени")}</b> ${p.gender === "f" ? "♀" : "♂"}`, (p) => (p.name || "").toLowerCase()],
  age: ["Возраст", (p) => p.age, (p) => p.age],
  city: ["Город", (p) => esc(p.city), (p) => p.city],
  status: ["Статус", (p) => `<span class="badge ${ST_BADGE[p.status === "published" ? "approved" : p.status] || ""}">${esc({pending: "на проверке", published: "опубликована", rejected: "отклонена"}[p.status] || p.status)}</span>`, (p) => p.status],
  marks: ["Метки", (p) => `${p.selfie_status === "approved" ? "✔ " : p.selfie_status === "pending" ? "📷 " : ""}${p.paused ? "⏸ " : ""}${p.suspect ? "⚠ " : ""}${p.vip ? "👑 " : ""}${p.invisible ? "👻" : ""}`, (p) => (p.vip ? 2 : 0) + (p.suspect ? 1 : 0)],
  likes: ["Симпатии", (p) => `${p.likes_in}↓ ${p.likes_out}↑`, (p) => p.likes_in],
  likes_in: ["Получено", (p) => p.likes_in, (p) => p.likes_in],
};
const DAT_DEFAULT = ["id", "name", "age", "city", "status", "marks", "likes"];
async function loadDatingAll() {
  const f = datFilter;
  const kitD = listKit("dating", DAT_COLS, DAT_DEFAULT, () => datFilter, (x) => { datFilter = {status: "", gender: "", flag: "", q: "", ...x}; datLimit = 30; });
  kitD.groupFns = {status: (p) => ({pending: "на проверке", published: "опубликованы", rejected: "отклонены"}[p.status] || p.status), city: (p) => p.city, gender: (p) => (p.gender === "f" ? "женщины" : "мужчины")};
  const bar = await kitToolbar(kitD, {status: "статус", city: "город", gender: "пол"});
  $("#main").innerHTML = `<form id="datForm" class="filters"><select name="status"><option value="">Любой статус</option>${[["pending", "на проверке"], ["published", "опубликованы"], ["rejected", "отклонены"]].map(([k, v]) => `<option value="${k}" ${f.status === k ? "selected" : ""}>${v}</option>`).join("")}</select>
    <select name="gender"><option value="">Любой пол</option><option value="f" ${f.gender === "f" ? "selected" : ""}>женщины</option><option value="m" ${f.gender === "m" ? "selected" : ""}>мужчины</option></select>
    <select name="flag">${D_FLAG.map(([k, v]) => `<option value="${k}" ${f.flag === k ? "selected" : ""}>${v}</option>`).join("")}</select>
    <input name="q" placeholder="Имя, город, слово, ID" value="${esc(f.q)}" style="width:200px"><button class="act" type="submit">Показать</button><span class="muted" id="datTotal"></span></form>${bar}<div id="datList"></div>`;
  const q = new URLSearchParams({limit: String(datLimit)});
  Object.entries(f).forEach(([k, v]) => { if (v) q.set(k, v); });
  const r = await api("/api/admin/dating-list?" + q);
  $("#datTotal").textContent = "Найдено: " + r.total;
  window.__datlist = Object.fromEntries(r.items.map((x) => [x.id, x]));
  $("#datList").innerHTML = (r.items.length ? kitTable(kitD, r.items, (p) => `class="clickrow" data-drow="${p.id}"`, () => '<button class="act gray">Открыть</button>')
    : '<div class="card muted">Ничего не нашли.</div>') + (r.total > r.items.length ? '<button class="act gray" id="datMore">Показать ещё</button>' : "");
}
$("#main").addEventListener("submit", (e) => {
  if (e.target.id !== "datForm") return;
  e.preventDefault();
  datFilter = Object.fromEntries(new FormData(e.target).entries()); datLimit = 30; reload();
});
async function openDatingDrawer(id) {
  const p = (window.__datlist || {})[id] || (await api(`/api/admin/dating-list?q=${id}&limit=50`)).items.find((x) => String(x.id) === String(id));
  if (!p) { say("Анкета не найдена"); return; }
  const sel = (k, opts) => `<select data-df="${k}">${Object.entries(opts).map(([o, t]) => `<option value="${o}" ${(p[k] || "") === o ? "selected" : ""}>${t}</option>`).join("")}</select>`;
  const d = openDrawer(`Анкета #${p.id} · ${esc(p.name || "без имени")}`, `<div class="row" data-did="${p.id}">
    ${p.has_photo ? '<div><div class="muted">Главное фото</div><img data-dphoto alt="" style="max-width:240px;border-radius:12px"></div>' : '<span class="muted">Фото нет</span>'}
    ${p.has_selfie ? '<div><div class="muted">Селфи для проверки (видят только модераторы)</div><img data-dselfie alt="" style="max-width:240px;border-radius:12px"></div>' : ""}</div>
    <div class="row" data-did="${p.id}">${p.status !== "published" ? '<button class="act ok" data-ddec="approve">Опубликовать</button>' : ""}${p.status !== "rejected" ? '<button class="act no" data-ddec="reject">Отклонить…</button>' : ""}
      ${p.selfie_status === "pending" ? '<button class="act ok" data-ddec="verify">✔ Тот же человек</button><button class="act warn" data-ddec="reject_selfie">Селфи не подходит</button>' : p.selfie_status === "approved" ? '<span class="badge ok">✔ подтверждена</span>' : ""}
      ${p.has_photo ? '<button class="act gray" data-dphoto-del>Убрать фото</button>' : ""}</div>
    <h4>Редактирование</h4><div class="gridform" data-did="${p.id}"><label>Имя<input data-df="first_name" value="${esc(p.name)}" maxlength="40"></label><label>Город<input data-df="city" value="${esc(p.city)}"></label>
      <label>Рост<input data-df="height" type="number" value="${p.height ?? ""}"></label><label>Кого ищет${sel("seeking", {"": "—", f: "женщину", m: "мужчину", any: "не важно"})}</label>
      <label>Цель${sel("goal", {"": "—", friendship: "дружба", relationship: "отношения", marriage: "брак", chat: "общение"})}</label><label>Виза<input data-df="visa" value="${esc(p.visa || "")}" maxlength="8"></label>
      <label class="wide">О себе<textarea data-df="bio" rows="4" maxlength="500">${esc(p.bio)}</textarea></label>
      <label class="chk"><input type="checkbox" data-df="paused" ${p.paused ? "checked" : ""}> На паузе</label><label class="chk"><input type="checkbox" data-df="suspect" ${p.suspect ? "checked" : ""}> Под сомнением (скрыта)</label></div>
    <div class="row" data-did="${p.id}"><button class="act ok" data-dsave>💾 Сохранить</button><button class="act no" data-ddelete>🗑 Удалить анкету…</button></div>
    <p class="muted">Автор: <a href="#" data-gu="${esc(p.owner)}">ID …${esc(p.owner.slice(-4))}</a> · симпатий получено ${p.likes_in}, отправлено ${p.likes_out}${p.note ? " · комментарий: " + esc(p.note) : ""}</p>`);
  if (p.has_photo) adminImage(`/api/admin/dating/${p.id}/photo`).then((u) => { const i = d.querySelector("[data-dphoto]"); if (i) i.src = u; });
  if (p.has_selfie) adminImage(`/api/admin/dating/${p.id}/selfie`).then((u) => { const i = d.querySelector("[data-dselfie]"); if (i) i.src = u; });
}
$("#main").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-drow]");
  if (row) { openDatingDrawer(row.dataset.drow); return; }
  if (e.target.id === "datMore") { datLimit += 30; reload(); return; }
  const box = e.target.closest("[data-did]");
  if (!box) return;
  const id = box.dataset.did;
  try {
    const dec = e.target.closest("[data-ddec]");
    if (dec) {
      let q = "";
      if (dec.dataset.ddec === "reject") { const why = ask("Причина отказа (автор анкеты её увидит)"); if (!why) return; q = "?reason=" + encodeURIComponent(why); }
      await post(`/api/admin/dating/${id}/${dec.dataset.ddec}${q}`); say("Готово"); closeDrawer(); reload(); return;
    }
    if (e.target.closest("[data-dsave]")) {
      const body = {};
      document.querySelectorAll("#drawer [data-df]").forEach((el) => { const k = el.dataset.df; body[k] = el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value; });
      Object.keys(body).forEach((k) => { if (body[k] === null) delete body[k]; });
      body.reason = askOpt("Что поправили (автор увидит)") || "";
      const r = await api(`/api/admin/dating/${id}/edit`, {method: "PUT", body: JSON.stringify(body)}); say("Сохранено: " + (r.changed.join(", ") || "без изменений")); closeDrawer(); reload();
    }
    if (e.target.closest("[data-dphoto-del]") && confirm("Убрать фото? Анкета вернётся на проверку.")) { await api(`/api/admin/dating/${id}/photo`, {method: "DELETE"}); say("Фото убрано"); closeDrawer(); reload(); }
    if (e.target.closest("[data-ddelete]")) {
      const why = ask("Причина удаления (автор её увидит):"); if (!why) return;
      if (!confirm("Удалить анкету вместе с симпатиями и просмотрами? Это нельзя отменить.")) return;
      await post(`/api/admin/dating/${id}/delete`, {reason: why}); say("Анкета удалена"); closeDrawer(); reload();
    }
  } catch (err) { say(err.message); }
});
const LOADERS = {exchange: () => loadExchange(), mynotify: () => loadMyNotify(), dash: loadDash, listings: loadListings, jobs: loadSection, housing: loadSection, market: loadSection, dating: loadDating,
                 employers: loadEmployers, reports: loadReports, reviews: loadReviews, resumes: loadResumes, support: loadSupport, texts: loadTexts, users: loadUsers, community: loadCommunity, guide: loadGuide,
                 marketing: loadMarketing, payments: loadPayments, pricing: loadPricing, rules: loadRules, alllist: loadAllListings, datingall: loadDatingAll, duty: loadDuty, security: loadSecurity, batches: loadBatches, refunds: loadRefunds, companies: loadCompanies, reportsm: loadMoneyReports, stest: loadSearchTest, audit: loadAudit, errors: loadErrors, settings: loadSettings, admins: loadAdmins};

// Старт: есть сессия — открываем; открыто из Telegram — входим сами; иначе показываем кнопку
if (session()) startApp();
else if (tgApp?.initData) autoLogin();

// ---- обменник: звёзды Telegram → KH (валюта сайта) ----
async function loadExchange() {
  const x = await api("/api/admin/exchange");
  const rep = await api("/api/admin/exchange/report?days=30").catch(() => null);
  const rate = x.per_100_stars / 100;
  $("#main").innerHTML = `<div class="card"><h3>Обменник KH</h3>
    <p class="muted">KH — валюта сайта: баланс людей и компаний и цены услуг считаются в KH. Звёзды Telegram — способ купить KH.</p>
    <div class="gridform"><label>Сколько KH даёт 1 ⭐<input id="exRate" type="number" step="0.1" min="0.1" max="10" value="${rate}"></label>
      <label class="chk"><input type="checkbox" id="exOnly" ${x.balance_only ? "checked" : ""}> Услуги только за KH с баланса (звёзды лишь пополняют баланс)</label></div>
    <p class="muted">Сейчас: 1 ⭐ = ${x.kh_per_star} KH. Курс не 1:1 работает только при включённой галочке: иначе цена услуги была бы разной в звёздах и в KH.</p>
    <div class="row"><button class="act ok" id="exSave">💾 Сохранить курс</button></div></div>
    <div class="card"><h3>Что увидят люди</h3><table><tr><th>KH</th><th>Заплатят звёздами</th></tr>${[10, 50, 100, 500, 1000, 10000].map((n) => `<tr><td>${n} KH</td><td>${Math.max(1, Math.ceil(n / x.kh_per_star - 1e-9))} ⭐</td></tr>`).join("")}</table></div>
    <div class="card"><h3>В обращении</h3><p>У людей: <b>${fmtN(x.in_circulation.people)} KH</b> · у компаний: <b>${fmtN(x.in_circulation.companies)} KH</b> · всего <b>${fmtN(x.in_circulation.total)} KH</b></p>
      <p class="muted">В этом месяце: пополнений ${x.month.topups}, получено ${fmtN(x.month.stars)} ⭐, выдано ${fmtN(x.month.kh)} KH.</p></div>
    ${rep ? `<div class="card"><h3>Оборот KH за 30 дней</h3><p class="muted">Куплено ${fmtN(rep.total.kh_bought)} KH за ${fmtN(rep.total.stars_in)} ⭐ · потрачено ${fmtN(rep.total.kh_spent)} KH на ${rep.total.purchases} покупок</p>
      <div class="kchart">${(() => { const peak = Math.max(1, ...rep.days.map((d) => Math.max(d.kh_bought, d.kh_spent))); return rep.days.map((d) => `<div class="kcol" title="${esc(d.day)}: куплено ${d.kh_bought}, потрачено ${d.kh_spent}"><i class="in" style="height:${Math.round(100 * d.kh_bought / peak)}%"></i><i class="out" style="height:${Math.round(100 * d.kh_spent / peak)}%"></i></div>`).join(""); })()}</div>
      <p class="muted"><span class="dot in"></span> куплено KH · <span class="dot out"></span> потрачено KH</p><div class="row"><button class="act gray" id="exCsv">⬇ Выгрузка CSV за 90 дней</button></div></div>` : ""}
    <div class="card"><h3>Потрачено KH в этом месяце: ${fmtN(x.spent_month.kh)}</h3>${x.spent_month.by_service.length ? `<table><tr><th>Услуга</th><th>Покупок</th><th>KH</th></tr>${x.spent_month.by_service.map((r) => `<tr><td>${esc(r.code)}</td><td>${r.n}</td><td>${fmtN(r.kh)}</td></tr>`).join("")}</table>` : '<p class="muted">Пока нет покупок за KH с баланса.</p>'}</div>`;
}
$("#main").addEventListener("click", async (e) => {
  if (e.target.id === "exCsv") {
    const r = await fetch("/api/admin/exchange/report.csv?days=90", {headers: {"X-Admin-Session": session()}});
    const a = document.createElement("a"); a.href = URL.createObjectURL(await r.blob()); a.download = "kh-report.csv"; a.click(); return;
  }
  if (e.target.id !== "exSave") return;
  try {
    const per = Math.round(Number($("#exRate").value) * 100);
    await api("/api/admin/exchange", {method: "PUT", body: JSON.stringify({per_100_stars: per, balance_only: $("#exOnly").checked})});
    say("Курс сохранён"); reload();
  } catch (err) { say(err.message); }
});

// ---- общий набор для списков: колонки, сохранённые виды, сортировка по заголовку, группировка ----
const KITS = {};
function listKit(tab, defs, defaults, getFilter, setFilter) {
  const key = "adminCols:" + tab;
  const stored = () => { try { const c = JSON.parse(localStorage.getItem(key) || "null"); return Array.isArray(c) && c.length ? c.filter((k) => defs[k]) : defaults; } catch (e) { return defaults; } };
  const kit = KITS[tab] = Object.assign(KITS[tab] || {}, {tab, defs, defaults, key, getFilter, setFilter, views: [], sort: (KITS[tab] || {}).sort || {key: "", dir: 1}, group: (KITS[tab] || {}).group || ""});
  kit.cols = stored();
  return kit;
}
async function kitToolbar(kit, groups) {
  const v = await api("/api/admin/views?tab=" + kit.tab).catch(() => ({mine: [], shared: []}));
  kit.views = [...v.mine, ...v.shared];
  return `<div class="filters" data-kit="${kit.tab}"><select data-kitpick><option value="">Сохранённые виды…</option>${v.mine.map((x) => `<option value="${x.id}">${esc(x.name)}${x.shared ? " (общий)" : ""}</option>`).join("")}${v.shared.length ? '<optgroup label="Общие от коллег">' + v.shared.map((x) => `<option value="${x.id}">${esc(x.name)} · ${esc(x.owner)}</option>`).join("") + "</optgroup>" : ""}</select>
    <button class="act gray" data-kitsave>💾 Сохранить этот вид</button><button class="act gray" data-kitshare>👥 Поделиться</button><button class="act gray" data-kitdel>🗑</button>
    ${groups ? `<select data-kitgroup><option value="">Без группировки</option>${Object.entries(groups).map(([k, n]) => `<option value="${k}" ${kit.group === k ? "selected" : ""}>Группы: ${esc(n)}</option>`).join("")}</select>` : ""}
    <details><summary class="act gray" style="display:inline-block;list-style:none">☰ Колонки</summary><div class="card" style="position:absolute;z-index:6">${Object.entries(kit.defs).map(([k, d]) => `<label style="display:block"><input type="checkbox" data-kitcol="${k}" ${kit.cols.includes(k) ? "checked" : ""}> ${d[0]}</label>`).join("")}</div></details></div>`;
}
// таблица с выбранными колонками; d = [название, ячейка(x), значение для сортировки(x), группа(x)]
function kitTable(kit, items, rowAttr, tail) {
  let rows = [...items];
  if (kit.sort.key && kit.defs[kit.sort.key]?.[2]) {
    const f = kit.defs[kit.sort.key][2];
    rows.sort((a, b) => { const x = f(a), y = f(b); return (x > y ? 1 : x < y ? -1 : 0) * kit.sort.dir; });
  }
  const head = kit.cols.map((k) => `<th data-kitsort="${kit.tab}:${k}" style="cursor:pointer">${kit.defs[k][0]}${kit.sort.key === k ? (kit.sort.dir > 0 ? " ▲" : " ▼") : ""}</th>`).join("") + "<th></th>";
  const line = (x) => `<tr ${rowAttr(x)}>${kit.cols.map((k) => `<td>${kit.defs[k][1](x)}</td>`).join("")}<td>${tail(x)}</td></tr>`;
  let body = "";
  const gf = kit.group && kit.groupFns?.[kit.group];
  if (gf) {
    const groups = new Map();
    rows.forEach((x) => { const g = gf(x); (groups.get(g) || groups.set(g, []).get(g)).push(x); });
    for (const [g, list] of groups) body += `<tr class="grouprow"><td colspan="${kit.cols.length + 1}"><b>${esc(String(g))}</b> · ${list.length}</td></tr>` + list.map(line).join("");
  } else body = rows.map(line).join("");
  return `<table><tr>${head}</tr>${body}</table>`;
}
$("#main").addEventListener("change", (e) => {
  const bar = e.target.closest("[data-kit]"); if (!bar) return;
  const kit = KITS[bar.dataset.kit]; if (!kit) return;
  if (e.target.matches("[data-kitcol]")) {
    kit.cols = [...bar.querySelectorAll("[data-kitcol]")].filter((x) => x.checked).map((x) => x.dataset.kitcol);
    if (!kit.cols.length) kit.cols = kit.defaults;
    localStorage.setItem(kit.key, JSON.stringify(kit.cols)); reload(); return;
  }
  if (e.target.matches("[data-kitgroup]")) { kit.group = e.target.value; reload(); return; }
  if (e.target.matches("[data-kitpick]") && e.target.value) {
    const v = kit.views.find((x) => String(x.id) === e.target.value);
    if (v) {
      kit.setFilter(v.state.filter || {});
      if (v.state.cols) { kit.cols = v.state.cols.filter((k) => kit.defs[k]); if (!kit.cols.length) kit.cols = kit.defaults; localStorage.setItem(kit.key, JSON.stringify(kit.cols)); }
      kit.sort = v.state.sort || {key: "", dir: 1}; kit.group = v.state.group || ""; reload();
    }
  }
});
$("#main").addEventListener("click", async (e) => {
  const th = e.target.closest("[data-kitsort]");
  if (th) { const [tab, k] = th.dataset.kitsort.split(":"); const kit = KITS[tab]; kit.sort = {key: k, dir: kit.sort.key === k ? -kit.sort.dir : 1}; reload(); return; }
  const bar = e.target.closest("[data-kit]"); if (!bar) return;
  const kit = KITS[bar.dataset.kit]; if (!kit) return;
  try {
    if (e.target.closest("[data-kitsave]")) {
      const name = prompt("Как назвать этот вид?", ""); if (!name) return;
      await post("/api/admin/views", {tab: kit.tab, name, state: {filter: kit.getFilter(), cols: kit.cols, sort: kit.sort, group: kit.group}, shared: false}); say("Вид сохранён"); reload();
    }
    const pick = bar.querySelector("[data-kitpick]");
    const chosen = pick?.value ? kit.views.find((x) => String(x.id) === pick.value) : null;
    if (e.target.closest("[data-kitshare]")) {
      if (!chosen) { say("Выберите свой вид в списке"); return; }
      if (!chosen.mine) { say("Делиться можно только своим видом"); return; }
      await api(`/api/admin/views/${chosen.id}/share`, {method: "PUT", body: JSON.stringify({shared: !chosen.shared})}); say(chosen.shared ? "Вид снова личный" : "Теперь вид видят все сотрудники"); reload();
    }
    if (e.target.closest("[data-kitdel]")) {
      if (!chosen) { say("Выберите вид в списке"); return; }
      if (!chosen.mine) { say("Удалить можно только свой вид"); return; }
      if (!confirm(`Удалить вид «${chosen.name}»?`)) return;
      await api(`/api/admin/views/${chosen.id}`, {method: "DELETE"}); say("Вид удалён"); reload();
    }
  } catch (err) { say(err.message); }
});
// правка прямо в строке: название и цена объявления
$("#main").addEventListener("click", async (e) => {
  const ie = e.target.closest("[data-inline]");
  if (!ie) return;
  e.preventDefault();
  e.stopPropagation();
  try {
    const [id, field] = ie.dataset.inline.split(":");
    const x = (window.__alllist || {})[id]; if (!x) return;
    const cur = field === "price_amount" ? (x.price_amount ?? "") : x[field];
    const val = prompt(field === "price_amount" ? "Новая цена, ₩ (число)" : "Новое название", cur); if (val === null) return;
    const body = {};
    if (field === "price_amount") { const n = Number(String(val).replace(/\s/g, "")); if (!(n >= 0)) { say("Нужно число"); return; } body.price_amount = n; } else body[field] = val.trim();
    const why = askOpt("Что поправили (автор увидит)") || "";
    await api(`/api/admin/listings/${id}/full`, {method: "PUT", body: JSON.stringify({...body, reason: why})}); say("Сохранено"); reload();
  } catch (err) { say(err.message); }
}, true);

// ---- мои уведомления: что и насколько срочное мне присылает бот ----
async function loadMyNotify() {
  const x = await api("/api/admin/my-notifications");
  const P = {3: "срочное", 2: "важное", 1: "обычное"};
  $("#main").innerHTML = `<div class="card"><h3>О чём вам сообщает бот</h3>
    <p class="muted">Это только ваши настройки. Срочное (безопасность, затянувшаяся модерация) лучше не отключать совсем.</p>
    <div class="row">${x.modes.map((m) => `<label style="margin-right:14px"><input type="radio" name="nmode" value="${m.key}" ${x.mode === m.key ? "checked" : ""}> ${esc(m.title)}</label>`).join("")}</div>
    <h4>Отключить отдельные виды</h4>
    <table><tr><th></th><th>Что</th><th>Насколько срочно</th></tr>${x.kinds.map((k) => `<tr><td><input type="checkbox" data-nmute="${k.key}" ${x.muted.includes(k.key) ? "checked" : ""}></td><td>${esc(k.title)}</td><td>${P[k.priority]}</td></tr>`).join("")}</table>
    <div class="row"><button class="act ok" id="nSave">💾 Сохранить</button></div></div>`;
}
$("#main").addEventListener("click", async (e) => {
  if (e.target.id !== "nSave") return;
  try {
    const mode = document.querySelector("[name=nmode]:checked")?.value || "all";
    const muted = [...document.querySelectorAll("[data-nmute]:checked")].map((c) => c.dataset.nmute);
    await api("/api/admin/my-notifications", {method: "PUT", body: JSON.stringify({mode, muted})}); say("Сохранено");
  } catch (err) { say(err.message); }
});

async function loadIndexVersions() {
  const x = await api("/api/admin/search/index-versions").catch(() => null);
  if (!x || !$("#slCard")) return;
  document.getElementById("ixCard")?.remove();
  $("#slCard").insertAdjacentHTML("afterend", `<div class="card" id="ixCard"><h3>Версии индекса поиска</h3>
    <p class="muted">Объявлений: ${x.listings}. С устаревшим текстом поиска: <b>${x.out_of_date}</b>. Каждая пересборка запоминает старые тексты, её можно откатить.</p>
    ${x.versions.length ? `<table><tr><th>№</th><th>Когда</th><th>Кто</th><th>Изменено</th><th></th></tr>${x.versions.map((v) => `<tr><td>${v.id}</td><td>${esc(fmt(v.at))}</td><td>${esc(v.by)}</td><td>${v.changed}</td>
      <td>${v.rolled_back ? '<span class="muted">откатана</span>' : `<button class="act gray" data-ixrb="${v.id}">↩ Откатить</button>`}</td></tr>`).join("")}</table>` : '<p class="muted">Пересборок пока не было.</p>'}</div>`);
}
$("#main").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-ixrb]");
  if (!b || !confirm("Вернуть старые тексты поиска этой пересборки?")) return;
  try { const r = await post(`/api/admin/search/index-versions/${b.dataset.ixrb}/rollback`); say(`Возвращено: ${r.restored}`); loadIndexVersions(); } catch (err) { say(err.message); }
});

async function companyReviewsAdmin() {
  const [shown, hidden] = await Promise.all([api("/api/admin/company-reviews?status=published"), api("/api/admin/company-reviews?status=hidden")]).catch(() => [[], []]);
  const row = (r, act, label) => `<div class="card" data-crid="${r.id}"><b class="rating">${"★".repeat(r.rating)}</b> · ${esc(r.company)} · от <a href="#" data-gu="${esc(r.reviewer)}">${esc(r.reviewer)}</a>
    <div class="meta">${esc(fmt(r.created_at))}</div><p>${esc(r.text || "(без текста)")}</p>${r.reply ? `<p class="muted"><b>Ответ компании:</b> ${esc(r.reply)}</p>` : ""}
    <button class="act ${act === "hide" ? "no" : "ok"}" data-crdo="${act}">${label}</button></div>`;
  return (shown.length ? shown.map((r) => row(r, "hide", "Скрыть")).join("") : '<div class="card muted">Пока нет.</div>') +
    (hidden.length ? "<h4>Скрытые</h4>" + hidden.map((r) => row(r, "show", "Вернуть")).join("") : "");
}
$("#main").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-crdo]");
  if (!b) return;
  try { await post(`/api/admin/company-reviews/${b.closest("[data-crid]").dataset.crid}/${b.dataset.crdo}`); reload(); } catch (err) { say(err.message); }
});
