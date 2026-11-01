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
function say(text) { $("#status").textContent = text; }
function ask(question) {
  const value = prompt(question, "");
  if (value === null) return null;
  if (value.trim().length < 3) { say("Причина должна быть не короче 3 символов"); return null; }
  return value.trim();
}
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
const SUP = ["super_admin"];
const TABS = [
  {key: "dash", label: "📊 Главная", roles: ADM, title: "Главная"},
  {key: "listings", label: "📋 Модерация", roles: MOD, title: "Очередь модерации"},
  {key: "jobs", label: "💼 Вакансии", roles: MOD, title: "Вакансии", cat: "job"},
  {key: "housing", label: "🏠 Жильё", roles: MOD, title: "Жильё", cat: "housing"},
  {key: "market", label: "🛍 Барахолка", roles: MOD, title: "Барахолка, авто, услуги", cat: "marketplace"},
  {key: "dating", label: "💗 Знакомства", roles: MOD, title: "Анкеты знакомств"},
  {key: "employers", label: "🏢 Работодатели", roles: MOD, title: "Заявки работодателей"},
  {key: "support", label: "💬 Поддержка", roles: SUPPORT, title: "Обращения пользователей"},
  {key: "resumes", label: "🧑‍💼 Резюме", roles: MOD, title: "Резюме соискателей"},
  {key: "reviews", label: "⭐ Отзывы", roles: MOD, title: "Отзывы"},
  {key: "reports", label: "🚩 Жалобы", roles: SUPPORT, title: "Жалобы"},
  {key: "users", label: "👥 Пользователи", roles: SUPPORT, title: "Пользователи"},
  {sep: "Контент", roles: ADM},
  {key: "community", label: "🌍 Сообщество", roles: ADM, title: "Группы сообщества"},
  {key: "guide", label: "📘 Гид", roles: ADM, title: "Статьи гида"},
  {key: "texts", label: "📝 Тексты, FAQ, курс", roles: ADM, title: "Тексты, вопросы и ответы, настройки"},
  {key: "marketing", label: "📣 Рассылки и баннеры", roles: ADM, title: "Рассылки и баннеры"},
  {sep: "Деньги", roles: ADM},
  {key: "payments", label: "💳 Платежи", roles: ADM, title: "Платежи и промокоды"},
  {key: "pricing", label: "💰 Цены", roles: ADM, title: "Цены услуг"},
  {sep: "Система", roles: ADM},
  {key: "errors", label: "🐞 Ошибки в приложении", roles: ADM, title: "Ошибки, которые видели пользователи"},
  {key: "audit", label: "📜 Журнал", roles: ADM, title: "Журнал действий"},
  {key: "settings", label: "⚙️ Выгрузки", roles: ADM, title: "Выгрузки"},
  {key: "admins", label: "🧑‍💼 Сотрудники", roles: SUP, title: "Сотрудники"},
];
let current = "";
function startApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#logout").hidden = false;
  $("#who").textContent = role() + " · ID " + (sessionStorage.getItem("adminId") || "");
  const mine = TABS.filter((t) => t.roles.includes(role()));
  $("#tabs").innerHTML = mine.map((t) => t.sep ? `<div class="sep">${esc(t.sep)}</div>`
    : `<button data-tab="${t.key}">${esc(t.label)}<em hidden data-badge="${t.key}"></em></button>`).join("");
  $("#tabs").querySelectorAll("button").forEach((b) => b.onclick = () => show(b.dataset.tab));
  const first = mine.find((t) => t.key);
  show(first.key);
  refreshBadges();
}
async function logout() {
  try { await fetch("/api/admin/auth/logout", {method: "POST", headers: {"X-Admin-Session": session()}}); } catch (e) { /* ничего */ }
  sessionStorage.clear();
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#logout").hidden = true;
  $("#webLoginBtn").hidden = false;
}
$("#logout").onclick = logout;

