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
  {key: "reports", label: "🚩 Жалобы", roles: SUPPORT, title: "Жалобы"},
  {key: "users", label: "👥 Пользователи", roles: SUPPORT, title: "Пользователи"},
  {sep: "Контент", roles: ADM},
  {key: "community", label: "🌍 Сообщество", roles: ADM, title: "Группы сообщества"},
  {key: "guide", label: "📘 Гид", roles: ADM, title: "Статьи гида"},
  {key: "marketing", label: "📣 Рассылки и баннеры", roles: ADM, title: "Рассылки и баннеры"},
  {sep: "Деньги", roles: ADM},
  {key: "payments", label: "💳 Платежи", roles: ADM, title: "Платежи и промокоды"},
  {key: "pricing", label: "💰 Цены", roles: ADM, title: "Цены услуг"},
  {sep: "Система", roles: ADM},
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

async function refreshBadges() {
  try {
    const q = MOD.includes(role()) ? (await api("/api/admin/listings")).length : 0;
    const rp = (await api("/api/admin/reports?status=new")).length;
    const set = (k, n) => { const el = document.querySelector(`[data-badge="${k}"]`); if (el) { el.textContent = n; el.hidden = !n; } };
    set("listings", q); set("reports", rp);
  } catch (e) { /* счётчики не обязательны */ }
}

function show(tab) {
  const def = TABS.find((t) => t.key === tab);
  current = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("#title").textContent = def.title;
  say("Загрузка…");
  $("#main").innerHTML = "";
  LOADERS[tab](def).then(() => { say("Обновлено " + new Date().toLocaleTimeString("ru-RU")); refreshBadges(); }).catch((e) => say(e.message));
}
const reload = () => show(current);

