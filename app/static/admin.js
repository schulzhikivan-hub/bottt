const $ = (s) => document.querySelector(s);
const esc = (s = "") => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const CAT = {job: "Работа", housing: "Жильё", marketplace: "Барахолка"};
const REASON_HINT = "Укажите причину (не короче 3 символов):";

function session() { return sessionStorage.getItem("adminSession") || ""; }

async function api(path, options = {}) {
  const headers = {"X-Admin-Session": session()};
  if (options.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {...options, headers});
  if (r.status === 401) { logout(); throw new Error("Сессия истекла. Войдите снова"); }
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) throw new Error(data?.detail || ("Ошибка сервера (" + r.status + ")"));
  return data;
}

function say(text) { $("#status").textContent = text; }
function ask(question) {
  const value = prompt(question, "");
  if (value === null) return null;
  if (value.trim().length < 3) { say("Причина должна быть не короче 3 символов"); return null; }
  return value.trim();
}

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
  // Панель открыта кнопкой в боте: Telegram сам подтверждает, кто вы
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

function startApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#logout").hidden = false;
  $("#who").textContent = (sessionStorage.getItem("adminRole") || "") + " · ID " + (sessionStorage.getItem("adminId") || "");
  if (sessionStorage.getItem("adminRole") === "super_admin") $("#tabAdmins").hidden = false;
  show("dash");
}
async function logout() {
  try { await fetch("/api/admin/auth/logout", {method: "POST", headers: {"X-Admin-Session": session()}}); } catch (e) {}
  sessionStorage.clear();
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#logout").hidden = true;
  $("#tabAdmins").hidden = true;
  $("#webLoginBtn").hidden = false;
}
$("#logout").onclick = logout;

// ---- вкладки ----
const LOADERS = {dash: loadDash, listings: loadListings, dating: loadDating, reports: loadReports,
                 users: loadUsers, employers: loadEmployers, payments: loadPayments,
                 pricing: loadPricing, audit: loadAudit, admins: loadAdmins};
function show(tab) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll("main section").forEach((s) => s.classList.toggle("hidden", s.id !== "tab-" + tab));
  say("Загрузка…");
  LOADERS[tab]().then(() => say("Обновлено " + new Date().toLocaleTimeString("ru-RU")))
    .catch((e) => say(e.message));
}
document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => show(b.dataset.tab));

// ---- дашборд ----
async function loadDash() {
  const s = await api("/api/admin/stats");
  const listings = Object.entries(s.listings || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "нет";
  $("#tab-dash").innerHTML = `
    <div class="stats">
      <div><b>${s.users}</b>пользователей</div>
      <div><b>${s.reports_new}</b>новых жалоб</div>
      <div><b>${s.payers}</b>платящих</div>
      <div><b>${s.revenue_stars_total} ⭐</b>выручка всего</div>
      <div><b>${s.day.stars} ⭐</b>за сутки (${s.day.sales} продаж)</div>
      <div><b>${s.week.stars} ⭐</b>за неделю (${s.week.sales})</div>
      <div><b>${s.month.stars} ⭐</b>за месяц (${s.month.sales})</div>
    </div>
    <div class="card"><b>Объявления по статусам:</b> ${esc(listings)}</div>
    <div class="card"><b>Популярные услуги:</b><br>${(s.top_services || []).map(([code, n]) => `${esc(code)} — ${n}`).join("<br>") || "пока нет продаж"}</div>`;
}

// ---- модерация объявлений ----
async function loadListings() {
  const rows = await api("/api/admin/listings");
  $("#tab-listings").innerHTML = rows.length ? rows.map((x) => `
    <div class="card" data-id="${x.id}">
      <b>#${x.id} ${esc(x.title)}</b>
      <div class="meta">${esc(CAT[x.category] || x.category)} · ${esc(x.city)} · ${esc(x.price || "цена не указана")}</div>
      <p>${esc(x.description || "")}</p>
      <p class="muted">Контакт: ${esc(x.contact || "—")}</p>
      <button class="act ok" data-act="approve">Одобрить</button>
      <button class="act warn" data-act="needs_fix">Вернуть на доработку</button>
      <button class="act no" data-act="reject">Отклонить</button>
    </div>`).join("") : '<div class="card muted">Очередь модерации пуста.</div>';
}
$("#tab-listings").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const id = btn.closest("[data-id]").dataset.id;
  const act = btn.dataset.act;
  let url = `/api/admin/listings/${id}/${act}`;
  if (act !== "approve") {
    const reason = ask(act === "reject" ? "Причина отказа (автор её увидит):" : "Что исправить (автор её увидит):");
    if (!reason) return;
    url += "?reason=" + encodeURIComponent(reason);
  }
  try { await api(url, {method: "POST"}); loadListings(); } catch (err) { say(err.message); }
});