const BADGE_KEYS = {listings: "listings", reports: "reports", support: "support", resumes: "resumes", employers: "employers", dating: "dating", errors: "errors"};
async function refreshBadges() {
  try {
    const c = await api("/api/admin/counters");
    const set = (k, n, late) => { const el = document.querySelector(`[data-badge="${k}"]`); if (el) { el.textContent = n; el.hidden = !n; el.classList.toggle("warn", !!late); } };
    for (const [tab, key] of Object.entries(BADGE_KEYS)) set(tab, c[key] || 0, (key === "listings" && c.listings_wait_h >= 24) || (key === "support" && c.support_wait_h >= 24));
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
      <div><button class="act gray" data-gu="${esc(u.tg_id)}">Карточка</button></div></div>`).join("");
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
  const gu = e.target.closest("[data-gu]");
  if (gu) openUserCard(gu.dataset.gu);
});

function show(tab) {
  const def = TABS.find((t) => t.key === tab);
  current = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("#title").textContent = def.title;
  say("Загрузка…");
  $("#main").innerHTML = "";
  LOADERS[tab](def).then(() => { say("Обновлено " + new Date().toLocaleTimeString("ru-RU")); refreshBadges(); }).catch((e) => say(e.message));
}
const reload = () => { if (LOADERS[current]) show(current); else if (current === "search") $("#gSearch").requestSubmit(); };

// ---- главная: показатели, график, воронка ----
function bars(days, key) {
  const max = Math.max(1, ...days.map((d) => d[key]));
  return `<div class="bars">${days.map((d) => `<i class="${d[key] ? "" : "zero"}" style="height:${Math.max(3, Math.round(d[key] / max * 100))}%" title="${esc(d.date)}: ${d[key]}"></i>`).join("")}</div>`;
}
async function fillSystem() {
  try {
    const x = await api("/api/admin/system");
    const ok = (v, t) => `<span class="${v ? "good" : "bad"}">${v ? "✔" : "✖"} ${t}</span>`;
    $("#sysCard").innerHTML = `<h3>Состояние системы</h3><div class="kv"><b>База данных</b><span>${ok(x.db, x.db ? "работает" : "недоступна")}</span>
      <b>Бот</b><span>${ok(x.bot_ok, x.bot_age_s == null ? "нет сигнала" : "отзывается " + x.bot_age_s + " с назад")}</span>
      <b>Свободно на диске</b><span>${x.disk_free_mb == null ? "—" : ok(x.disk_free_mb > 2000, Math.round(x.disk_free_mb / 1024) + " ГБ")}</span>
      <b>Сертификат HTTPS</b><span>${x.cert_days == null ? "не проверяется" : ok(x.cert_days > 14, "ещё " + x.cert_days + " дн.")}</span>
      <b>Ошибки у людей за сутки</b><span>${ok(x.client_errors_24h === 0, x.client_errors_24h)}</span><b>Пользователей</b><span>${x.users}</span></div>`;
  } catch (err) { $("#sysCard").innerHTML = ""; }
}
async function loadDash() {
  const [s, a, queue] = await Promise.all([api("/api/admin/stats"), api("/api/admin/analytics?days=30"), api("/api/admin/listings")]);
  const f = a.funnel;
  $("#main").innerHTML = `
    <div class="stats">
      <div><b>${s.users}</b><small>пользователей</small></div>
      <div><b>${a.totals.users}</b><small>новых за 30 дней</small></div>
      <div><b>${s.listings.approved || 0}</b><small>опубликовано</small></div>
      <div><b>${queue.length}</b><small>ждут проверки</small></div>
      <div><b>${s.reports_new}</b><small>новых жалоб</small></div>
      <div><b>${s.revenue_stars_total} ⭐</b><small>выручка всего</small></div>
      <div><b>${s.week.stars} ⭐</b><small>за неделю (${s.week.sales} продаж)</small></div>
      <div><b>${s.payers}</b><small>платящих</small></div>
    </div>
    <div class="card" id="sysCard"><h3>Состояние системы</h3><p class="muted">Загрузка…</p></div>
    <div class="card"><h3>Новые пользователи за 30 дней</h3>${bars(a.days, "users")}
      <h3 style="margin-top:12px">Новые объявления</h3>${bars(a.days, "listings")}
      <h3 style="margin-top:12px">Заработано звёзд по дням</h3>${bars(a.days, "stars")}<p class="muted">Итого за 30 дней: ${a.totals.stars} ⭐, откликов: ${a.totals.applications}</p></div>
    <div class="card"><h3>Воронка за всё время</h3><div class="funnel">
      <span>👁 просмотры <b>${f.views}</b></span><span>→ 💬 чаты <b>${f.chats}</b></span><span>→ 📨 отклики <b>${f.applications}</b></span><span>→ ⭐ оплаты <b>${f.paid}</b></span></div></div>
    <div class="card"><h3>Откуда пришли (30 дней)</h3>${(a.sources || []).length ? `<table><tr><th>Метка</th><th>Людей</th></tr>${a.sources.map((x) => `<tr><td>${esc(x.source)}</td><td>${x.users}</td></tr>`).join("")}</table><p class="muted">Ссылка для рекламы: t.me/ИМЯ_БОТА?start=src_МЕТКА</p>` : '<p class="muted">Пока нет. Ссылка для рекламы: t.me/ИМЯ_БОТА?start=src_МЕТКА (латиница, цифры, _).</p>'}</div>
    <div class="card"><h3>Заработано по услугам (30 дней)</h3>${a.stars_by_service.length ? `<table><tr><th>Услуга</th><th>⭐</th></tr>${a.stars_by_service.map((x) => `<tr><td>${esc(x.service)}</td><td>${x.stars}</td></tr>`).join("")}</table>` : '<p class="muted">Продаж не было.</p>'}</div>
    <div class="card"><h3>Последние на проверке</h3>${queue.length ? `<table><tr><th>№</th><th>Раздел</th><th>Название</th><th>Город</th><th></th></tr>${queue.slice(0, 8).map((x) => `
      <tr data-id="${x.id}"><td>${x.id}</td><td>${esc(CAT[x.category] || x.category)}</td><td>${esc(x.title)}</td><td>${esc(x.city)}</td>
      <td><button class="act ok" data-quick="approve">Одобрить</button><button class="act no" data-quick="reject">Отклонить</button></td></tr>`).join("")}</table>` : '<p class="muted">Очередь пуста.</p>'}</div>`;
  fillSystem();
}
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

// ---- карточка объявления ----
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
    <p class="muted">Контакт: ${esc(x.contact || "—")} · Автор: ${esc(x.author_id || "—")} · просмотров: ${x.views || 0}${x.note ? " · комментарий: " + esc(x.note) : ""}</p>
    ${buttons}${extraButtons(x)}<div data-editbox></div></div>`;
}
const hoursSince = (iso) => (iso ? Math.max(0, Math.floor((Date.now() - new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime()) / 3600000)) : 0);
function extraButtons(x) {
  const wait = x.status === "pending" || x.has_edit ? `<span class="wait ${hoursSince(x.created_at) >= 24 ? "late" : ""}">ждёт ${hoursSince(x.created_at)} ч</span> ` : "";
  const undo = ["approved", "rejected", "needs_fix", "hidden"].includes(x.status) ? '<button class="act gray" data-undo>↶ Отменить решение</button>' : "";
  const claimed = claimsMap[x.id] ? `<span class="wait late">🔒 в работе: ${esc(claimsMap[x.id])}</span> ` : '<button class="act gray" data-claim>🔒 Взять в работу</button>';
  return `<div class="row" style="margin-top:6px">${wait}${claimed}<button class="act gray" data-edit>✏ Править</button>${undo}</div>`;
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
async function loadListings() {
  await loadClaims("listing");
  TEMPLATES = await api("/api/admin/templates");
  const rows = await api("/api/admin/listings");
  $("#main").innerHTML = `
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
      <div class="meta">Контакт: ${esc(u.contact_person)} · ID ${esc(u.tg_id)}</div>
      <button class="act ok" data-emp="approve">Подтвердить</button><button class="act no" data-emp="reject">Отклонить</button></div>`).join("")
    : '<div class="card muted">Заявок на проверке нет.</div>';
}
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-emp]");
  if (!btn) return;
  try { await post(`/api/admin/employers/${btn.closest("[data-eid]").dataset.eid}/${btn.dataset.emp}`); reload(); } catch (err) { say(err.message); }
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
    <div class="card" data-rid="${r.id}"><b>Жалоба #${r.id}</b> · ${esc(r.target_type)} ${esc(r.target_id)} · причина: ${esc(r.reason)}
      <div class="meta">${esc(r.created_at)}</div><p>${esc(r.comment || "")}</p><div data-ctx></div>
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
    await post(`/api/admin/reports/${card.dataset.rid}/${btn.dataset.rep}`);
    reload();
  } catch (err) { say(err.message); }
});