// ---- главная: показатели, график, воронка ----
function bars(days, key) {
  const max = Math.max(1, ...days.map((d) => d[key]));
  return `<div class="bars">${days.map((d) => `<i class="${d[key] ? "" : "zero"}" style="height:${Math.max(3, Math.round(d[key] / max * 100))}%" title="${esc(d.date)}: ${d[key]}"></i>`).join("")}</div>`;
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
    <p class="muted">Контакт: ${esc(x.contact || "—")} · Автор: ${esc(x.author_id || "—")} · просмотров: ${x.views || 0}${x.note ? " · комментарий: " + esc(x.note) : ""}</p>
    ${buttons}</div>`;
}
function reasonFrom(card, act) {
  const key = card.querySelector("[data-template]")?.value;
  if (key) return TEMPLATES.find((t) => t.key === key)?.text || "";
  return ask(act === "reject" ? "Причина отказа (автор её увидит):" : act === "hide" ? "Причина, почему скрыть (автор её увидит):" : "Что исправить (автор её увидит):");
}
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
  try { await post(`/api/admin/dating/${btn.closest("[data-pid]").dataset.pid}/${btn.dataset.dec}`); reload(); } catch (err) { say(err.message); }
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
  $("#main").innerHTML = rows.length ? rows.map((r) => `
    <div class="card" data-rid="${r.id}"><b>Жалоба #${r.id}</b> · ${esc(r.target_type)} ${esc(r.target_id)} · причина: ${esc(r.reason)}
      <div class="meta">${esc(r.created_at)}</div><p>${esc(r.comment || "")}</p><div data-ctx></div>
      <button class="act gray" data-rep="context">Показать контекст</button>
      <button class="act ok" data-rep="resolve">Принять (скрыть / заблокировать)</button>
      <button class="act no" data-rep="reject">Отклонить жалобу</button></div>`).join("") : '<div class="card muted">Новых жалоб нет.</div>';
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

// ---- пользователи ----
async function loadUsers() {
  $("#main").innerHTML = `<div class="toolbar"><input id="userQuery" placeholder="ID пользователя"><button class="act" id="userSearch">Найти</button></div><div id="usersList"></div>`;
  const run = async () => {
    const rows = await api("/api/admin/users?q=" + encodeURIComponent($("#userQuery").value.trim()));
    $("#usersList").innerHTML = rows.length ? rows.map((u) => `
      <div class="card" data-uid="${esc(u.tg_id)}"><b>ID ${esc(u.tg_id)}</b> · ${esc(u.city)} · согласие: ${u.consent ? "да" : "нет"}
        ${u.deleted ? " · аккаунт удалён" : ""}${u.blocked ? " · <b>заблокирован</b>" : ""}${u.block_reason ? `<div class="meta">Причина: ${esc(u.block_reason)}</div>` : ""}
        <div data-act-box></div>
        <button class="act gray" data-ux="activity">Активность</button>
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
    if (btn.dataset.ux === "activity") {
      const a = await api(`/api/admin/users/${id}/activity`);
      const fmtObj = (o) => Object.entries(o).map(([k, v]) => `${esc(STATUS_RU[k] || k)}: ${v}`).join(", ") || "нет";
      card.querySelector("[data-act-box]").innerHTML = `<div class="meta">Объявления: ${fmtObj(a.listings)}<br>Отклики: ${a.applications_total} (${fmtObj(a.applications)})<br>
        Жалоб на него: ${a.reports_against} · жалоб подано: ${a.reports_made}<br>Следит за поисками: ${a.saved_searches.length ? a.saved_searches.map((s) => esc(s.name) + " " + esc(JSON.stringify(s.filters))).join("; ") : "нет"}</div>`;
      return;
    }
    const reason = ask(btn.dataset.ux === "block" ? "Причина блокировки (пользователь её не увидит):" : "Причина разблокировки:");
    if (!reason) return;
    await post(`/api/admin/users/${id}/${btn.dataset.ux}`, {reason});
    reload();
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
      <button class="act" id="cMake">Создать черновик</button></div>
      <table><tr><th>№</th><th>Текст</th><th>Кому</th><th>Статус</th><th>Отправлено</th><th></th></tr>${casts.map((c) => `<tr data-cid="${c.id}"><td>${c.id}</td><td>${esc(c.text.slice(0, 70))}</td><td>${esc(c.region_code || "все")}</td>
        <td>${esc(c.status)}</td><td>${c.sent}/${c.total}${c.failed ? ` (ошибок ${c.failed})` : ""}</td><td>
        ${c.status === "draft" ? '<button class="act ok" data-csend>Отправить</button>' : ""}${["draft", "sending"].includes(c.status) ? '<button class="act no" data-ccancel>Остановить</button>' : ""}</td></tr>`).join("")}</table></div>`;
  $("#bAdd").onclick = async () => {
    try { await post("/api/admin/banners", {text: $("#bText").value.trim(), link: $("#bLink").value.trim(), days: $("#bDays").value ? Number($("#bDays").value) : null}); reload(); }
    catch (err) { say(err.message); }
  };
  $("#cMake").onclick = async () => {
    try { const c = await post("/api/admin/broadcasts", {text: $("#cText").value.trim(), region_code: $("#cRegion").value || null}); say(`Черновик создан: получателей ${c.total}`); reload(); }
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
async function loadAudit() {
  const rows = await api("/api/admin/audit?limit=200");
  $("#main").innerHTML = rows.length ? `<table><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Детали</th></tr>
    ${rows.map((r) => `<tr><td>${esc(fmt(r.created_at))}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td>${esc(r.object_type)} ${esc(r.object_id)}</td><td>${esc(r.details)}</td></tr>`).join("")}</table>`
    : '<div class="card muted">Журнал пуст.</div>';
}

// ---- выгрузки ----
async function loadSettings() {
  $("#main").innerHTML = `<div class="card"><h3>Выгрузка в таблицу (CSV)</h3><p class="muted">Файлы открываются в Excel и Google Таблицах. В выгрузке пользователей нет имён и контактов. Каждая выгрузка записывается в журнал.</p>
    ${["users:Пользователи", "payments:Платежи", "listings:Объявления"].map((x) => { const [k, v] = x.split(":"); return `<button class="act" data-export="${k}">⬇ ${v}</button>`; }).join("")}</div>`;
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
async function loadAdmins() {
  const rows = await api("/api/admin/admins");
  const ROLES = ["support", "moderator", "admin", "super_admin"];
  $("#main").innerHTML = `<p class="muted">Роли: support — жалобы, пользователи и блокировки; moderator — + модерация объявлений, анкет и работодателей;
    admin — + цены, платежи, контент, рассылки, статистика, журнал; super_admin — + сотрудники.</p>
    <table><tr><th>Telegram ID</th><th>Роль</th><th>Доступ</th><th></th></tr>
    ${rows.map((a) => `<tr data-staff="${esc(a.tg_id)}"><td>${esc(a.tg_id)}</td><td><select data-role>${ROLES.map((r) => `<option value="${r}" ${r === a.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td><input type="checkbox" data-active ${a.active ? "checked" : ""}></td><td><button class="act" data-staff-save>Сохранить</button></td></tr>`).join("")}</table>
    <p><input id="newStaffId" inputmode="numeric" placeholder="Telegram ID сотрудника"><select id="newStaffRole">${ROLES.map((r) => `<option>${r}</option>`).join("")}</select>
    <button class="act" id="addStaff">Добавить</button></p>`;
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
                 employers: loadEmployers, reports: loadReports, users: loadUsers, community: loadCommunity, guide: loadGuide,
                 marketing: loadMarketing, payments: loadPayments, pricing: loadPricing, audit: loadAudit, settings: loadSettings, admins: loadAdmins};

// Старт: есть сессия — открываем; открыто из Telegram — входим сами; иначе показываем кнопку
if (session()) startApp();
else if (tgApp?.initData) autoLogin();