// ---- знакомства ----
async function loadDating() {
  const rows = await api("/api/admin/dating?status=pending");
  const box = $("#tab-dating");
  box.innerHTML = rows.length ? rows.map((p) => `
    <div class="card" data-pid="${p.id}">
      <b>Анкета #${p.id}</b> · ${p.age} лет · ${p.gender === "f" ? "ж" : "м"} · ${esc(p.city)}
      <p>${esc(p.bio)}</p>
      <img data-photo="${p.id}" alt="" style="max-width:200px;border-radius:10px" ${p.has_photo ? "" : "hidden"}>
      <p><button class="act ok" data-dec="approve">Опубликовать</button>
      <button class="act no" data-dec="reject">Отклонить</button></p>
    </div>`).join("") : '<div class="card muted">Анкет на проверке нет.</div>';
  for (const img of box.querySelectorAll("img[data-photo]")) {
    if (img.hidden) continue;
    const r = await fetch(`/api/admin/dating/${img.dataset.photo}/photo`, {headers: {"X-Admin-Session": session()}});
    if (r.ok) img.src = URL.createObjectURL(await r.blob());
  }
}
$("#tab-dating").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-dec]");
  if (!btn) return;
  const id = btn.closest("[data-pid]").dataset.pid;
  try { await api(`/api/admin/dating/${id}/${btn.dataset.dec}`, {method: "POST"}); loadDating(); }
  catch (err) { say(err.message); }
});

// ---- жалобы ----
async function loadReports() {
  const rows = await api("/api/admin/reports?status=new");
  $("#tab-reports").innerHTML = rows.length ? rows.map((r) => `
    <div class="card" data-rid="${r.id}">
      <b>Жалоба #${r.id}</b> · ${esc(r.target_type)} ${esc(r.target_id)} · причина: ${esc(r.reason)}
      <div class="meta">${esc(r.created_at)}</div>
      <p>${esc(r.comment || "")}</p>
      <button class="act ok" data-rep="resolve">Принять (скрыть / заблокировать)</button>
      <button class="act gray" data-rep="reject">Отклонить жалобу</button>
    </div>`).join("") : '<div class="card muted">Новых жалоб нет.</div>';
}
$("#tab-reports").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-rep]");
  if (!btn) return;
  const id = btn.closest("[data-rid]").dataset.rid;
  try { await api(`/api/admin/reports/${id}/${btn.dataset.rep}`, {method: "POST"}); loadReports(); }
  catch (err) { say(err.message); }
});

// ---- пользователи ----
async function loadUsers() {
  const q = $("#userQuery").value.trim();
  const rows = await api("/api/admin/users?q=" + encodeURIComponent(q));
  $("#usersList").innerHTML = rows.length ? rows.map((u) => `
    <div class="card" data-uid="${esc(u.tg_id)}">
      <b>ID ${esc(u.tg_id)}</b> · ${esc(u.city)} · согласие: ${u.consent ? "да" : "нет"}
      ${u.deleted ? " · аккаунт удалён" : ""}${u.blocked ? " · <b>заблокирован</b>" : ""}
      ${u.block_reason ? `<div class="meta">Причина: ${esc(u.block_reason)}</div>` : ""}
      <p>${u.blocked
        ? '<button class="act ok" data-ux="unblock">Разблокировать</button>'
        : '<button class="act no" data-ux="block">Заблокировать</button>'}</p>
    </div>`).join("") : '<div class="card muted">Никого не нашли.</div>';
}
$("#userSearch").onclick = () => loadUsers().catch((e) => say(e.message));
$("#usersList").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ux]");
  if (!btn) return;
  const id = btn.closest("[data-uid]").dataset.uid;
  const reason = ask(btn.dataset.ux === "block" ? "Причина блокировки (пользователь её не увидит):" : "Причина разблокировки:");
  if (!reason) return;
  try { await api(`/api/admin/users/${id}/${btn.dataset.ux}`, {method: "POST", body: JSON.stringify({reason})}); loadUsers(); }
  catch (err) { say(err.message); }
});

// ---- работодатели ----
async function loadEmployers() {
  const rows = await api("/api/admin/employers?status=pending");
  $("#tab-employers").innerHTML = rows.length ? rows.map((u) => `
    <div class="card" data-eid="${esc(u.tg_id)}">
      <b>${esc(u.company_name)}</b> · рег. номер ${esc(u.registration_no)}
      <div class="meta">Контакт: ${esc(u.contact_person)} · ID ${esc(u.tg_id)}</div>
      <button class="act ok" data-emp="approve">Подтвердить</button>
      <button class="act no" data-emp="reject">Отклонить</button>
    </div>`).join("") : '<div class="card muted">Заявок на проверке нет.</div>';
}
$("#tab-employers").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-emp]");
  if (!btn) return;
  const id = btn.closest("[data-eid]").dataset.eid;
  try { await api(`/api/admin/employers/${id}/${btn.dataset.emp}`, {method: "POST"}); loadEmployers(); }
  catch (err) { say(err.message); }
});