// ---- поддержка: обращения и оспаривания ----
async function loadSupport() {
  await loadClaims("ticket");
  const rows = await api("/api/admin/tickets?status=open");
  const answered = await api("/api/admin/tickets?status=answered");
  const card = (t) => `<div class="card" data-tid="${t.id}"><label style="float:right"><input type="checkbox" data-tpick> выбрать</label><b>${t.kind === "appeal" ? "Оспаривание" : "Обращение"} #${t.id}</b> · пользователь ${esc(t.user)} · <button class="act gray" data-gu="${esc(t.user)}">карточка</button>
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
async function loadResumes() {
  const rows = await api("/api/admin/resumes?status=pending");
  $("#main").innerHTML = (rows.length ? '<div class="toolbar"><button class="act ok" id="rBulkOk">Опубликовать выбранные</button></div>' : "") + (rows.length ? rows.map((r) => `<div class="card" data-owner="${esc(r.owner)}"><label style="float:right"><input type="checkbox" data-rpick> выбрать</label><b>${esc(r.position)}</b> · ${esc(r.city)} · виза ${esc(r.visa || "—")} · от ${r.salary_from ?? "—"} ₩
    <div class="meta">пользователь ${esc(r.owner)} · ${esc(r.updated || "")}</div><p>${esc(r.experience)}</p><p>${esc(r.about)}</p>
    <button class="act ok" data-cv="approve">Опубликовать</button><button class="act no" data-cv="reject">Отклонить</button> <button class="act gray" data-gu="${esc(r.owner)}">Карточка</button></div>`).join("") : '<div class="card muted">Новых резюме нет.</div>');
}
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
  const row = (r, act, label) => `<div class="card" data-revid="${r.id}"><b>${"★".repeat(r.rating)}</b> · объявление #${r.listing_id} · автор ${esc(r.target)} · от ${esc(r.reviewer)}
    <div class="meta">${esc(r.created_at)}</div><p>${esc(r.text || "(без текста)")}</p><button class="act ${act === "hide" ? "no" : "ok"}" data-rev="${act}">${label}</button></div>`;
  $("#main").innerHTML = `<h3>Опубликованы</h3>` + (shown.length ? shown.map((r) => row(r, "hide", "Скрыть")).join("") : '<div class="card muted">Пока нет.</div>') +
    `<h3>Скрытые</h3>` + (hidden.length ? hidden.map((r) => row(r, "show", "Вернуть")).join("") : '<div class="card muted">Нет.</div>');
}
$("#main").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-rev]");
  if (!b) return;
  try { await post(`/api/admin/reviews/${b.closest("[data-revid]").dataset.revid}/${b.dataset.rev}`); reload(); } catch (err) { say(err.message); }
});

// ---- карточка человека ----
async function openUserCard(id) {
  try {
    const u = await api(`/api/admin/users/${encodeURIComponent(id)}/card`);
    current = "usercard";
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
    $("#title").textContent = "Карточка " + (u.name || "ID " + u.tg_id);
    const blocked = u.blocked ? `<span class="bad">заблокирован${u.blocked_until ? " до " + esc(fmt(u.blocked_until)) : " бессрочно"}</span>${u.block_reason ? " · " + esc(u.block_reason) : ""}` : '<span class="good">активен</span>';
    $("#main").innerHTML = `<div class="card" data-uid="${esc(u.tg_id)}"><div class="kv">
      <b>ID</b><span>${esc(u.tg_id)}</span><b>Имя</b><span>${esc(u.name || "—")}</span><b>Город</b><span>${esc(u.city)}</span><b>Виза</b><span>${esc(u.visa || "—")}</span>
      <b>Статус</b><span>${blocked}${u.deleted ? " · аккаунт удалён" : ""}${u.trusted ? " · ✔ проверенный автор" : ""}${u.employer_status === "approved" ? " · работодатель " + esc(u.company) : ""}</span>
      <b>Регистрация</b><span>${esc(fmt(u.created_at))}</span><b>Был в сети</b><span>${esc(fmt(u.last_seen_at) || "—")}</span><b>Согласие с правилами</b><span>${u.consent ? "да" : "нет"}</span>
      <b>Объявлений</b><span>${u.listings.length}</span><b>Откликов</b><span>${u.applications}</span><b>Чатов</b><span>${u.chats}</span><b>В избранном</b><span>${u.favorites}</span>
      <b>Жалоб на него / от него</b><span>${u.reports_against} / ${u.reports_made}</span><b>Отзывов получено / написано</b><span>${u.reviews_got} / ${u.reviews_written}</span>
      <b>Заплатил</b><span>${u.stars_paid} ⭐</span><b>Резюме</b><span>${u.has_resume ? "есть" : "нет"}</span></div>
      <div style="margin-top:10px">${u.blocked ? '<button class="act ok" data-ux="unblock">Разблокировать</button>' : '<button class="act no" data-ux="block">Заблокировать бессрочно</button><button class="act warn" data-ux="blockfor">Заблокировать на срок</button>'}
        <button class="act gray" data-ux="${u.trusted ? "untrust" : "trust"}">${u.trusted ? "Снять «проверенный автор»" : "Отметить «проверенный автор»"}</button></div></div>
      <div class="card"><h3>Заметки сотрудников</h3>${u.notes.map((n) => `<div class="meta"><b>${esc(n.author)}</b> ${esc(fmt(n.at))} <button class="act gray" data-delnote="${n.id}">убрать</button></div><p>${esc(n.text)}</p>`).join("") || '<p class="muted">Заметок нет.</p>'}
        <textarea id="noteText" rows="2" maxlength="500" placeholder="Например: предупреждён о телефонах в описании"></textarea><button class="act ok" id="noteAdd">Добавить заметку</button></div>
      <div class="card"><h3>Объявления</h3>${u.listings.map((x) => `<div>#${x.id} ${esc(x.title)} <span class="badge">${esc(STATUS_RU[x.status] || x.status)}</span> <button class="act gray" data-gl="${x.id}">Открыть</button><div data-gbox></div></div>`).join("") || '<p class="muted">Нет.</p>'}</div>
      <div class="card"><h3>Обращения в поддержку</h3>${u.tickets.map((t) => `<div>#${t.id} · ${esc(t.kind)} · ${esc(t.status)}</div>`).join("") || '<p class="muted">Нет.</p>'}</div>`;
    say("");
  } catch (err) { say(err.message); }
}
$("#main").addEventListener("click", async (e) => {
  const holder = document.querySelector('#main > .card[data-uid]');
  if (e.target.id === "noteAdd" && holder) {
    try { await post(`/api/admin/users/${holder.dataset.uid}/notes`, {text: $("#noteText").value.trim()}); openUserCard(holder.dataset.uid); } catch (err) { say(err.message); }
  }
  const dn = e.target.closest("[data-delnote]");
  if (dn && holder) { try { await api(`/api/admin/notes/${dn.dataset.delnote}`, {method: "DELETE"}); openUserCard(holder.dataset.uid); } catch (err) { say(err.message); } }
});

// ---- пользователи ----
async function loadUsers() {
  $("#main").innerHTML = `<div class="toolbar"><input id="userQuery" placeholder="ID пользователя"><button class="act" id="userSearch">Найти</button></div><div id="usersList"></div>`;
  const run = async () => {
    const rows = await api("/api/admin/users?q=" + encodeURIComponent($("#userQuery").value.trim()));
    $("#usersList").innerHTML = rows.length ? rows.map((u) => `
      <div class="card" data-uid="${esc(u.tg_id)}"><b>ID ${esc(u.tg_id)}</b> · ${esc(u.city)} · согласие: ${u.consent ? "да" : "нет"}${u.trusted ? " · ✔ проверенный автор" : ""}
        ${u.deleted ? " · аккаунт удалён" : ""}${u.blocked ? " · <b>заблокирован</b>" : ""}${u.block_reason ? `<div class="meta">Причина: ${esc(u.block_reason)}</div>` : ""}
        <div data-act-box></div>
        <button class="act gray" data-ux="card">Карточка</button>
        <button class="act gray" data-ux="activity">Активность</button>
        <button class="act gray" data-ux="${u.trusted ? "untrust" : "trust"}">${u.trusted ? "Снять отметку «проверен»" : "Отметить «проверенный автор»"}</button>
        ${u.blocked ? '<button class="act ok" data-ux="unblock">Разблокировать</button>' : '<button class="act no" data-ux="block">Заблокировать</button>'}</div>`).join("")
      : '<div class="card muted">Никого не нашли.</div>';
  };
  $("#userSearch").onclick = () => run().catch((e) => say(e.message));
  await run();
}
$("#main").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ux]");
  if (!btn) return;
  const card = btn.closest("[data-uid]");
  const id = card.dataset.uid;
  try {
    if (btn.dataset.ux === "card") { openUserCard(id); return; }
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
  const row = (g = {}) => `<tr data-gid="${g.id || ""}"><td><input data-f="flag" value="${esc(g.flag || "")}" style="width:50px"></td>
    <td><input data-f="name" value="${esc(g.name || "")}" placeholder="Название"></td>
    <td><input data-f="telegram_link" value="${esc(g.telegram_link || "")}" placeholder="https://t.me/…"></td>
    <td><input data-f="members_count" type="number" min="0" value="${g.members_count || 0}" style="width:90px"></td>
    <td><select data-f="kind"><option value="nation" ${g.kind !== "city" ? "selected" : ""}>национальность</option><option value="city" ${g.kind === "city" ? "selected" : ""}>город</option></select></td>
    <td><input data-f="description" value="${esc(g.description || "")}" placeholder="Описание"></td>
    <td><button class="act" data-gsave>${g.id ? "Сохранить" : "Добавить"}</button>${g.id ? '<button class="act no" data-gdel>Удалить</button>' : ""}</td></tr>`;
  $("#main").innerHTML = `<p class="muted">Ссылки только на группы в Telegram (https://t.me/…). Группа без ссылки в приложении показана серой.</p>
    <table><tr><th>Флаг</th><th>Название</th><th>Ссылка</th><th>Участников</th><th>Раздел</th><th>Описание</th><th></th></tr>${rows.map(row).join("")}${row()}</table>`;
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
  const [banners, casts, geo] = await Promise.all([api("/api/admin/banners"), api("/api/admin/broadcasts"), fetch("/api/geo").then((r) => r.json()).then((g) => g.regions)]);
  $("#main").innerHTML = `
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
    const c = e.target.closest("[data-cid]");
    if (c && e.target.closest("[data-csend]")) {
      if (!confirm("Отправить рассылку всем получателям?")) return;
      await post(`/api/admin/broadcasts/${c.dataset.cid}/send`); reload();
    }
    if (c && e.target.closest("[data-ccancel]")) { await post(`/api/admin/broadcasts/${c.dataset.cid}/cancel`); reload(); }
  } catch (err) { say(err.message); }
});

// ---- платежи и промокоды ----
async function loadPayments() {
  const [rows, promos, an] = await Promise.all([api("/api/admin/payments"), api("/api/admin/promos"), api("/api/admin/analytics?days=30")]);
  const sold = an.days.filter((d) => d.payments).reverse();
  $("#main").innerHTML = `
    <div class="card"><h3>Заработано звёзд за 30 дней: ${an.totals.stars} ⭐</h3>
      ${sold.length ? `<table><tr><th>День</th><th>Продаж</th><th>⭐</th></tr>${sold.map((d) => `<tr><td>${esc(d.date)}</td><td>${d.payments}</td><td>${d.stars}</td></tr>`).join("")}</table>` : '<p class="muted">Продаж не было.</p>'}
      ${an.stars_by_service.length ? `<h3 style="margin-top:12px">По услугам</h3><table><tr><th>Услуга</th><th>⭐</th></tr>${an.stars_by_service.map((x) => `<tr><td>${esc(x.service)}</td><td>${x.stars}</td></tr>`).join("")}</table>` : ""}</div>
    <div class="card"><h3>Промокоды</h3><div class="row"><input id="pCode" placeholder="КОД" maxlength="24" style="width:130px;text-transform:uppercase">
      <input id="pPercent" type="number" min="1" max="90" placeholder="% скидки" style="width:100px"><input id="pFixed" type="number" min="1" placeholder="или звёзд" style="width:100px">
      <input id="pMax" type="number" min="1" placeholder="лимит" style="width:90px"><input id="pDays" type="number" min="1" placeholder="дней" style="width:80px"><button class="act" id="pAdd">Создать</button></div>
      <table><tr><th>Код</th><th>Скидка</th><th>Использовано</th><th>До</th><th></th></tr>${promos.map((p) => `<tr data-pid="${p.id}"><td>${esc(p.code)}</td>
      <td>${p.percent ? "−" + p.percent + "%" : "−" + p.fixed_stars + " ⭐"}</td><td>${p.used}${p.max_uses ? "/" + p.max_uses : ""}</td><td>${esc(fmt(p.valid_until) || "без срока")}</td>
      <td><button class="act ${p.active ? "gray" : "ok"}" data-ptoggle>${p.active ? "Выключить" : "Включить"}</button></td></tr>`).join("")}</table></div>
    ${rows.length ? `<table><tr><th>№</th><th>Пользователь</th><th>Объявление</th><th>Услуга</th><th>⭐</th><th>Статус</th><th></th></tr>
    ${rows.map((p) => `<tr data-pay="${p.id}"><td>${p.id}</td><td>${esc(p.user)}</td><td>#${p.listing_id}</td><td>${esc(p.service)}</td><td>${p.stars}</td><td>${esc(p.status)}</td>
      <td>${p.status === "paid" ? '<button class="act no" data-refund>Вернуть</button>' : ""}</td></tr>`).join("")}</table>` : '<div class="card muted">Платежей пока нет.</div>'}`;
  $("#pAdd").onclick = async () => {
    const num = (id) => ($(id).value ? Number($(id).value) : null);
    try { await post("/api/admin/promos", {code: $("#pCode").value.trim(), percent: num("#pPercent"), fixed_stars: num("#pFixed"), max_uses: num("#pMax"), days: num("#pDays")}); reload(); }
    catch (err) { say(err.message); }
  };
}
$("#main").addEventListener("click", async (e) => {
  try {
    const t = e.target.closest("[data-ptoggle]");
    if (t) { await api(`/api/admin/promos/${t.closest("[data-pid]").dataset.pid}/toggle`, {method: "PUT"}); reload(); }
    const btn = e.target.closest("[data-refund]");
    if (!btn) return;
    const id = btn.closest("[data-pay]").dataset.pay;
    if (!confirm("Вернуть оплату " + id + "? Звёзды вернутся пользователю, услуга отключится.")) return;
    await post(`/api/admin/payments/${id}/refund`); reload();
  } catch (err) { say(err.message); }
});