// ---- платежи ----
async function loadPayments() {
  const rows = await api("/api/admin/payments");
  $("#tab-payments").innerHTML = rows.length ? `<table>
    <tr><th>№</th><th>Пользователь</th><th>Объявление</th><th>Услуга</th><th>⭐</th><th>Статус</th><th></th></tr>
    ${rows.map((p) => `<tr data-pay="${p.id}">
      <td>${p.id}</td><td>${esc(p.user)}</td><td>#${p.listing_id}</td><td>${esc(p.service)}</td>
      <td>${p.stars}</td><td>${esc(p.status)}</td>
      <td>${p.status === "paid" ? '<button class="act no" data-refund>Вернуть</button>' : ""}</td></tr>`).join("")}
    </table>` : '<div class="card muted">Платежей пока нет.</div>';
}
$("#tab-payments").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-refund]");
  if (!btn) return;
  const id = btn.closest("[data-pay]").dataset.pay;
  if (!confirm("Вернуть оплату " + id + "? Звёзды вернутся пользователю, услуга отключится.")) return;
  try { await api(`/api/admin/payments/${id}/refund`, {method: "POST"}); loadPayments(); }
  catch (err) { say(err.message); }
});

// ---- цены ----
async function loadPricing() {
  const rows = await api("/api/admin/pricing");
  $("#tab-pricing").innerHTML = `<table>
    <tr><th>Услуга</th><th>Раздел</th><th>⭐</th><th>Дней</th><th>Активна</th><th></th></tr>
    ${rows.map((p) => `<tr data-code="${esc(p.code)}">
      <td>${esc(p.title)}</td><td>${esc(CAT[p.category] || p.category)}</td>
      <td><input type="number" min="1" max="10000" value="${p.stars}" data-stars style="width:90px"></td>
      <td>${p.duration_days}</td>
      <td><input type="checkbox" data-active ${p.is_active ? "checked" : ""}></td>
      <td><button class="act" data-save>Сохранить</button></td></tr>`).join("")}
    </table>`;
}
$("#tab-pricing").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-save]");
  if (!btn) return;
  const row = btn.closest("[data-code]");
  const body = {stars: Number(row.querySelector("[data-stars]").value), is_active: row.querySelector("[data-active]").checked};
  try {
    await api(`/api/admin/pricing/${row.dataset.code}`, {method: "PUT", body: JSON.stringify(body)});
    say("Цена сохранена (запись в журнале)");
  } catch (err) { say(err.message); }
});

// ---- журнал ----
async function loadAudit() {
  const rows = await api("/api/admin/audit?limit=200");
  $("#tab-audit").innerHTML = rows.length ? `<table>
    <tr><th>Время</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Детали</th></tr>
    ${rows.map((r) => `<tr><td>${esc(r.created_at.slice(0, 16))}</td><td>${esc(r.actor)}</td>
      <td>${esc(r.action)}</td><td>${esc(r.object_type)} ${esc(r.object_id)}</td><td>${esc(r.details)}</td></tr>`).join("")}
    </table>` : '<div class="card muted">Журнал пуст.</div>';
}

// Сотрудники (только суперадминистратор)
async function loadAdmins() {
  const rows = await api("/api/admin/admins");
  $("#tab-admins").innerHTML = `
    <p class="muted">Роли: moderator — модерация; admin — + цены, платежи, статистика, журнал; super_admin — + сотрудники.</p>
    <table><tr><th>Telegram ID</th><th>Роль</th><th>Доступ</th><th></th></tr>
    ${rows.map((a) => `<tr data-staff="${esc(a.tg_id)}"><td>${esc(a.tg_id)}</td>
      <td><select data-role>${["moderator", "admin", "super_admin"].map((r) =>
        `<option value="${r}" ${r === a.role ? "selected" : ""}>${r}</option>`).join("")}</select></td>
      <td><input type="checkbox" data-active ${a.active ? "checked" : ""}></td>
      <td><button class="act" data-staff-save>Сохранить</button></td></tr>`).join("")}
    </table>
    <p><input id="newStaffId" inputmode="numeric" placeholder="Telegram ID сотрудника">
    <button class="act" id="addStaff">Добавить / изменить</button></p>`;
}
$("#tab-admins").addEventListener("click", async (e) => {
  const row = e.target.closest("[data-staff]");
  try {
    if (e.target.closest("[data-staff-save]")) {
      await api(`/api/admin/admins/${row.dataset.staff}`, {method: "PUT", body: JSON.stringify({
        role: row.querySelector("[data-role]").value, active: row.querySelector("[data-active]").checked})});
      say("Сохранено (запись в журнале)");
      loadAdmins();
    }
    if (e.target.id === "addStaff") {
      const id = $("#newStaffId").value.trim();
      if (!/^\d{3,20}$/.test(id)) { say("Введите числовой Telegram ID"); return; }
      await api(`/api/admin/admins/${id}`, {method: "PUT", body: JSON.stringify({role: "moderator", active: true})});
      loadAdmins();
    }
  } catch (err) { say(err.message); }
});

// Старт: есть сессия — открываем; открыто из Telegram — входим сами; иначе показываем кнопку
if (session()) startApp();
else if (tgApp?.initData) autoLogin();