// ---- цены ----
async function loadPricing() {
  const rows = await api("/api/admin/pricing");
  $("#main").innerHTML = `<table><tr><th>Услуга</th><th>Раздел</th><th>⭐</th><th>Дней</th><th>Активна</th><th></th></tr>
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
  const rows = await api("/api/admin/client-errors");
  $("#main").innerHTML = rows.length ? `<p class="muted">Приложение само присылает сюда ошибки. «Код» совпадает с тем, что человек видит на экране.</p>
    <table><tr><th>Время</th><th>Пользователь</th><th>Где</th><th>Статус</th><th>Сообщение</th><th>Код</th><th>Телефон</th></tr>
    ${rows.map((r) => `<tr><td>${esc(fmt(r.at))}</td><td>${esc(r.user || "гость")}</td><td>${esc(r.where)}</td><td>${r.status || ""}</td><td>${esc(r.message)}</td><td>${esc(r.request_id)}</td><td>${esc(r.agent)}</td></tr>`).join("")}</table>`
    : '<div class="card muted">Ошибок пока нет.</div>';
}
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
  const ROLES = ["support", "moderator", "admin", "super_admin"];
  $("#main").innerHTML = `<p class="muted">Роли: support — жалобы, пользователи и блокировки; moderator — + модерация объявлений, анкет и работодателей;
    admin — + цены, платежи, контент, рассылки, статистика, журнал; super_admin — + сотрудники.</p>
    <table><tr><th>Telegram ID</th><th>Роль</th><th>Доступ</th><th></th></tr>
    ${rows.map((a) => `<tr data-staff="${esc(a.tg_id)}"><td>${esc(a.tg_id)}</td><td><select data-role>${ROLES.map((r) => `<option value="${r}" ${r === a.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td><input type="checkbox" data-active ${a.active ? "checked" : ""}></td><td><button class="act" data-staff-save>Сохранить</button> <button class="act gray" data-staff-out>Выйти со всех устройств</button></td></tr>`).join("")}</table>
    <div class="card"><h3>Работа сотрудников за 30 дней</h3><div id="staffDetail" class="muted">Загрузка…</div></div>
    <div class="card"><h3>Входы и действия с доступом</h3><div id="staffLogins" class="muted">Загрузка…</div></div>
    <p><input id="newStaffId" inputmode="numeric" placeholder="Telegram ID сотрудника"><select id="newStaffRole">${ROLES.map((r) => `<option>${r}</option>`).join("")}</select>
    <button class="act" id="addStaff">Добавить</button></p>`;
  fillStaffExtras();
}
$("#main").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-staff]");
  try {
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

const LOADERS = {dash: loadDash, listings: loadListings, jobs: loadSection, housing: loadSection, market: loadSection, dating: loadDating,
                 employers: loadEmployers, reports: loadReports, reviews: loadReviews, resumes: loadResumes, support: loadSupport, texts: loadTexts, users: loadUsers, community: loadCommunity, guide: loadGuide,
                 marketing: loadMarketing, payments: loadPayments, pricing: loadPricing, audit: loadAudit, errors: loadErrors, settings: loadSettings, admins: loadAdmins};

// Старт: есть сессия — открываем; открыто из Telegram — входим сами; иначе показываем кнопку
if (session()) startApp();
else if (tgApp?.initData) autoLogin();
