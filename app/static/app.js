const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
try { tg?.disableVerticalSwipes?.(); } catch (e) { /* старая версия Telegram */ }

const $ = (s) => document.querySelector(s);
const esc = (s = "") => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const CAT = {job: "Работа", housing: "Жильё", marketplace: "Барахолка"};
const STATUS = {pending: ["На модерации", "pending"], approved: ["Опубликовано", "approved"],
                rejected: ["Отклонено", "rejected"], needs_fix: ["Нужно исправить", "pending"],
                hidden: ["Скрыто", "rejected"], closed: ["Закрыто", "rejected"], sold: ["Продано", "approved"],
                archived: ["В архиве", "rejected"], draft: ["Черновик", "pending"], expired: ["Срок истёк", "rejected"]};
const REASONS = {fraud: "Мошенничество", fake: "Поддельное объявление", spam: "Спам",
                 offensive: "Оскорбления", inappropriate: "Неприемлемый контент", rights: "Нарушение прав",
                 minor: "Несовершеннолетний", profile: "Жалоба на профиль"};
const SECTIONS = {visas: "Визы и документы", registration: "Регистрация и адрес", medicine: "Медицина",
                  banks: "Банки и мобильная связь", housing: "Жильё", work: "Работа и трудовые права",
                  emergency: "Экстренные ситуации"};
const PROMO = {pin: "📌 Закреплено", vip: "VIP", bump: "⬆ Поднято", highlight: "★ Выделено"};
const NAV_VIEWS = ["home", "saved", "chats", "profile"];
const MAIN_VIEWS = [...NAV_VIEWS, "dating", "community", "guide"];
const ALL_VIEWS = [...MAIN_VIEWS, "detail", "chat", "article", "mine", "sub", "notifs"];

let currentChat = null;
let chatTimer = null;
let lastMessageKey = "";
let olderMessages = [];   // подгруженные по кнопке «Ранее»
let hasOlder = false;
let me = null;
let favIds = new Set();
let ownItems = {};
let datingGender = "";
let currentEditId = null;
let formPhotos = [];   // уже загруженные: {id, thumb, status}
let formFiles = [];    // новые файлы, ещё не отправленные
let removedPhotos = [];
let ownFilter = "all";
let mineAll = [];

// ---------- запросы ----------
// Внутри Telegram вход автоматический (подпись initData). На сайте: сессия после входа через бота.
const inTelegram = Boolean(tg?.initData);
function authHeaders() {
  const headers = {};
  if (inTelegram) headers["X-Telegram-Init-Data"] = tg.initData;
  else if (localStorage.getItem("webSession")) headers["X-Web-Session"] = localStorage.getItem("webSession");
  return headers;
}
// Нет связи: понятное сообщение вместо «Failed to fetch»
const setOffline = (off) => { const b = document.getElementById("offlineBar"); if (b) b.hidden = !off; };
window.addEventListener("offline", () => setOffline(true));
window.addEventListener("online", () => setOffline(false));
if (!navigator.onLine) setTimeout(() => setOffline(true), 0);
async function api(path, options = {}) {
  const headers = authHeaders();
  if (options.body && typeof options.body === "string") headers["Content-Type"] = "application/json";
  let r;
  try { r = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}}); }
  catch (e) { setOffline(true); throw new Error("Нет связи с интернетом. Проверьте подключение и повторите"); }
  setOffline(false);
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (r.status === 401 && !inTelegram) {
    if (localStorage.getItem("webSession")) { localStorage.removeItem("webSession"); applyGuestMode(currentView); }   // сессия истекла
    if (!options.quiet && !GUEST_VIEWS[currentView]) showWebLogin();
  }
  if (!r.ok) {
    const rid = r.headers.get("x-request-id") || "";
    let msg = typeof data?.detail === "string" ? data.detail : "";
    if (!msg && Array.isArray(data?.detail)) msg = "Проверьте поля: " + data.detail.map((d) => d.msg).filter(Boolean).slice(0, 2).join("; ");
    if (r.status >= 500 || !msg) {
      reportError(path, r.status, msg || "Ошибка сервера", rid);
      msg = `Ошибка сервера (${r.status}). Код для поддержки: ${rid || "—"}`;
    }
    throw new Error(msg);
  }
  return data;
}
// Приложение само сообщает серверу об ошибке: скриншоты и пересказ не нужны
const reported = new Set();
function reportError(where, status, message, rid = "") {
  const key = where + status + message;
  if (reported.has(key) || reported.size > 20) return;
  reported.add(key);
  try { fetch("/api/client-log", {method: "POST", headers: {...authHeaders(), "Content-Type": "application/json"},
    body: JSON.stringify({where: String(where).slice(0, 120), status, message: String(message).slice(0, 300), request_id: rid})}).catch(() => {}); } catch (e) { /* не критично */ }
}
window.addEventListener("error", (e) => reportError("js:" + (e.filename || "").split("/").pop() + ":" + e.lineno, 0, e.message || "error"));
window.addEventListener("unhandledrejection", (e) => reportError("promise", 0, String(e.reason?.message || e.reason || "rejected")));

const applyTextSize = () => { document.body.classList.remove("fs1", "fs2"); const n = localStorage.getItem("fs"); if (n) document.body.classList.add("fs" + n); };
applyTextSize();
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.style.display = "none"), 3000);
  tg?.HapticFeedback?.notificationOccurred("success");
}
const fmtDate = (s) => (s ? new Date(s.replace(" ", "T")).toLocaleString("ru-RU") : "");
const fmtKRW = (n) => new Intl.NumberFormat("ru-RU").format(n) + " ₩";

// Своё окно подтверждения: системные окна Telegram бывают заняты или недоступны и тогда дают ошибку
function confirmDialog(message, yes = "Да") {
  return new Promise((resolve) => {
    $("#confirmText").textContent = message;
    $("#confirmYes").textContent = yes;
    const sheet = $("#confirmSheet");
    const done = (v) => { sheet.classList.add("hidden"); resolve(v); };
    $("#confirmYes").onclick = () => done(true);
    $("#confirmNo").onclick = () => done(false);
    sheet.onclick = (e) => { if (e.target === sheet) done(false); };
    sheet.classList.remove("hidden");
    $("#confirmNo").focus();
  });
}

// Окно выбора причины жалобы (системное prompt() в Telegram может не открыться)
function askReason() {
  return new Promise((resolve) => {
    $("#reasonList").innerHTML = Object.entries(REASONS)
      .map(([key, title]) => `<button class="wide" data-reason="${key}">${esc(title)}</button>`).join("");
    const sheet = $("#reasonSheet");
    sheet.classList.remove("hidden");
    const done = (value) => { sheet.classList.add("hidden"); resolve(value); };
    $("#reasonList").onclick = (e) => { const b = e.target.closest("[data-reason]"); if (b) done(b.dataset.reason); };
    $("#closeReason").onclick = () => done(null);
  });
}

// ---------- вход на сайте через Telegram (в Mini App не нужен) ----------
let loginPoll = null;
function showWebLogin() { if (!inTelegram) $("#webLogin").classList.remove("hidden"); }
// Гость (на сайте, без входа): личные разделы показывают один экран «Войти», а не пустой кабинет с меню
const isGuest = () => !inTelegram && !localStorage.getItem("webSession");
const GUEST_VIEWS = {
  profile: ["👤", "Личный кабинет", "Войдите через Telegram, чтобы публиковать объявления, откликаться на вакансии, сохранять избранное и переписываться с авторами."],
  saved: ["♡", "Избранное", "Войдите через Telegram, чтобы сохранять объявления и получать уведомления, если цена снизится."],
  chats: ["💬", "Сообщения", "Войдите через Telegram, чтобы писать авторам объявлений и получать ответы."],
  notifs: ["🔔", "Уведомления", "Войдите через Telegram, чтобы видеть ответы на отклики, решения модератора и новые объявления по вашим поискам."],
};
function applyGuestMode(name) {
  const el = document.getElementById("view-" + name);
  if (!el || !GUEST_VIEWS[name]) return false;
  const guest = isGuest();
  el.classList.toggle("guest-mode", guest);
  let card = el.querySelector(":scope > .guestcard");
  if (guest && !card) {
    const [icon, title, text] = GUEST_VIEWS[name];
    el.insertAdjacentHTML("afterbegin", `<div class="guestcard"><div class="gicon">${icon}</div><h2>${title}</h2><p>${text}</p>
      <button class="submit" data-guest-login type="button">Войти через Telegram</button>
      <p class="muted">Смотреть объявления, вакансии и гид можно и без входа.</p></div>`);
  }
  return guest;
}
document.addEventListener("click", (e) => { if (e.target.closest("[data-guest-login]")) { showWebLogin(); startWebLogin(); } });
async function startWebLogin() {
  const info = $("#webLoginInfo");
  info.textContent = "Готовим вход…";
  try {
    const r = await api("/api/auth/start", {method: "POST", quiet: true});
    const link = $("#webLoginLink");
    link.href = r.url; link.hidden = false;
    window.open(r.url, "_blank");
    info.textContent = "Подтвердите вход в Telegram кнопкой «Это я, войти». Страница обновится сама.";
    clearInterval(loginPoll);
    const started = Date.now();
    loginPoll = setInterval(async () => {
      if (Date.now() - started > 300000) { clearInterval(loginPoll); info.textContent = "Время вышло. Нажмите кнопку ещё раз."; return; }
      try {
        const p = await api(`/api/auth/poll/${r.token}`, {quiet: true});
        if (p.status === "ok") { clearInterval(loginPoll); localStorage.setItem("webSession", p.session); location.reload(); }
        else if (p.status === "rejected") { clearInterval(loginPoll); info.textContent = "Вход отклонён в Telegram."; }
        else if (p.status === "expired") { clearInterval(loginPoll); info.textContent = "Ссылка устарела. Нажмите кнопку ещё раз."; }
      } catch (e) { /* связь пропала на секунду, пробуем снова */ }
    }, 2000);
  } catch (e) { info.textContent = e.message; }
}
$("#webLoginBtn").onclick = startWebLogin;
$("#closeWebLogin").onclick = () => { clearInterval(loginPoll); $("#webLogin").classList.add("hidden"); };
$("#loginTop").onclick = showWebLogin;

async function loadMe() { me = await api("/api/me"); return me; }
async function requireConsent() {
  try { await loadMe(); } catch (e) { return false; }  // без входа api() сам показал окно входа
  if (!me.consent) { $("#consent").classList.remove("hidden"); return false; }
  return true;
}

// ---------- навигация: шапка и нижняя панель не двигаются, прокручивается только #scroll ----------
let currentView = "home";
let backStack = [];
const scrollPos = {};
let homeLoaded = false;

function showView(name, opts = {}) {
  const box = $("#scroll");
  scrollPos[currentView] = box.scrollTop;
  if (opts.push) backStack.push(currentView);
  else if (!opts.back && NAV_VIEWS.includes(name)) backStack = [];
  currentView = name;
  for (const v of ALL_VIEWS) { const el = $("#view-" + v); if (el) el.hidden = v !== name; }
  box.classList.toggle("fill", name === "chat");
  const parent = ["detail", "article", "mine", "sub", "notifs"].includes(name) ? backStack[backStack.length - 1] : name;
  const navName = name === "chat" ? "chats" : (NAV_VIEWS.includes(parent) ? parent : "");
  document.querySelectorAll(".bottom [data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === navName));
  if (name !== "chat") { clearInterval(chatTimer); clearInterval(chatStatusTimer); }
  if (NAV_VIEWS.includes(name) && backStack.length === 0) tg?.BackButton?.hide(); else tg?.BackButton?.show();
  if (name === "home" && !homeLoaded) loadCatalog();
  const guestHere = applyGuestMode(name);
  if (name === "saved" && !guestHere) loadFavorites();
  if (name === "chats" && !guestHere) loadConversations();
  if ((name === "profile" || name === "mine") && !guestHere) loadProfile();
  if (name === "notifs" && !guestHere) loadNotifs();
  if (name === "community") loadGroups();
  if (name === "guide") loadGuide();
  if (name === "dating") loadDating();
  box.scrollTop = opts.restore ? (scrollPos[name] || 0) : 0;
}
function goBack() {
  const open = typeof modalOpen === "function" ? modalOpen() : null;
  if (open) { closeModal(open); return; }
  const previous = backStack.pop() || "home";
  showView(previous, {back: true, restore: true});
}
tg?.BackButton?.onClick(goBack);

// ---------- справочники полей: визы, графики, типы жилья… (приходят с сервера) ----------
let fieldDefs = null;
async function loadFields() {
  if (!fieldDefs) fieldDefs = await api("/api/fields", {quiet: true});
  document.querySelectorAll("select[data-list]").forEach((sel) => {
    sel.innerHTML = `<option value="">${sel.dataset.any || "Не указано"}</option>` +
      (fieldDefs[sel.dataset.list] || []).map((i) => `<option value="${i.value}">${esc(i.label)}</option>`).join("");
  });
  document.querySelectorAll("[data-visas]").forEach((box) => {
    box.innerHTML = fieldDefs.visas.map((v) => `<label class="vchip"><input type="checkbox" value="${v.value}"><span>${v.label}</span></label>`).join("");
  });
  return fieldDefs;
}
const labelOf = (list, value) => (fieldDefs?.[list] || []).find((i) => i.value === value)?.label || value;

// ---------- карточки ----------
const promoBadge = (x) => (x.promo ? `<span class="promo ${x.promo}">${PROMO[x.promo] || ""}</span>` : "") + (x.reserved ? '<span class="reserved-tag">Зарезервировано</span>' : "");
function priceText(x) {
  if (x.price_amount == null) return x.price || "Цена договорная";
  let text = fmtKRW(x.price_amount);
  if (x.category === "job" && x.details?.salary_max) text = new Intl.NumberFormat("ru-RU").format(x.price_amount) + " – " + fmtKRW(x.details.salary_max);
  const suffix = x.category === "job" ? labelOf("salary_period", x.salary_period || "month") : x.category === "housing" ? "в месяц" : "";
  return [text, suffix, x.price].filter(Boolean).join(" ");
}
let ratesCfg = {};
// Приблизительный пересчёт: курс задаёт владелец в настройках сервера, к конкретной цене не привязан
function approxPrice(x) {
  if (x.price_amount == null || !(ratesCfg.rub || ratesCfg.usd)) return "";
  const n = (v) => new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 0}).format(v);
  const parts = [];
  if (ratesCfg.rub) parts.push("≈ " + n(x.price_amount * ratesCfg.rub) + " ₽");
  if (ratesCfg.usd) parts.push("≈ $" + n(x.price_amount * ratesCfg.usd));
  return `<div class="muted">${parts.join(" · ")} (примерно)</div>`;
}
function pills(x) {
  const out = [];
  if (x.category === "job") {
    if (x.housing_provided) out.push("🏠 Жильё");
    if (x.meals_provided) out.push("🍱 Питание");
    if (x.schedule) out.push(labelOf("schedule", x.schedule));
    (x.visas || []).slice(0, 3).forEach((v) => out.push(v));
  }
  if (x.category === "housing") {
    if (x.housing_type) out.push(labelOf("housing_type", x.housing_type));
    if (x.rooms) out.push(`🛏 ${x.rooms} комн.`);
    if (x.area_m2) out.push(`${x.area_m2} м²`);
  }
  if (x.category === "marketplace") {
    if (x.goods_category) out.push(labelOf("goods_category", x.goods_category));
    if (x.condition) out.push(labelOf("condition", x.condition));
  }
  return out.map((t) => `<span class="pill">${esc(t)}</span>`).join("");
}
// Полный список характеристик для страницы объявления
function specs(x) {
  const d = x.details || {};
  const yes = (v, a = "Да", b = "Нет") => (v === true ? a : v === false ? b : null);
  const rows = [];
  if (x.category === "job") {
    rows.push(["Компания", d.company], ["Должность", d.position],
      ["Занятость", x.employment && labelOf("employment", x.employment)], ["График", x.schedule && labelOf("schedule", x.schedule)],
      ["Подходящие визы", (x.visas || []).join(", ")], ["Жильё", yes(x.housing_provided, "Предоставляется", "Нет")],
      ["Питание", yes(x.meals_provided, "Включено", "Нет")], ["Развозка", yes(d.transport)],
      ["Опыт", yes(d.experience, "Нужен", "Не нужен")], ["Мест", d.vacancies], ["Начало работы", d.start_date]);
  } else if (x.category === "housing") {
    rows.push(["Тип", x.housing_type && labelOf("housing_type", x.housing_type)],
      ["Залог", x.deposit != null && fmtKRW(x.deposit)], ["Коммунальные", d.maintenance_fee != null && fmtKRW(d.maintenance_fee)],
      ["Площадь", x.area_m2 && x.area_m2 + " м²"], ["Комнат", x.rooms], ["Этаж", d.floor], ["Свободно с", d.available_from],
      ["Срок договора", d.contract_term], ["Станция метро", d.station],
      ["Животные", yes(x.pets_allowed, "Можно", "Нельзя")], ["Мебель", yes(x.furnished, "Есть", "Нет")]);
    if (x.price_amount != null && x.deposit != null) {
      rows.push(["Въезд", `залог + первый месяц = ${fmtKRW(x.deposit + x.price_amount)}`]);
    }
  } else {
    rows.push(["Категория", x.goods_category && labelOf("goods_category", x.goods_category)],
      ["Состояние", x.condition && labelOf("condition", x.condition)], ["Бренд", d.brand], ["Модель", d.model],
      ["Год выпуска", d.year], ["Пробег", d.mileage_km != null && new Intl.NumberFormat("ru-RU").format(d.mileage_km) + " км"],
      ["Где оказывается", d.service_area], ["Опыт", d.experience_years != null && d.experience_years + " лет"],
      ["Торг", yes(x.negotiable, "Возможен", "Нет")], ["Доставка", yes(d.delivery, "Есть", "Нет")]);
  }
  const html = rows.filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false)
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  return html ? `<dl class="specs">${html}</dl>` : "";
}
const CAT_ICON = {job: "💼", housing: "🏠", marketplace: "🛍️"};
function listingCard(x) {
  const fav = favIds.has(x.id);
  return `<article class="listing hcard ${x.promo === "highlight" ? "hl" : ""}" data-id="${x.id}" data-cat="${x.category}" data-open="${x.id}">
    <div class="thumb">${x.cover ? `<img src="${esc(x.cover)}" alt="" loading="lazy">` : CAT_ICON[x.category] || "📄"}</div>
    <div class="body">
      ${promoBadge(x)}
      <h3>${esc(x.title)}</h3>
      <div class="price">${esc(priceText(x))}</div>
      <div class="meta">📍 ${esc(x.city)} · ${esc(CAT[x.category] || x.category)}</div>
      <div class="pills">${pills(x)}</div>
    </div>
    <button class="fav ${fav ? "on" : ""}" data-act="fav" aria-label="В избранное">${fav ? "♥" : "♡"}</button>
  </article>`;
}

// ---------- каталог: поиск, фильтры, страницы ----------
const DEFAULT_FILTERS = {q: "", category: "", region_code: "", district_id: "", price_min: "", price_max: "", sort: "new",
                         housing_provided: false, meals_provided: false, condition: "", rooms_min: "",
                         visa: "", schedule: "", employment: "", housing_type: "", deposit_max: "", area_min: "",
                         pets_allowed: false, furnished: false, goods_category: "", year_min: "", year_max: "", mileage_max: ""};
const SECTION_FILTERS = ["housing_provided", "meals_provided", "condition", "rooms_min", "visa", "schedule", "employment",
                         "housing_type", "deposit_max", "area_min", "pets_allowed", "furnished", "goods_category", "year_min", "year_max", "mileage_max"];
let filters = {...DEFAULT_FILTERS};
const page = {offset: 0, loading: false, run: 0};

function catalogParams(offset) {
  const p = new URLSearchParams({limit: "20", offset: String(offset)});
  for (const [key, value] of Object.entries(filters)) {
    if (value === "" || value === false || value == null) continue;
    p.set(key === "region_code" ? "region" : key, value === true ? "true" : value);
  }
  return p;
}
function activeFilterCount() {
  return ["region_code", "price_min", "price_max", ...SECTION_FILTERS].filter((k) => filters[k]).length;
}
function updateFilterUi() {
  const n = activeFilterCount();
  $("#filterBadge").hidden = n === 0;
  $("#filterBadge").textContent = n;
  document.querySelectorAll("#chips .chip").forEach((c) => c.classList.toggle("active", c.dataset.cat === filters.category));
  $("#sortSelect").value = filters.sort;
  // Без поиска и фильтров показываем «витрину»: баннер и плитки разделов
  $("#view-home").classList.toggle("landing", !filters.q && !filters.category && n === 0);
  renderCategoryHead();
}
const PILLS = {
  job: [["Город", "region_code"], ["Зарплата", "price_min"], ["График", "schedule"], ["Жильё", "housing_provided"], ["Виза", "visa"]],
  housing: [["Город", "region_code"], ["Цена", "price_max"], ["Тип жилья", "housing_type"], ["Комнат", "rooms_min"]],
  marketplace: [["Город", "region_code"], ["Цена", "price_max"], ["Состояние", "condition"]],
};
const GOODS = [["phones", "Телефоны", "📱"], ["furniture", "Мебель", "🛋️"], ["appliances", "Техника", "🔌"], ["auto", "Авто", "🚗"],
               ["clothes", "Одежда", "👕"], ["kids", "Детские", "🧸"], ["tools", "Инструменты", "🛠️"], ["other", "Разное", "📦"]];
function renderCategoryHead() {
  const cat = filters.category;
  $("#catHead").hidden = !cat;
  $("#pillRow").hidden = !cat;
  $("#chips").hidden = Boolean(cat);
  $("#goodsGrid").hidden = cat !== "marketplace";
  if (!cat) return;
  $("#catTitle").textContent = {job: "💼 Работа", housing: "🏠 Жильё", marketplace: "🛍️ Барахолка"}[cat];
  $("#pillRow").innerHTML = PILLS[cat].map(([label, key]) =>
    `<button class="pill-btn ${filters[key] ? "on" : ""}" data-pill>${label} ▾</button>`).join("");
  document.querySelectorAll("[data-pill]").forEach((b) => b.onclick = openFilters);
  if (cat === "marketplace") {
    $("#goodsGrid").innerHTML = GOODS.map(([v, label, icon]) =>
      `<button class="${filters.goods_category === v ? "on" : ""}" data-goods-cat="${v}"><i>${icon}</i>${label}</button>`).join("");
    document.querySelectorAll("[data-goods-cat]").forEach((b) => b.onclick = () => {
      filters.goods_category = filters.goods_category === b.dataset.goodsCat ? "" : b.dataset.goodsCat;
      reloadCatalog();
    });
  }
}
$("#catBack").onclick = () => {
  filters.category = "";
  for (const k of SECTION_FILTERS) filters[k] = DEFAULT_FILTERS[k];
  reloadCatalog();
};

function jobCard(x) {
  const bits = [];
  if (x.schedule) bits.push(`<span class="pill">${esc(labelOf("schedule", x.schedule))}</span>`);
  if (x.housing_provided) bits.push('<span class="pill">🏠 Жильё</span>');
  return `<article class="jcard" data-id="${x.id}" data-cat="job" data-open="${x.id}">
    <div class="jphoto">${x.cover ? `<img src="${esc(x.cover)}" alt="" loading="lazy">` : "💼"}${promoBadge(x)}</div>
    <div class="jbody"><h3>${esc(x.title)}</h3><div class="meta">📍 ${esc(x.city)}</div>
    <div class="price">${esc(priceText(x))}</div><div class="pills">${bits.join("")}</div>
    <button class="more-btn" data-open-btn>Подробнее</button></div></article>`;
}
async function loadBanners() {
  const box = $("#banners");
  try {
    const rows = await api("/api/banners", {quiet: true});
    box.innerHTML = rows.map((b) => b.link
      ? `<a href="${esc(b.link)}" data-banner="${esc(b.link)}">📢 ${esc(b.text)}</a>` : `<div>📢 ${esc(b.text)}</div>`).join("");
    box.hidden = !rows.length;
  } catch (e) { box.hidden = true; }
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-banner]");
  if (!a) return;
  e.preventDefault();
  const link = a.dataset.banner;
  const m = link.match(/[?&]listing=(\d+)/);
  if (m) openDetail(m[1]);
  else if (tg?.openTelegramLink && link.startsWith("https://t.me/")) tg.openTelegramLink(link);
  else if (tg?.openLink) tg.openLink(link);
  else window.open(link, "_blank", "noopener");
});
let bannerChecked = false;
async function useBannerPhoto() {
  if (bannerChecked) return;
  bannerChecked = true;
  try {   // если в app/static лежит banner.jpg, он ложится на баннер под текст
    if ((await api("/api/banner-photo", {quiet: true})).exists) $(".hero").classList.add("photo");
  } catch (e) { /* остаётся рисунок */ }
}
async function loadLanding() {
  useBannerPhoto();
  loadBanners();
  const jobs = $("#freshJobs"), ads = $("#freshAds");
  jobs.innerHTML = '<div class="skeleton" style="width:236px;height:230px"></div><div class="skeleton" style="width:236px;height:230px"></div>';
  ads.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  const failed = (box, again) => {
    box.innerHTML = '<div class="hint-empty">Не удалось загрузить данные<br><button class="retry">Повторить</button></div>';
    box.querySelector(".retry").onclick = again;
  };
  try {
    const r = await api("/api/catalog?limit=8&category=job", {quiet: true});
    jobs.innerHTML = r.items.length ? r.items.map(jobCard).join("") : '<div class="hint-empty">Свежих вакансий пока нет</div>';
  } catch (e) { failed(jobs, loadLanding); }
  try {
    const [h, m] = await Promise.all([api("/api/catalog?limit=6&category=housing", {quiet: true}),
                                      api("/api/catalog?limit=6&category=marketplace", {quiet: true})]);
    const items = [...h.items, ...m.items].sort((a, b) => b.id - a.id).slice(0, 8);
    ads.innerHTML = items.length ? items.map(listingCard).join("") : '<div class="hint-empty">Новых объявлений пока нет</div>';
  } catch (e) { failed(ads, loadLanding); }
  homeLoaded = true;
}
async function loadCatalog(append = false) {
  if (!append && $("#view-home").classList.contains("landing")) return loadLanding();
  const myRun = ++page.run;
  page.loading = true;
  const box = $("#listings");
  if (!append) {
    page.offset = 0;
    $("#moreBtn").hidden = true;
    box.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  }
  try {
    const r = await api("/api/catalog?" + catalogParams(page.offset), {quiet: true});
    if (myRun !== page.run) return;      // за это время запрос изменили: показываем только свежий ответ
    const html = r.items.map(listingCard).join("");
    if (append) box.insertAdjacentHTML("beforeend", html);
    else box.innerHTML = html || `<div class="empty">${activeFilterCount() || filters.q || filters.category
      ? 'Ничего не нашли. Проверьте написание или сбросьте фильтры.<br><button class="link" id="emptyReset">Сбросить фильтры</button> · <button class="link" id="emptyWatch">Сообщить, когда появится</button>'
      : "Пока нет объявлений. Добавьте первое кнопкой «+»."}</div>`;
    page.offset += r.items.length;
    $("#resultCount").textContent = !r.total ? "Ничего не найдено"
      : $("#view-home").classList.contains("landing") ? "Популярные объявления"
      : r.corrected ? `Показано по запросу «${r.corrected}» (найдено: ${r.total})` : `Найдено: ${r.total}`;
    $("#moreBtn").hidden = !r.has_more;
    homeLoaded = true;
  } catch (e) {
    if (!append && myRun === page.run) box.innerHTML = `<div class="empty">Не удалось загрузить: ${esc(e.message)}<br><button class="link" id="retryCatalog">Повторить</button></div>`;
  }
  if (myRun === page.run) page.loading = false;
}
document.addEventListener("click", async (e) => {
  if (e.target.id === "retryCatalog") reloadCatalog();
  if (e.target.id === "emptyReset") { filters = {...DEFAULT_FILTERS}; $("#search").value = ""; reloadCatalog(); }
  if (e.target.id === "emptyWatch") {
    if (!(await requireConsent())) return;
    const body = {q: filters.q || undefined, category: filters.category || undefined, region: filters.region_code || undefined};
    try { await api("/api/me/searches", {method: "POST", body: JSON.stringify(body)}); toast("Сообщим в Telegram, когда появится"); } catch (err) { toast(err.message); }
  }
});
function reloadCatalog() { homeLoaded = false; updateFilterUi(); loadCatalog(); $("#scroll").scrollTop = 0; }

let searchTimer = null;
const HISTORY_KEY = "khSearchHistory";
const loadHistory = () => { try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]"); } catch (e) { return []; } };
function rememberSearch(q) {
  if (!q || q.length < 2) return;
  localStorage.setItem(HISTORY_KEY, JSON.stringify([q, ...loadHistory().filter((x) => x !== q)].slice(0, 8)));
}
const hideSuggest = () => { $("#suggestBox").hidden = true; };
function runSearch(q) {
  $("#search").value = q;
  filters.q = q.trim();
  rememberSearch(filters.q);
  hideSuggest();
  reloadCatalog();
}
let suggestTimer = null;
async function showSuggest() {
  const q = $("#search").value.trim();
  const box = $("#suggestBox");
  let rows = [];
  if (!q) rows = loadHistory().map((t) => ({type: "history", text: t}));
  else { try { rows = await api("/api/suggest?q=" + encodeURIComponent(q), {quiet: true}); } catch (e) { rows = []; } }
  if (!rows.length) { hideSuggest(); return; }
  box.innerHTML = rows.map((r) => `<button type="button" data-suggest="${esc(r.text)}">${r.type === "place" ? "📍" : r.type === "history" ? "🕘" : "🔎"} ${esc(r.text)}<small>${{place: "место", history: "недавно", title: ""}[r.type] || ""}</small></button>`).join("");
  box.hidden = false;
}
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  clearTimeout(suggestTimer);
  suggestTimer = setTimeout(showSuggest, 200);
  searchTimer = setTimeout(() => { filters.q = $("#search").value.trim(); rememberSearch(filters.q); reloadCatalog(); }, 600);
});
$("#search").addEventListener("focus", showSuggest);
$("#suggestBox").addEventListener("pointerdown", (e) => {
  const b = e.target.closest("[data-suggest]");
  if (b) { e.preventDefault(); clearTimeout(searchTimer); runSearch(b.dataset.suggest); }
});
document.addEventListener("pointerdown", (e) => { if (!e.target.closest(".searchbar")) hideSuggest(); });
$("#search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { clearTimeout(searchTimer); filters.q = $("#search").value.trim(); reloadCatalog(); e.target.blur(); }
});
document.querySelectorAll("#chips .chip").forEach((chip) => chip.addEventListener("click", () => {
  filters.category = chip.dataset.cat;
  // Особые фильтры подходят только одному разделу
  for (const k of SECTION_FILTERS) filters[k] = DEFAULT_FILTERS[k];
  reloadCatalog();
}));
document.querySelectorAll("[data-tile]").forEach((t) => t.addEventListener("click", () => {
  filters.category = t.dataset.tile;
  for (const k of SECTION_FILTERS) filters[k] = DEFAULT_FILTERS[k];
  if (t.dataset.goods) filters.goods_category = t.dataset.goods;
  reloadCatalog();
}));
$("#sortSelect").onchange = () => { filters.sort = $("#sortSelect").value; reloadCatalog(); };
$("#moreBtn").onclick = () => loadCatalog(true);

// ---------- города и районы Южной Кореи ----------
let geoData = null;
async function loadGeo() {
  if (!geoData) geoData = (await api("/api/geo", {quiet: true})).regions;
  return geoData;
}
function regionOptions(selected, anyText) {
  return `<option value="">${anyText || "Выберите регион"}</option>` + (geoData || []).map((r) =>
    `<option value="${r.code}" ${r.code === selected ? "selected" : ""}>${esc(r.name)} (${esc(r.name_ko)})</option>`).join("");
}
function districtOptions(regionCode, selectedId, anyText) {
  const r = (geoData || []).find((x) => x.code === regionCode);
  if (!r) return `<option value="">${anyText || "Сначала выберите регион"}</option>`;
  return `<option value="">${anyText || "Выберите город / район"}</option>` + r.districts.map((d) =>
    `<option value="${d.id}" ${d.id === Number(selectedId) ? "selected" : ""}>${esc(d.name)} (${esc(d.name_ko)})</option>`).join("");
}
// Два связанных списка: регион → город или район
async function setGeo(container, regionCode, districtId, anyRegion, anyDistrict) {
  await loadGeo();
  const region = document.querySelector(`${container} [data-geo-region]`);
  const district = document.querySelector(`${container} [data-geo-district]`);
  region.innerHTML = regionOptions(regionCode, anyRegion);
  district.innerHTML = districtOptions(regionCode, districtId, anyDistrict);
  region.onchange = () => { district.innerHTML = districtOptions(region.value, null, anyDistrict); };
}
function showBlocks(formSelector, category) {
  document.querySelectorAll(`${formSelector} [data-for]`).forEach((el) => { el.hidden = el.dataset.for !== category; });
}
function syncCarFilters() {
  const f = $("#filterForm");
  $("#carFilters").hidden = f.goods_category.value !== "auto";
}
document.addEventListener("change", (e) => { if (e.target.matches('#filterForm [name="goods_category"]')) syncCarFilters(); });
async function openFilters() {
  const f = $("#filterForm");
  await loadFields();
  await setGeo("#filterGeo", filters.region_code, filters.district_id, "Вся Корея", "Любой район");
  f.price_min.value = filters.price_min; f.price_max.value = filters.price_max;
  for (const k of SECTION_FILTERS) {
    const el = f.elements[k];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = Boolean(filters[k]); else el.value = filters[k] || "";
  }
  showBlocks("#filterForm", filters.category);
  syncCarFilters();
  $("#filterHint").hidden = Boolean(filters.category);
  $("#filters").classList.remove("hidden");
}
$("#filterBtn").onclick = openFilters;
$("#closeFilters").onclick = () => $("#filters").classList.add("hidden");
function applyFormToFilters(f) {
  filters.region_code = f.region_code.value; filters.district_id = f.district_id.value;
  filters.price_min = f.price_min.value; filters.price_max = f.price_max.value;
  for (const k of SECTION_FILTERS) {
    const el = f.elements[k];
    // Скрытые поля других разделов не учитываются
    const visible = el && !el.closest("[data-for]")?.hidden && !el.closest("#carFilters")?.hidden;
    filters[k] = visible ? (el.type === "checkbox" ? el.checked : el.value) : DEFAULT_FILTERS[k];
  }
}
$("#filterForm").onsubmit = (e) => {
  e.preventDefault();
  applyFormToFilters(e.target);
  $("#filters").classList.add("hidden");
  reloadCatalog();
};
$("#watchBtn").onclick = async () => {
  const body = {};
  applyFormToFilters($("#filterForm"));
  for (const [k, v] of Object.entries(filters)) {
    if (k === "sort" || v === "" || v === false || v == null) continue;
    body[k === "region_code" ? "region" : k] = v;
  }
  try {
    if (!(await requireConsent())) return;
    await api("/api/me/searches", {method: "POST", body: JSON.stringify(body)});
    toast("Готово: пришлём уведомление о новых объявлениях");
  } catch (err) { toast(err.message); }
};
$("#resetFilters").onclick = () => {
  filters = {...DEFAULT_FILTERS, category: filters.category, q: filters.q, sort: filters.sort};
  $("#filters").classList.add("hidden");
  reloadCatalog();
};

// ---------- страница объявления ----------
function contactLinks(contact) {
  const c = (contact || "").trim();
  const digits = c.replace(/\D/g, "");
  const phone = /^[+\d][\d\s().-]*$/.test(c) && digits.length >= 9 ? (c.startsWith("+") ? "+" : "") + digits : "";
  const user = /^@?[A-Za-z][A-Za-z0-9_]{3,31}$/.test(c) ? c.replace(/^@/, "") : "";
  return {phone, user};
}
function authorBlock(x) {
  const a = x.author || {};
  const parts = [];
  if (a.verified) parts.push(`<b class="badge-ok">✔ Проверенный работодатель${a.company ? ": " + esc(a.company) : ""}</b>`);
  else if (a.trusted) parts.push('<b class="badge-ok">✔ Проверенный автор</b>');
  else if (a.since) parts.push("<b>Автор</b>");
  if (a.active) parts.push(`<button class="alink" data-author="${x.id}">Все объявления автора: ${a.active}</button>`);
  if (a.reviews) parts.push(`<b class="rating">★ ${a.rating}</b> (${a.reviews} отзыв.)`);
  if (a.since) parts.push(`На сайте с ${a.since}`);
  return parts.length ? `<p class="author">${parts.join(" · ")}</p>` : "";
}
const APP_STATUS = {new: "Отправлен", viewed: "Просмотрен", invited: "Приглашение", rejected: "Отказ", withdrawn: "Отозван"};
// Окно с коротким текстом (ответ работодателя): системное prompt() в Telegram может не открыться
function askText(title, placeholder = "") {
  return new Promise((resolve) => {
    $("#textTitle").textContent = title;
    $("#textInput").value = "";
    $("#textInput").placeholder = placeholder;
    $("#textSheet").classList.remove("hidden");
    const done = (value) => { $("#textSheet").classList.add("hidden"); resolve(value); };
    $("#textOk").onclick = () => done($("#textInput").value.trim());
    $("#closeText").onclick = () => done(null);
  });
}
let applyListingId = null;
$("#applyResume").onclick = () => {
  const cv = window.__cv; if (!cv) return;
  const parts = [`Меня зовут по резюме: ${cv.position}.`, cv.experience ? `Опыт: ${cv.experience}.` : "", cv.visa ? `Виза: ${cv.visa}.` : "", cv.about || ""].filter(Boolean);
  $("#applyForm").message.value = parts.join(" ").slice(0, 1000);
};
$("#closeApply").onclick = () => $("#applyModal").classList.add("hidden");
$("#applyForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api(`/api/listings/${applyListingId}/apply`, {method: "POST",
      body: JSON.stringify({contact: f.contact.value.trim(), message: f.message.value.trim()})});
    $("#applyModal").classList.add("hidden");
    f.reset();
    toast("Отклик отправлен");
  } catch (err) { toast(err.message); }
};
// ---------- просмотр фото на весь экран ----------
let lbList = [], lbIndex = 0;
function lbShow() {
  $("#lbImg").src = lbList[lbIndex];
  $("#lbCount").textContent = lbList.length > 1 ? `${lbIndex + 1} / ${lbList.length}` : "";
  $("#lbPrev").hidden = $("#lbNext").hidden = lbList.length < 2;
}
function openLightbox(urls, i) { lbList = urls; lbIndex = i; lbShow(); $("#lightbox").classList.remove("hidden"); }
const lbMove = (d) => { if (lbList.length > 1) { lbIndex = (lbIndex + d + lbList.length) % lbList.length; lbShow(); } };
$("#lbClose").onclick = () => $("#lightbox").classList.add("hidden");
$("#lbPrev").onclick = () => lbMove(-1);
$("#lbNext").onclick = () => lbMove(1);
{
  let x0 = null;
  $("#lightbox").addEventListener("touchstart", (e) => { x0 = e.touches.length === 1 ? e.touches[0].clientX : null; }, {passive: true});
  $("#lightbox").addEventListener("touchend", (e) => { if (x0 === null) return; const dx = e.changedTouches[0].clientX - x0; if (Math.abs(dx) > 50) lbMove(dx < 0 ? 1 : -1); x0 = null; }, {passive: true});
  document.addEventListener("keydown", (e) => { if ($("#lightbox").classList.contains("hidden")) return; if (e.key === "Escape") $("#lbClose").click(); if (e.key === "ArrowLeft") lbMove(-1); if (e.key === "ArrowRight") lbMove(1); });
}
document.addEventListener("click", (e) => {
  const img = e.target.closest(".gallery img");
  if (!img) return;
  const all = [...img.closest(".gallery").querySelectorAll("img")];
  openLightbox(all.map((x) => x.src), all.indexOf(img));
});
// ---------- недавно смотрел ----------
function rememberViewed(id) {
  try { const l = JSON.parse(localStorage.getItem("recent") || "[]").filter((x) => x !== id); l.unshift(id); localStorage.setItem("recent", JSON.stringify(l.slice(0, 20))); } catch (e) { /* не критично */ }
}
async function openDetail(id, push = true) {
  try {
    await Promise.all([loadFields(), ensureConfig()]);
    const x = await api(`/api/listings/${id}`, {quiet: true});
    const {phone, user} = contactLinks(x.contact);
    const fav = favIds.has(x.id);
    const photos = x.photos || [];
    const top = photos.length
      ? `<div class="gallery">${photos.map((p) => `<img src="${esc(p.url)}" alt="" loading="lazy">`).join("")}</div>`
      : `<div class="nophoto">${CAT_ICON[x.category] || "📄"}</div>`;
    $("#detailBody").innerHTML = `<article class="detail" data-id="${x.id}" data-cat="${x.category}">
      <div class="dphoto">${top}
        <div class="dtools"><button data-act="fav" class="rnd" aria-label="В избранное">${fav ? "♥" : "♡"}</button>
        <button data-act="share" class="rnd" aria-label="Поделиться">⤴</button></div></div>
      <div class="dhead"><h1>${esc(x.title)}</h1>${promoBadge(x)}</div>
      <div class="price big">${esc(priceText(x))}</div>${approxPrice(x)}
      <div class="meta">📍 ${esc(x.city)} · ${esc(CAT[x.category] || x.category)}</div>
      ${specs(x)}
      <h3 class="dsub">Описание</h3>
      <p class="desc">${esc(x.description || "Подробности уточняйте у автора")}</p>
      ${authorBlock(x)}
      <p class="muted">Опубликовано: ${esc(fmtDate(x.created_at).split(",")[0] || "")}${x.contact && !phone && !user ? " · Контакт: " + esc(x.contact) : ""}</p>
      <div class="cta col">
        ${x.category === "job" ? '<button class="primary apply" data-act="apply">Откликнуться</button>' : ""}
        <button class="primary" data-act="chat">💬 Написать</button>
        ${phone ? `<a class="btn call" href="tel:${esc(phone)}">📞 Позвонить</a>` : ""}
        ${user ? `<a class="btn call" data-tglink href="https://t.me/${esc(user)}" target="_blank" rel="noopener">Telegram автора</a>` : ""}
      </div>
      <button class="linkbtn" data-act="report">⚑ Пожаловаться</button>
    </article>`;
    showView("detail", {push});
    rememberViewed(x.id);
    loadReviewButton(x.id);
    loadSimilar(x.id);
  } catch (e) { toast(e.message); }
}
// Кнопка «Оставить отзыв» только тем, кто переписывался с автором по этому объявлению
async function loadReviewButton(id) {
  try {
    const st = await api(`/api/listings/${id}/review-state`, {quiet: true});
    const art = $("#detailBody").querySelector("article");
    if (!art || art.dataset.id !== String(id)) return;
    if (st.can) art.querySelector(".cta")?.insertAdjacentHTML("afterend", `<button class="linkbtn" data-review="${id}">★ Оставить отзыв об авторе</button>`);
    else if (st.mine) art.querySelector(".cta")?.insertAdjacentHTML("afterend", `<p class="muted">Ваш отзыв: ${"★".repeat(st.mine.rating)} <button class="linkbtn" data-delreview="${st.mine.id}">Удалить</button></p>`);
  } catch (e) { /* гость или без права — кнопки не будет */ }
}
let reviewFor = null, reviewRating = 0, reviewTags = new Set();
const REVIEW_TAGS = {polite: "Вежливый", punctual: "Пунктуальный", as_described: "Всё как в описании", fair_price: "Честная цена", fast: "Быстро отвечает", unreliable: "Не пришёл/не ответил", rude: "Грубый"};
function drawTags() {
  $("#reviewTags").innerHTML = Object.entries(REVIEW_TAGS).map(([k, t]) => `<button type="button" class="chip ${reviewTags.has(k) ? "on" : ""}" data-tag="${k}">${esc(t)}</button>`).join("");
}
$("#reviewTags").onclick = (e) => { const b = e.target.closest("[data-tag]"); if (!b) return; if (reviewTags.has(b.dataset.tag)) reviewTags.delete(b.dataset.tag); else if (reviewTags.size < 4) reviewTags.add(b.dataset.tag); drawTags(); };
function drawStars() {
  $("#reviewStars").innerHTML = [1, 2, 3, 4, 5].map((n) => `<button type="button" role="radio" aria-checked="${n === reviewRating}" class="${n <= reviewRating ? "on" : ""}" data-star="${n}">★</button>`).join("");
}
$("#reviewStars").onclick = (e) => { const b = e.target.closest("[data-star]"); if (b) { reviewRating = +b.dataset.star; drawStars(); } };
$("#closeReview").onclick = () => $("#reviewSheet").classList.add("hidden");
$("#reviewOk").onclick = async () => {
  if (!reviewRating) { toast("Поставьте оценку"); return; }
  try {
    await api("/api/reviews", {method: "POST", body: JSON.stringify({listing_id: reviewFor, rating: reviewRating, text: $("#reviewText").value.trim(), tags: [...reviewTags]})});
    $("#reviewSheet").classList.add("hidden");
    toast("Спасибо за отзыв!");
    openDetail(reviewFor, false);
  } catch (err) { toast(err.message); }
};
// «Похожие объявления»: подгружаются после основной карточки, ошибка не мешает читать объявление
async function loadSimilar(id) {
  try {
    const rows = await api(`/api/listings/${id}/similar`, {quiet: true});
    const body = $("#detailBody");
    if (!rows.length || body.querySelector("article")?.dataset.id !== String(id)) return;
    body.insertAdjacentHTML("beforeend", `<section class="similar"><h3 class="dsub">Похожие объявления</h3>${rows.map(listingCard).join("")}</section>`);
  } catch (e) { /* не критично */ }
}
let botName = null;
async function ensureConfig() {
  if (botName !== null) return;
  try { const cfg = await api("/api/config", {quiet: true}); botName = cfg.bot || ""; ratesCfg = cfg.rates || {}; } catch (e) { botName = ""; }
}
async function shareListing(id, title) {
  await ensureConfig();
  // Ссылка ведёт в чат с ботом: он покажет карточку и кнопку «Открыть в приложении»
  const url = botName ? `https://t.me/${botName}?start=l_${id}` : `${location.origin}/?listing=${id}`;
  if (tg?.openTelegramLink) tg.openTelegramLink("https://t.me/share/url?url=" + encodeURIComponent(url) + "&text=" + encodeURIComponent(title));
  else if (navigator.share) navigator.share({title, url}).catch(() => {});
  else { try { await navigator.clipboard.writeText(url); toast("Ссылка скопирована"); } catch (e) { toast(url); } }
}

// ---------- избранное ----------
async function loadFavIds() {
  try { favIds = new Set((await api("/api/favorites", {quiet: true})).map((x) => x.id)); } catch (e) { favIds = new Set(); }
}
let cmpItems = {};
document.addEventListener("change", (e) => {
  if (!e.target.matches("[data-cmp]")) return;
  const picked = [...document.querySelectorAll("[data-cmp]:checked")];
  if (picked.length > 3) { e.target.checked = false; toast("Сравнить можно до трёх объявлений"); return; }
  const go = document.getElementById("cmpGo");
  if (go) { go.hidden = picked.length < 2; document.getElementById("cmpN").textContent = picked.length; }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#cmpGo")) return;
  const xs = [...document.querySelectorAll("[data-cmp]:checked")].map((c) => cmpItems[c.dataset.cmp]).filter(Boolean);
  const yes = (v) => (v ? "да" : "");
  const rows = [["Название", (x) => x.title], ["Цена", priceText], ["Город", (x) => x.city], ["Раздел", (x) => CAT[x.category] || ""],
    ["Занятость", (x) => labelOf("employment", x.employment) || ""], ["График", (x) => labelOf("schedule", x.schedule) || ""], ["Жильё от работодателя", (x) => yes(x.housing_provided)],
    ["Тип жилья", (x) => labelOf("housing_type", x.housing_type) || ""], ["Залог", (x) => (x.deposit ? fmtKRW(x.deposit) : "")], ["Площадь", (x) => (x.area_m2 ? x.area_m2 + " м²" : "")],
    ["Комнат", (x) => x.rooms || ""], ["Состояние", (x) => labelOf("condition", x.condition) || ""], ["Год", (x) => x.year || ""]]
    .filter(([, f]) => xs.some((x) => f(x)));
  openSub("compare");
  $("#subBody").innerHTML = `<article class="listing"><table class="cmptable">${rows.map(([n, f]) => `<tr><th>${esc(n)}</th>${xs.map((x) => `<td>${esc(String(f(x) ?? ""))}</td>`).join("")}</tr>`).join("")}</table></article>`;
});
async function loadFavorites() {
  const box = $("#favorites");
  try {
    const items = await api("/api/favorites");
    favIds = new Set(items.map((x) => x.id));
    cmpItems = Object.fromEntries(items.map((x) => [x.id, x]));
    box.innerHTML = items.length ? items.map(listingCard).join("") + (items.length > 1 ? '<div class="cmpbar"><button id="cmpGo" hidden>⚖ Сравнить (<span id="cmpN">0</span>)</button></div>' : "")
      : '<div class="empty">Здесь будут объявления, которые вы сохранили ♡</div>';
    if (items.length > 1) box.querySelectorAll("article.listing").forEach((a) => a.insertAdjacentHTML("beforeend", `<label class="cmp"><input type="checkbox" data-cmp="${a.dataset.id}"> сравнить</label>`));
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ---------- чаты ----------
async function loadConversations() {
  const box = $("#conversations");
  try {
    const items = await api("/api/conversations");
    box.innerHTML = items.length ? items.map((c) => `
      <article class="listing convo" data-chat="${c.id}" data-title="${esc(c.listing_title)}">
        <h3>${esc(c.listing_title)}${c.unread ? `<span class="unread-dot">${c.unread}</span>` : ""}</h3><p>${esc(c.last_text || "Нет сообщений")}</p></article>`).join("")
      : '<div class="empty">Переписок пока нет. Откройте объявление и нажмите «Написать».</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function openChat(id, title) {
  currentChat = id;
  lastMessageKey = ""; olderMessages = []; hasOlder = false;
  $("#chatTitle").textContent = title || "Чат";
  $("#chatSub").textContent = "";
  showView("chat", {push: true});
  await loadMessages();
  loadChatStatus();
  clearInterval(chatTimer); clearInterval(chatStatusTimer);
  chatTimer = setInterval(loadMessages, 5000);
  chatStatusTimer = setInterval(loadChatStatus, 3500);
  drawQuickReplies(title);
}
let chatStatusTimer = null, lastTypingPing = 0;
const QUICK = ["Здравствуйте! Ещё актуально?", "Когда можно посмотреть?", "Можно ли снизить цену?", "Спасибо, я подумаю", "Где вы находитесь?"];
function drawQuickReplies() {
  $("#quickReplies").innerHTML = QUICK.map((t) => `<button type="button" data-quick="${esc(t)}">${esc(t)}</button>`).join("");
}
$("#quickReplies").addEventListener("click", (e) => { const b = e.target.closest("[data-quick]"); if (b) { $("#chatInput").value = b.dataset.quick; $("#chatInput").focus(); } });
$("#chatInput").addEventListener("input", () => {
  const t = Date.now();
  if (!currentChat || t - lastTypingPing < 3000) return;
  lastTypingPing = t;
  api(`/api/conversations/${currentChat}/typing`, {method: "POST", quiet: true}).catch(() => {});
});
async function sendAttachment(file) {
  if (!file || !currentChat) return;
  const form = new FormData(); form.append("file", file, file.name || "voice");
  try {
    await api(`/api/conversations/${currentChat}/attachment`, {method: "POST", body: form});
    lastMessageKey = ""; olderMessages = []; hasOlder = false;
    await loadMessages();
  } catch (err) { toast(err.message); }
}
$("#chatFile").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; sendAttachment(f); };
// Голосовое: кнопка видна, только если браузер умеет записывать звук
if (window.MediaRecorder && navigator.mediaDevices?.getUserMedia) {
  $("#chatMic").hidden = false;
  let rec = null, chunks = [], stopTimer = null;
  $("#chatMic").onclick = async () => {
    if (rec) { rec.stop(); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({audio: true});
      rec = new MediaRecorder(stream); chunks = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        clearTimeout(stopTimer); stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, {type: rec.mimeType || "audio/webm"});
        rec = null; $("#chatMic").textContent = "🎤";
        if (blob.size > 800) sendAttachment(new File([blob], "Голосовое сообщение." + (blob.type.includes("mp4") ? "m4a" : "webm"), {type: blob.type}));
      };
      rec.start(); $("#chatMic").textContent = "⏹";
      stopTimer = setTimeout(() => rec && rec.stop(), 60000);   // не длиннее минуты
    } catch (err) { toast("Нет доступа к микрофону"); }
  };
}
async function fetchAttachment(url) {
  const r = await fetch(url, {headers: authHeaders()});
  if (!r.ok) throw new Error("Не удалось открыть файл");
  return URL.createObjectURL(await r.blob());
}
document.addEventListener("click", async (e) => {
  const pv = e.target.closest("[data-playvoice]");
  if (pv) {
    try { const a = new Audio(await fetchAttachment(pv.dataset.playvoice)); await a.play(); pv.textContent = "🔊 Играет…"; a.onended = () => { pv.textContent = "▶ Воспроизвести ещё раз"; }; }
    catch (err) { toast(err.message || "Не удалось воспроизвести"); }
    return;
  }
  const gf = e.target.closest("[data-getfile]");
  if (gf) {
    try {
      const href = await fetchAttachment(gf.dataset.getfile);
      const a = document.createElement("a"); a.href = href; a.download = gf.dataset.fname || "document.pdf"; document.body.appendChild(a); a.click(); a.remove();
    } catch (err) { toast(err.message); }
  }
});
const fileHtml = (m) => !m.file ? "" : m.file.kind === "audio"
  ? `<button type="button" class="chatfile" data-playvoice="${esc(m.file.url)}">▶ ${esc(m.file.name)}</button>`
  : `<button type="button" class="chatfile" data-getfile="${esc(m.file.url)}" data-fname="${esc(m.file.name)}">📄 ${esc(m.file.name)}</button>`;
const msgHtml = (m) => `
      <div class="bubble ${m.mine ? "mine" : ""}">${fileHtml(m)}${m.photo ? `<a href="${esc(m.photo)}" target="_blank" rel="noopener"><img class="chatpic" src="${esc(m.photo)}" alt="" loading="lazy"></a>` : ""}${esc(m.text)}<small>${new Date(m.created_at).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"})}${m.mine ? `<span class="ticks ${m.read ? "read" : ""}">${m.read ? "✓✓" : "✓"}</span>` : ""}</small></div>`;
function renderMessages(items, stick) {
  const all = [...olderMessages, ...items];
  const box = $("#messages");
  const before = box.scrollHeight;
  box.innerHTML = (hasOlder ? '<button class="link older" data-older>Ранее</button>' : "") +
    (all.length ? all.map(msgHtml).join("") : '<div class="empty">Напишите первое сообщение</div>');
  box.scrollTop = stick ? box.scrollHeight : box.scrollHeight - before;
}
async function loadMessages() {
  if (!currentChat) return;
  try {
    const items = await api(`/api/conversations/${currentChat}/messages?limit=50`);
    const key = items.length + ":" + (items[items.length - 1]?.id || 0) + ":" + items.filter((m) => m.read).length;
    if (key === lastMessageKey) return;  // ничего нового: не перерисовываем, экран не дёргается
    if (lastMessageKey === "") hasOlder = items.length === 50;
    lastMessageKey = key;
    renderMessages(items, true);
    window.__latest = items;
  } catch (e) { toast(e.message); }
}
async function loadOlder() {
  const oldest = (olderMessages[0] || window.__latest?.[0])?.id;
  if (!oldest) return;
  try {
    const older = await api(`/api/conversations/${currentChat}/messages?limit=50&before_id=${oldest}`);
    hasOlder = older.length === 50;
    olderMessages = [...older, ...olderMessages];
    renderMessages(window.__latest || [], false);
  } catch (e) { toast(e.message); }
}
document.addEventListener("click", (e) => { if (e.target.closest("[data-older]")) loadOlder(); });
$("#chatBack").onclick = goBack;
$("#chatBlock").onclick = async () => {
  if (!currentChat || !(await confirmDialog("Заблокировать собеседника? Он больше не сможет вам писать."))) return;
  try { await api(`/api/conversations/${currentChat}/block`, {method: "POST"}); toast("Собеседник заблокирован"); goBack(); }
  catch (err) { toast(err.message); }
};
$("#chatReport").onclick = async () => {
  if (!currentChat) return;
  const reason = await askReason();
  if (!reason) return;
  try {
    await api(`/api/conversations/${currentChat}/report`, {method: "POST", body: JSON.stringify({reason})});
    toast("Жалоба отправлена. Спасибо!");
  } catch (err) { toast(err.message); }
};
$("#chatPhoto").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file || !currentChat) return;
  const form = new FormData(); form.append("file", file);
  try {
    await api(`/api/conversations/${currentChat}/photo`, {method: "POST", body: form});
    lastMessageKey = ""; olderMessages = []; hasOlder = false;
    await loadMessages();
  } catch (err) { toast(err.message); }
};
async function loadChatStatus() {
  if (!currentChat) return;
  try {
    const r = await api(`/api/conversations/${currentChat}/status`, {quiet: true});
    const el = $("#chatSub");
    el.textContent = r.typing ? "печатает…" : r.status === "online" ? "в сети" : r.status === "recently" ? "был(а) недавно" : "";
    el.className = "chat-sub " + r.status;
  } catch (e) { /* статус не обязателен */ }
}
$("#chatForm").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#chatInput");
  const text = input.value.trim();
  if (!text || !currentChat) return;
  try {
    await api(`/api/conversations/${currentChat}/messages`, {method: "POST", body: JSON.stringify({text})});
    input.value = "";
    lastMessageKey = ""; olderMessages = []; hasOlder = false;
    await loadMessages();
  } catch (err) { toast(err.message); }
};

// ---------- профиль ----------
const STATUS_BADGE = (x) => `<span class="status ${STATUS[x.status]?.[1] || ""}">${STATUS[x.status]?.[0] || esc(x.status)}</span>`;
function ownCard(x) {
  const acts = [];
  if (x.status === "draft") acts.push('<button data-own="submit">Отправить на проверку</button>');
  if (["draft", "pending", "approved", "rejected", "needs_fix"].includes(x.status)) acts.push('<button data-own="edit" class="secondary">✏️ Изменить</button>');
  if (x.category === "job" && x.applications?.total) acts.push(`<button data-own="applicants">📨 Отклики (${x.applications.total}${x.applications.new ? ", новых " + x.applications.new : ""})</button>`);
  if (["approved", "expired"].includes(x.status)) acts.push('<button data-own="renew">🔄 Продлить</button>');
  if (x.status === "approved") acts.push('<button data-own="stats" class="secondary">📊 Статистика</button>');
  if (x.status === "approved") {
    if (x.category === "marketplace") acts.push(`<button data-own="reserve" class="secondary">${x.reserved ? "Снять резерв" : "Зарезервировано"}</button>`);
    acts.push('<button data-own="close" class="secondary">Закрыть</button>');
    if (x.category === "marketplace") acts.push('<button data-own="sold" class="secondary">Продано</button>');
    acts.push('<button data-own="promo">🚀 Продвинуть</button>');
  }
  if (["closed", "sold"].includes(x.status)) acts.push('<button data-own="reopen">Вернуть</button>');
  if (["rejected", "needs_fix", "hidden"].includes(x.status)) acts.push('<button data-own="appeal" class="secondary">Оспорить решение</button>');
  if (x.status !== "archived") acts.push('<button data-own="archive" class="secondary">Удалить</button>');
  const note = x.note ? `<p class="note">Комментарий модератора: ${esc(x.note)}</p>` : "";
  const edit = x.has_edit ? '<p class="muted">Правка на проверке — пока видна прежняя версия</p>' : "";
  const until = x.status === "approved" && x.expires_at ? ` · до ${esc(fmtDate(x.expires_at).split(",")[0])}` : "";
  const runs = (x.placements || []).map((p) => `${(PROMO[p.kind] || p.kind)} до ${fmtDate(p.ends_at).split(",")[0]}`).join(" · ");
  const meta = x.status === "approved" ? `<p class="meta-line">👁 ${x.views || 0}${until}</p>${runs ? `<p class="meta-line">🚀 ${esc(runs)}</p>` : ""}` : "";
  return `<article class="listing" data-id="${x.id}" data-cat="${x.category}">
    ${x.cover ? `<img class="cover" src="${esc(x.cover)}" alt="" loading="lazy">` : ""}
    <div class="listing-top"><div><h3>${esc(x.title)}</h3><div class="meta">📍 ${esc(x.city)}</div></div>${STATUS_BADGE(x)}</div>
    <div class="price">${esc(priceText(x))}</div>${meta}${edit}${note}
    <div class="actions">${acts.join("")}</div></article>`;
}
const OWN_GROUPS = {active: ["approved"], review: ["pending", "needs_fix", "rejected"], draft: ["draft"],
                    closed: ["closed", "sold", "expired", "hidden"]};
function renderOwn() {
  const visible = mineAll.filter((x) => x.status !== "archived" && (ownFilter === "all" || OWN_GROUPS[ownFilter].includes(x.status)));
  $("#myListings").innerHTML = visible.length ? visible.map(ownCard).join("")
    : `<div class="empty">${mineAll.length ? "В этом разделе пока пусто" : "Вы ещё не публиковали объявления. Нажмите «+» внизу."}</div>`;
}
document.querySelectorAll("[data-own-filter]").forEach((b) => b.onclick = () => {
  ownFilter = b.dataset.ownFilter;
  document.querySelectorAll("[data-own-filter]").forEach((c) => c.classList.toggle("active", c === b));
  renderOwn();
});
async function showApplicants(id) {
  const rows = await api(`/api/listings/${id}/applications`);
  SUB_TITLES.applicants = "Отклики";
  openSub("applicants");
  $("#subBody").innerHTML = `<h3>${esc(ownItems[id]?.title || "")}</h3>` + (rows.map((a) => `<article class="listing" data-app="${a.id}">
    <div class="listing-top"><div><b>${esc(a.contact)}</b><div class="meta">${a.city ? "📍 " + esc(a.city) : ""}${a.visa ? " · виза " + esc(a.visa) : ""}</div></div>
    <span class="status ${a.status === "invited" ? "approved" : a.status === "rejected" ? "rejected" : "pending"}">${APP_STATUS[a.status] || a.status}</span></div>
    ${a.message ? `<p>${esc(a.message)}</p>` : ""}${a.reply ? `<p class="muted">Ваш ответ: ${esc(a.reply)}</p>` : ""}<p class="muted">${esc(fmtDate(a.created_at))}</p>
    <div class="actions"><button data-appstatus="invited" data-appid="${a.id}">Пригласить</button>
    <button data-appstatus="rejected" data-appid="${a.id}" class="secondary">Отказать</button></div></article>`).join("")
    || '<div class="empty">Откликов пока нет</div>');
  loadProfile();
}
function fillProfileHead() {
  const u = tg?.initDataUnsafe?.user;
  const name = me?.display_name || [u?.first_name, u?.last_name].filter(Boolean).join(" ") || "Мой профиль";
  $("#pName").textContent = name;
  const initials = ((name || "?")[0] + ((name.split(" ")[1] || "")[0] || "")).toUpperCase();
  const photo = me?.avatar || u?.photo_url;
  $("#pAvatar").innerHTML = photo ? `<img src="${esc(photo)}" alt="">` : esc(initials);
}
function showProfileChips() {
  fillProfileHead();
  $("#pCity").textContent = me?.city || "Выберите город";
  $("#pVisa").textContent = me?.visa ? "виза " + me.visa : "виза не указана";
  $("#pEmployer").hidden = me?.employer_status !== "approved";
  showCityChip();
}
async function loadProfile() {
  fillProfileHead();
  try {
    const [user, mine, favs, convs, apps] = await Promise.all([api("/api/me"), api("/api/me/listings"), api("/api/favorites"), api("/api/conversations"), api("/api/me/applications")]);
    me = user;
    showBell(user.unread || 0);
    showProfileChips();
    favIds = new Set(favs.map((x) => x.id));
    const alive = mine.filter((x) => x.status !== "archived");
    $("#sApps").textContent = apps.length;
    $("#sListings").textContent = alive.length;
    $("#sFav").textContent = favs.length;
    $("#sChats").textContent = convs.length;
    $("#sChats").hidden = convs.length === 0;
    const attention = alive.filter((x) => ["pending", "needs_fix", "rejected", "draft"].includes(x.status) || x.has_edit).length;
    $("#sMineBadge").textContent = attention;
    $("#sMineBadge").hidden = attention === 0;
    ownItems = Object.fromEntries(mine.map((x) => [x.id, x]));
    mineAll = mine;
    renderOwn();
  } catch (e) { $("#myListings").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function renderPayments(rows) {
  return rows.length ? rows.map((p) => `
    <article class="listing"><h3>${esc(p.service)}</h3>
    <p>${p.stars} ⭐${p.promo ? " (промокод " + esc(p.promo) + ")" : ""} · ${esc({pending: "ожидает оплаты", paid: "оплачено", refunded: "возврат выполнен", failed: "ошибка"}[p.status] || p.status)} · объявление #${p.listing_id}</p>
    <p class="muted">${esc(p.number)} · ${fmtDate(p.paid_at || p.created_at)}</p>
    ${p.status === "paid" || p.status === "refunded" ? `<div class="actions"><button class="secondary" data-receipt="${p.id}">Квитанция</button></div>` : ""}</article>`).join("")
    : '<div class="empty">Платежей пока нет.<br>Продвижение объявления покупается в «Мои объявления» → «Продвинуть».</div>';
}
async function showReceipt(id) {
  const r = await api(`/api/me/payments/${id}/receipt`);
  SUB_TITLES.receipt = "Квитанция " + r.number;
  openSub("receipt");
  const rows = [["Номер", r.number], ["Дата", fmtDate(r.date)], ["Услуга", r.service + (r.days > 1 ? ` (${r.days} дн.)` : "")],
    ["Объявление", `#${r.listing_id} ${r.listing_title}`], ["Цена", r.list_stars + " ⭐"],
    ...(r.discount ? [["Скидка", `−${r.discount} ⭐ (промокод ${r.promo})`]] : []), ["Оплачено", r.stars + " ⭐"],
    ["Статус", {paid: "оплачено", refunded: "возврат выполнен", pending: "ожидает оплаты", failed: "ошибка"}[r.status] || r.status],
    ["Платёж Telegram", r.charge_id || "—"]];
  $("#subBody").innerHTML = `<article class="listing receipt"><dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join("")}</dl>
    <div class="actions"><button id="copyReceipt">Скопировать</button></div></article>`;
  $("#copyReceipt").onclick = async () => {
    try { await navigator.clipboard.writeText(rows.map(([k, v]) => `${k}: ${v}`).join("\n")); toast("Квитанция скопирована"); } catch (e) { toast("Не удалось скопировать"); }
  };
}
const SUB_TITLES = {author: "Объявления автора", applications: "Мои отклики", searches: "Мои поиски", settings: "Настройки", employer: "Статус работодателя",
                    recent: "Недавно смотрел", myreviews: "Мои отзывы", compare: "Сравнение", support: "Поддержка", faq: "Вопросы и ответы", ticket: "Обращение", invite: "Пригласить друзей", resume: "Моё резюме", cvsearch: "Поиск сотрудников", payments: "История платежей", blocks: "Заблокированные", help: "Помощь и правила"};
let currentSubKey = "";
const TICKET_STATUS = {open: "Ждёт ответа", answered: "Есть ответ", closed: "Закрыто"};
// ---------- поддержка внутри приложения: без ников, ответ приходит сюда ----------
async function renderSupport(body) {
  const [rows, texts] = await Promise.all([api("/api/tickets"), api("/api/app-texts", {quiet: true}).catch(() => ({}))]);
  body.innerHTML = `<article class="listing"><p class="muted">${esc(texts.support_note || "")}</p>
    <form id="ticketForm" class="form"><label>Ваш вопрос или проблема<textarea name="text" rows="4" minlength="5" maxlength="1500" required placeholder="Опишите, что случилось. Для платежей укажите, что и когда покупали"></textarea></label>
    <button class="submit" type="submit">Отправить</button></form></article>` +
    (rows.length ? "<h3 class=\"dsub\">Мои обращения</h3>" + rows.map((t) => `<article class="listing" data-ticket="${t.id}"><div class="listing-top"><div><h3>${t.kind === "appeal" ? "Оспаривание: " : ""}${esc(t.preview)}</h3>
      <div class="meta">${esc(fmtDate(t.updated_at))}</div></div><span class="status ${t.status === "answered" ? "approved" : t.status === "closed" ? "rejected" : "pending"}">${TICKET_STATUS[t.status] || t.status}</span></div></article>`).join("") : "");
  $("#ticketForm").onsubmit = async (e) => {
    e.preventDefault();
    if (!(await requireConsent())) return;
    try { await api("/api/tickets", {method: "POST", body: JSON.stringify({text: e.target.text.value.trim()})}); toast("Отправлено. Ответ придёт сюда и в уведомления"); renderSupport(body); }
    catch (err) { toast(err.message); }
  };
}
async function openTicket(id) {
  const t = await api(`/api/tickets/${id}`);
  openSub("ticket");
  $("#subBody").innerHTML = `<article class="listing"><div class="meta">${TICKET_STATUS[t.status] || t.status}</div>` +
    t.messages.map((m) => `<div class="review"><b>${m.staff ? "Поддержка" : "Вы"}</b> · ${esc(fmtDate(m.at))}<p style="white-space:pre-wrap">${esc(m.text)}</p></div>`).join("") +
    (t.status !== "closed" ? `<form id="ticketReply" class="form"><label>Ответить<textarea name="text" rows="3" maxlength="1500" required></textarea></label><button class="submit" type="submit">Отправить</button></form>` : "") + "</article>";
  const f = $("#ticketReply");
  if (f) f.onsubmit = async (e) => {
    e.preventDefault();
    try { await api(`/api/tickets/${id}/messages`, {method: "POST", body: JSON.stringify({text: f.text.value.trim()})}); openTicket(id); } catch (err) { toast(err.message); }
  };
}
// ---------- приветствие при первом открытии: текст задаётся в админке ----------
async function showWelcomeOnce() {
  if (localStorage.getItem("welcomeSeen")) return;
  try {
    const t = await api("/api/app-texts", {quiet: true});
    $("#welcomeTitle").textContent = t.welcome_title || "KOREA HUB";
    $("#welcomeText").textContent = t.welcome_text || "";
    $("#welcomeSheet").classList.remove("hidden");
  } catch (e) { return; }
  const close = () => { localStorage.setItem("welcomeSeen", "1"); $("#welcomeSheet").classList.add("hidden"); };
  $("#welcomeOk").onclick = close; $("#closeWelcome").onclick = close;
}
const CV_STATUS = {pending: "На проверке у модератора", approved: "Опубликовано: работодатели могут найти", rejected: "Отклонено", hidden: "Скрыто"};
// ---------- «Ищу работу»: своё резюме ----------
async function renderMyResume(body) {
  await loadFields();
  const cv = await api("/api/me/resume");
  const v = cv || {};
  body.innerHTML = `<article class="listing">
    <p class="muted">Имя и контакты не показываются. Работодатель может только пригласить вас в чат по своей вакансии, а отвечать или нет — решаете вы.</p>
    ${cv ? `<p><b>${esc(CV_STATUS[cv.status] || cv.status)}</b>${cv.note ? ": " + esc(cv.note) : ""}</p>` : ""}
    <form id="cvForm" class="form">
      <label>Кем хотите работать<input name="position" required minlength="3" maxlength="80" value="${esc(v.position || "")}"></label>
      <label>Город<input name="city" required minlength="2" maxlength="100" value="${esc(v.city || me?.city || "")}"></label>
      <label>Виза<select name="visa"><option value="">Не указана</option>${fieldDefs.visas.map((x) => `<option value="${x.value}" ${v.visa === x.value ? "selected" : ""}>${x.label}</option>`).join("")}</select></label>
      <label>Опыт<textarea name="experience" rows="2" maxlength="300">${esc(v.experience || "")}</textarea></label>
      <label>О себе (без телефонов и ссылок)<textarea name="about" rows="3" maxlength="700">${esc(v.about || "")}</textarea></label>
      <label>Зарплата от, ₩ в месяц<input name="salary_from" type="number" min="0" step="10000" value="${v.salary_from ?? ""}"></label>
      <button class="submit" type="submit">${cv ? "Сохранить (уйдёт на проверку)" : "Отправить на проверку"}</button>
    </form>
    ${cv ? '<div class="actions"><button class="secondary" id="cvHide">Скрыть</button><button class="secondary" id="cvDel">Удалить</button></div>' : ""}</article>`;
  $("#cvForm").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    const salary = f.salary_from.value;
    try {
      await api("/api/me/resume", {method: "PUT", body: JSON.stringify({position: f.position.value.trim(), city: f.city.value.trim(), visa: f.visa.value,
        experience: f.experience.value.trim(), about: f.about.value.trim(), salary_from: salary === "" ? null : +salary})});
      toast("Резюме отправлено на проверку");
      renderMyResume(body);
    } catch (err) { toast(err.message); }
  };
  $("#cvHide") && ($("#cvHide").onclick = async () => { try { await api("/api/me/resume/hide", {method: "POST"}); renderMyResume(body); } catch (err) { toast(err.message); } });
  $("#cvDel") && ($("#cvDel").onclick = async () => {
    if (!(await confirmDialog("Удалить резюме?"))) return;
    try { await api("/api/me/resume", {method: "DELETE"}); renderMyResume(body); } catch (err) { toast(err.message); }
  });
}
// ---------- поиск сотрудников (работодатель с опубликованной вакансией) ----------
async function renderCvSearch(body) {
  body.innerHTML = `<form id="cvSearch" class="toolbar"><input name="q" placeholder="Профессия или слово" maxlength="60"><input name="city" placeholder="Город" maxlength="100"><button type="submit">Найти</button></form><div id="cvList"></div>`;
  const run = async () => {
    const f = $("#cvSearch");
    const qs = new URLSearchParams({q: f.q.value.trim(), city: f.city.value.trim()});
    try {
      const rows = await api("/api/resumes?" + qs);
      $("#cvList").innerHTML = rows.length ? rows.map((r) => `<article class="listing"><h3>${esc(r.position)}</h3>
        <div class="meta">📍 ${esc(r.city)}${r.visa ? " · 🛂 " + esc(r.visa) : ""}${r.salary_from ? " · от " + r.salary_from.toLocaleString("ru-RU") + " ₩" : ""}</div>
        ${r.experience ? `<p>${esc(r.experience)}</p>` : ""}${r.about ? `<p class="muted">${esc(r.about)}</p>` : ""}
        <div class="actions"><button data-invite="${esc(r.id)}">Пригласить на вакансию</button></div></article>`).join("")
        : '<div class="empty">Подходящих резюме пока нет.</div>';
    } catch (err) { $("#cvList").innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
  };
  $("#cvSearch").onsubmit = (e) => { e.preventDefault(); run(); };
  run();
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-invite]");
  if (!b) return;
  try {
    const mine = (await api("/api/me/listings")).filter((x) => x.category === "job" && x.status === "approved");
    if (!mine.length) { toast("Нужна опубликованная вакансия"); return; }
    let pick = mine[0];
    if (mine.length > 1) {
      const n = await askText("Номер вакансии: " + mine.map((x, i) => `${i + 1} — ${x.title}`).join("; "), "1");
      if (!n) return;
      pick = mine[Math.max(0, Math.min(mine.length - 1, (parseInt(n, 10) || 1) - 1))];
    }
    const r = await api(`/api/resumes/${b.dataset.invite}/invite`, {method: "POST", body: JSON.stringify({listing_id: pick.id})});
    toast("Приглашение отправлено");
    b.disabled = true;
  } catch (err) { toast(err.message); }
});
// Раздел кабинета открывается отдельной страницей со стрелкой «назад», а не под длинным списком
function openSub(key) {
  currentSubKey = key;
  $("#subTitle").textContent = SUB_TITLES[key] || "";
  if (currentView !== "sub") showView("sub", {push: true});
}
async function openMenu(key) {
  if (key === "favorites") return showView("saved");
  if (key === "chats") return showView("chats");
  if (key === "mine") return showView("mine", {push: true});
  if (key === "export") return exportMyData();
  if (key === "delete") return deleteMyAccount();
  if (key === "logout") {
    await api("/api/auth/logout", {method: "POST", quiet: true});
    localStorage.removeItem("webSession");
    location.reload();
    return;
  }
  if (!SUB_TITLES[key]) return;
  openSub(key);
  const body = $("#subBody");
  body.innerHTML = '<div class="skeleton"></div>';
  if (key === "payments") body.innerHTML = renderPayments(await api("/api/me/payments"));
  if (key === "applications") {
    const rows = await api("/api/me/applications");
    body.innerHTML = rows.length ? rows.map((a) => `<article class="listing" data-open="${a.listing_id}">
      <div class="listing-top"><div><h3>${esc(a.title)}</h3><div class="meta">📍 ${esc(a.city)}</div></div>
      <span class="status ${a.status === "invited" ? "approved" : ["rejected", "withdrawn"].includes(a.status) ? "rejected" : "pending"}">${APP_STATUS[a.status] || a.status}</span></div>
      ${a.reply ? `<p><b>Ответ работодателя:</b> ${esc(a.reply)}</p>` : ""}
      ${a.listing_status !== "approved" ? '<p class="muted">Вакансия закрыта</p>' : ""}
      <p class="muted">${esc(fmtDate(a.created_at))}</p>
      ${a.can_withdraw ? `<div class="actions"><button class="secondary" data-withdraw="${a.id}">Отозвать отклик</button></div>` : ""}</article>`).join("")
      : '<div class="empty">Вы пока не откликались на вакансии.<br>Откройте вакансию и нажмите «Откликнуться».</div>';
  }
  if (key === "searches") {
    const rows = await api("/api/me/searches");
    window.__searches = Object.fromEntries(rows.map((x) => [x.id, x]));
    body.innerHTML = rows.length ? rows.map((x) => `<article class="listing"><h3>${esc(x.name)}</h3>
      <p class="muted">${x.paused ? "⏸ На паузе: уведомлений нет" : "Пришлём уведомление в Telegram, когда появится новое подходящее объявление"}</p>
      <div class="actions"><button data-opensearch="${x.id}">Смотреть сейчас</button>
        <button data-pausesearch="${x.id}" data-on="${x.paused ? "" : "1"}" class="secondary">${x.paused ? "▶ Включить" : "⏸ Пауза"}</button>
        <button data-delsearch="${x.id}" class="secondary">Не следить</button></div></article>`).join("")
      : '<div class="empty">Нет сохранённых поисков.<br>На главной откройте «Фильтры» и нажмите «Следить за этим поиском».</div>';
  }
  if (key === "settings") {
    await loadFields();
    let prefs = {master: true, kinds: []};
    try { await loadMe(); prefs = await api("/api/me/notify-prefs"); } catch (e) { /* без входа api() сам покажет окно входа */ }
    const initials = ((me?.display_name || "?")[0] || "?").toUpperCase();
    body.innerHTML = `<article class="listing">
      <div class="avatar-row"><div class="avatar" id="setAvatar">${me?.avatar ? `<img src="${esc(me.avatar)}" alt="">` : esc(initials)}</div>
        <div style="flex:1;min-width:0"><input id="setName" type="text" maxlength="40" placeholder="Ваше имя" value="${esc(me?.display_name || "")}">
        <small class="muted">Имя видит только работодатель, когда вы откликаетесь</small></div></div>
      <div class="actions"><label class="btn secondary" style="flex:1;text-align:center;cursor:pointer;padding:10px;border-radius:10px">Загрузить фото<input type="file" id="setAvatarFile" accept="image/jpeg,image/png,image/webp" hidden></label>
        <button class="secondary" id="setAvatarDel">Убрать</button><button id="setNameSave">Сохранить имя</button></div>
      <div class="set-row"><div><b>Город</b><small>${esc(me?.city || "не выбран")}</small></div><button class="secondary" id="setCity">Изменить</button></div>
      <div class="set-row"><div><b>Виза</b><small>Видна только вам: подставляется в отклики и фильтры</small></div>
        <select id="myVisa"><option value="">Не указана</option>${fieldDefs.visas.map((v) => `<option value="${v.value}">${v.label}</option>`).join("")}</select></div>
      <div class="set-row"><div><b>Уведомления в Telegram</b><small>Главный выключатель</small></div>
        <label class="check" style="margin:0"><input type="checkbox" id="setNotify"></label></div>
      ${prefs.kinds.map((k) => `<div class="set-row" style="padding-left:14px"><div>${esc(k.label)}</div>
        <label class="check" style="margin:0"><input type="checkbox" data-kind="${k.key}" ${k.on ? "checked" : ""}></label></div>`).join("")}
      <div class="set-row" style="padding-left:14px"><div>Новое по поискам — раз в сутки одним сообщением<small>Вместо уведомления о каждом объявлении</small></div>
        <label class="check" style="margin:0"><input type="checkbox" id="setDigest" ${prefs.digest ? "checked" : ""}></label></div>
      <p class="muted">Все уведомления всегда видны в приложении (колокольчик), даже если в Telegram они выключены.</p>
      <div class="set-row"><div><b>Размер текста</b><small>Если мелко читать</small></div>
        <span><button class="secondary" data-fs="">A</button> <button class="secondary" data-fs="1">A+</button> <button class="secondary" data-fs="2">A++</button></span></div>
      <div class="set-row"><div><b>Язык</b><small>Сейчас доступен только русский</small></div><span class="muted">RU</span></div></article>`;
    $("#myVisa").value = me?.visa || "";
    $("#setNotify").checked = Boolean(prefs.master);
    body.querySelectorAll("[data-fs]").forEach((b) => { b.onclick = () => { if (b.dataset.fs) localStorage.setItem("fs", b.dataset.fs); else localStorage.removeItem("fs"); applyTextSize(); }; });
    $("#setDigest").onchange = async (e) => {
      try { await api("/api/me/notify-prefs", {method: "PUT", body: JSON.stringify({digest: e.target.checked})}); toast("Сохранено"); }
      catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
    };
    $("#setCity").onclick = openCity;
    $("#myVisa").onchange = async () => {
      try { const r = await api("/api/me/settings", {method: "PUT", body: JSON.stringify({visa: $("#myVisa").value})}); me.visa = r.visa; showProfileChips(); toast("Сохранено"); }
      catch (err) { toast(err.message); }
    };
    $("#setNotify").onchange = async (e) => {
      try { await api("/api/me/notify-prefs", {method: "PUT", body: JSON.stringify({master: e.target.checked})}); me.notify_messages = e.target.checked; toast("Сохранено"); }
      catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
    };
    document.querySelectorAll("[data-kind]").forEach((c) => c.onchange = async () => {
      try { await api("/api/me/notify-prefs", {method: "PUT", body: JSON.stringify({kinds: {[c.dataset.kind]: c.checked}})}); toast("Сохранено"); }
      catch (err) { toast(err.message); c.checked = !c.checked; }
    });
    $("#setNameSave").onclick = async () => {
      try { const r = await api("/api/me/profile", {method: "PUT", body: JSON.stringify({display_name: $("#setName").value})}); me.display_name = r.display_name; showProfileChips(); toast("Имя сохранено"); }
      catch (err) { toast(err.message); }
    };
    $("#setAvatarFile").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const form = new FormData(); form.append("file", file);
      try { const r = await api("/api/me/avatar", {method: "POST", body: form}); me.avatar = r.avatar; showProfileChips(); openMenu("settings"); toast("Фото сохранено"); }
      catch (err) { toast(err.message); }
    };
    $("#setAvatarDel").onclick = async () => {
      try { await api("/api/me/avatar", {method: "DELETE"}); me.avatar = null; showProfileChips(); openMenu("settings"); }
      catch (err) { toast(err.message); }
    };
  }
  if (key === "employer") {
    const st = me?.employer_status;
    const text = {none: "Работодателям с подтверждённым статусом доверяют больше: у их вакансий появляется значок «Проверенный работодатель». Нужны название и номер регистрации компании.",
                  pending: "Заявка на проверке модератора. Мы сообщим в Telegram о решении.",
                  approved: `Статус подтверждён${me?.company_name ? ": " + me.company_name : ""}. На ваших вакансиях виден значок «Проверенный работодатель».`,
                  rejected: "Заявка отклонена. Можно подать снова с верными данными."}[st] || "";
    body.innerHTML = `<article class="listing"><p>${esc(text)}</p>
      ${(st === "none" || st === "rejected") ? '<div class="actions"><button data-own="employer-apply">Подать заявку</button></div>' : ""}</article>`;
  }
  if (key === "recent") {
    let ids = []; try { ids = JSON.parse(localStorage.getItem("recent") || "[]"); } catch (e) { ids = []; }
    const rows = ids.length ? await api("/api/listings-batch?ids=" + ids.join(","), {quiet: true}) : [];
    body.innerHTML = rows.length ? rows.map(listingCard).join("") : '<div class="empty">Вы пока ничего не открывали, или объявления уже сняты.</div>';
  }
  if (key === "myreviews") {
    const r = await api("/api/me/reviews");
    body.innerHTML = `<article class="listing"><h3>${r.reviews ? `<span class="rating">★ ${r.rating}</span> · отзывов: ${r.reviews}` : "Отзывов пока нет"}</h3>
      <p class="muted">Отзыв можно оставить после переписки по объявлению. Вы вправе один раз публично ответить на каждый.</p></article>` +
      r.items.map((v) => `<article class="listing review" data-rv="${v.id}"><b class="rating">${"★".repeat(v.rating)}</b> ${esc(v.name)} · ${esc(v.at)}
        <div class="muted">${esc(v.listing)}</div>${v.tags.map((t) => `<span class="tagchip">${esc(t)}</span>`).join("")}${v.text ? `<p>${esc(v.text)}</p>` : ""}
        ${v.reply ? `<p class="muted"><b>Ваш ответ:</b> ${esc(v.reply)}</p>` : `<button class="linkbtn" data-replyreview="${v.id}">Ответить</button>`}</article>`).join("");
  }
  if (key === "support") await renderSupport(body);
  if (key === "faq") {
    const rows = await api("/api/faq", {quiet: true});
    body.innerHTML = rows.length ? rows.map((r) => `<article class="listing"><details><summary><b>${esc(r.question)}</b></summary><p style="white-space:pre-wrap">${esc(r.answer)}</p></details></article>`).join("")
      : '<div class="empty">Ответы скоро появятся. Если вопрос срочный, напишите в поддержку.</div>';
  }
  if (key === "invite") {
    await ensureConfig();
    const r = await api("/api/me/referral");
    const link = botName ? `https://t.me/${botName}?start=ref_${r.code}` : "";
    body.innerHTML = `<article class="listing"><p>Отправьте ссылку друзьям: когда они зайдут в KOREA HUB, мы сообщим вам.</p>
      <p><b>Приглашено: ${r.invited}</b></p>
      ${link ? `<p class="muted" style="word-break:break-all">${esc(link)}</p><div class="actions"><button id="inviteShare">Поделиться ссылкой</button></div>` : '<p class="muted">Ссылка появится, когда бот будет доступен.</p>'}</article>`;
    if (link) $("#inviteShare").onclick = () => {
      if (tg?.openTelegramLink) tg.openTelegramLink("https://t.me/share/url?url=" + encodeURIComponent(link) + "&text=" + encodeURIComponent("KOREA HUB: работа, жильё и объявления в Корее"));
      else if (navigator.share) navigator.share({url: link}).catch(() => {});
      else navigator.clipboard?.writeText(link).then(() => toast("Ссылка скопирована"));
    };
  }
  if (key === "resume") await renderMyResume(body);
  if (key === "cvsearch") await renderCvSearch(body);
  if (key === "blocks") {
    const rows = await api("/api/me/blocks");
    body.innerHTML = rows.length ? rows.map((r) => `
      <article class="listing"><h3>${esc(r.label)}</h3>
      <div class="actions"><button data-unblock="${r.id}" class="secondary">Разблокировать</button></div></article>`).join("")
      : '<div class="empty">Вы никого не блокировали.<br>Заблокировать можно из чата.</div>';
  }
  if (key === "help") {
    body.innerHTML = `<article class="listing"><h3>Как пользоваться</h3>
      <p>1. Опубликуйте объявление: модератор проверит его обычно в течение суток.</p>
      <p>2. Общение внутри приложения: телефон указывайте по желанию, только в поле «Контакт».</p>
      <p>3. Оплата услуг продвижения — в звёздах Telegram (⭐). Возврат — через поддержку.</p>
      <p>4. Жалобы на объявления и людей — кнопка «Пожаловаться».</p>
      <p>5. Объявление живёт 30 дней. За 3 дня и за день мы напомним, продлить можно в один шаг.</p>
      <p>Вопросы: команда /help в боте.</p></article>`;
  }
}
async function exportMyData() {
  try {
    const data = await api("/api/me/export");
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "korea-hub-moi-dannye.json"; link.click();
    URL.revokeObjectURL(url);
  } catch (err) { toast(err.message); }
}
async function deleteMyAccount() {
  if (!(await confirmDialog("Удалить аккаунт? Объявления будут скрыты, избранное удалено. Действие необратимо."))) return;
  try { await api("/api/me/delete", {method: "POST"}); toast("Аккаунт удалён"); showView("home"); reloadCatalog(); }
  catch (err) { toast(err.message); }
}

// ---------- создание и редактирование объявления ----------
let formKind = "job";
const KIND_CATEGORY = {job: "job", housing: "housing", goods: "marketplace", car: "marketplace", service: "marketplace"};
const KIND_TITLE = {job: "Добавить вакансию", housing: "Добавить жильё", goods: "Добавить товар", car: "Добавить автомобиль", service: "Добавить услугу"};
const KIND_HINT = {job: "Например: Работа на заводе", housing: "Например: Квартира 2 комнаты, Каннам", goods: "Например: iPhone 13, 128 ГБ",
                   car: "Например: Hyundai Sonata 2018", service: "Например: Перевод документов"};
const kindOfItem = (x) => x.category !== "marketplace" ? x.category
  : x.goods_category === "auto" ? "car" : x.goods_category === "services" ? "service" : "goods";
const needsPhoto = (kind) => ["housing", "goods", "car"].includes(kind);
function updateFormBlocks() {
  const category = KIND_CATEGORY[formKind];
  $("#formCategory").value = category;
  showBlocks("#listingForm", category);
  document.querySelectorAll("#listingForm [data-showfor]").forEach((el) => { el.hidden = !el.dataset.showfor.split(",").includes(formKind); });
  $("#listingForm [name=title]").placeholder = KIND_HINT[formKind];
  $("#listingForm [name=d_brand]").placeholder = formKind === "car" ? "Например: Hyundai" : "Например: Samsung";
  $("#photoHint").textContent = needsPhoto(formKind) ? "Нужно хотя бы одно фото: с ним объявлениям доверяют больше." : "Фото по желанию.";
  $("#amountLabel").textContent = {job: "Зарплата от, ₩", housing: "Аренда в месяц, ₩", goods: "Цена, ₩", car: "Цена, ₩", service: "Стоимость от, ₩"}[formKind];
}
$("#priceNego").onchange = () => {
  const f = $("#listingForm");
  f.price_amount.disabled = $("#priceNego").checked;
  if ($("#priceNego").checked) { f.price_amount.value = ""; f.price.value = "Договорная"; }
  else if (f.price.value === "Договорная") f.price.value = "";
};

// ---- шаги мастера ----
const STEP_NAMES = ["Основное", "Детали", "Фото и текст", "Проверка"];
let wizStep = 1;
function showStep(n) {
  wizStep = n;
  document.querySelectorAll("#listingForm .step").forEach((el) => { el.hidden = Number(el.dataset.step) !== n; });
  $("#stepsBar").innerHTML = STEP_NAMES.map((t, i) => `<div class="${i + 1 === n ? "on" : i + 1 < n ? "done" : ""}"><i></i>${i + 1}. ${t}</div>`).join("");
  $("#prevBtn").hidden = n === 1;
  $("#nextBtn").hidden = n === 4;
  if (n === 4) renderPreview();
  $("#scroll").scrollTop = 0;
  $("#modal .sheet").scrollTop = 0;
}
function validateStep(n) {
  const f = $("#listingForm");
  if (n === 1) {
    if (f.title.value.trim().length < 3) { toast("Напишите заголовок (не короче 3 букв)"); return false; }
    if (!f.region_code.value || !f.district_id.value) { toast("Выберите регион и город или район"); return false; }
  }
  if (n === 3 && needsPhoto(formKind) && !formPhotos.length && !formFiles.length) { toast("Добавьте хотя бы одно фото"); return false; }
  return true;
}
$("#nextBtn").onclick = () => { if (validateStep(wizStep)) showStep(wizStep + 1); };
$("#prevBtn").onclick = () => showStep(wizStep - 1);
function renderPreview() {
  const f = $("#listingForm");
  const data = listingPayload(f);
  const place = f.district_id.selectedOptions[0]?.text || "";
  const first = formPhotos[0]?.thumb || formFiles[0]?.preview || null;
  const item = {...data, id: 0, city: place, cover: first, visas: data.visas || [], details: data.details || {}, promo: null};
  $("#listingPreview").innerHTML = listingCard(item);
}

// ---- черновик на устройстве: форма не пропадает, если закрыть окно ----
const draftKey = () => "khDraft:" + formKind;
let draftTimer = null;
function saveLocalDraft() {
  if (currentEditId) return;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    try {
      const data = listingPayload($("#listingForm"));
      if (data.title || data.description) {
        localStorage.setItem(draftKey(), JSON.stringify({data, step: wizStep}));
        clearTimeout(saveLocalDraft.srv);   // и на сервер: черновик переживёт смену телефона
        saveLocalDraft.srv = setTimeout(() => api("/api/me/wizard-draft", {method: "PUT", body: JSON.stringify({data, step: wizStep}), quiet: true}).catch(() => {}), 1500);
      }
    } catch (e) { /* форма ещё не готова */ }
  }, 400);
}
$("#listingForm").addEventListener("input", saveLocalDraft);
$("#listingForm").addEventListener("change", saveLocalDraft);
function renderPhotos() {
  const cells = formPhotos.map((p, i) => `<div class="ph" data-ph="${p.id}"><img src="${esc(p.thumb)}" alt="">
      ${p.status === "pending" ? '<small>на проверке</small>' : ""}${i === 0 ? "<small>обложка</small>" : ""}
      <button type="button" data-ph-del="${p.id}" aria-label="Удалить">×</button></div>`)
    .concat(formFiles.map((f, i) => `<div class="ph"><img src="${esc(f.preview)}" alt="">
      <button type="button" data-file-del="${i}" aria-label="Убрать">×</button></div>`));
  if (formPhotos.length + formFiles.length < 10) cells.push('<label class="ph add">＋<input type="file" id="photoInput" accept="image/jpeg,image/png,image/webp" multiple hidden></label>');
  $("#photoPreview").innerHTML = cells.join("");
  const input = $("#photoInput");
  if (input) input.onchange = () => {
    for (const file of input.files) {
      if (formPhotos.length + formFiles.length >= 10) break;
      formFiles.push({file, preview: URL.createObjectURL(file)});
    }
    renderPhotos();
  };
  document.querySelectorAll("[data-file-del]").forEach((b) => b.onclick = () => { formFiles.splice(Number(b.dataset.fileDel), 1); renderPhotos(); });
  document.querySelectorAll("[data-ph-del]").forEach((b) => b.onclick = () => {
    const id = Number(b.dataset.phDel);
    removedPhotos.push(id);
    formPhotos = formPhotos.filter((p) => p.id !== id);
    renderPhotos();
  });
}
async function uploadPhotos(listingId) {
  for (const id of removedPhotos) await api(`/api/listings/${listingId}/photos/${id}`, {method: "DELETE"});
  for (const f of formFiles) {
    const body = new FormData();
    body.append("file", f.file);
    await api(`/api/listings/${listingId}/photos`, {method: "POST", body});
  }
}
function resetPhotos(list) {
  formPhotos = list || []; formFiles = []; removedPhotos = [];
  renderPhotos();
}
async function openAdd(kind) {
  if (kind === "dating") return openMyDating();
  if (me?.blocked) { toast("Аккаунт ограничен" + (me.block_reason ? ": " + me.block_reason : "")); return; }
  await loadFields();
  currentEditId = null;
  formKind = kind;
  const f = $("#listingForm");
  f.reset();
  f.category.disabled = false;
  f.price_amount.disabled = false;
  await setGeo("#formGeo", me?.region_code || "", me?.district_id || "");
  $("#modalTitle").textContent = KIND_TITLE[kind];
  resetPhotos([]);
  updateFormBlocks();
  // Незаконченный черновик на этом устройстве
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(draftKey()) || "null"); } catch (e) { saved = null; }
  if (!saved) {
    try { const sd = await api("/api/me/wizard-draft", {quiet: true}); if (sd?.data && (sd.data.title || sd.data.description)) saved = sd; } catch (e) { /* без черновика */ }
  }
  $("#addSheet").classList.add("hidden");
  $("#modal").classList.remove("hidden");
  showStep(1);
  if (saved?.data && (await confirmDialog("Продолжить незаконченное объявление?"))) {
    fillForm(saved.data);
    updateFormBlocks();
    toast("Фото нужно добавить заново");
  } else if (saved) { localStorage.removeItem(draftKey()); api("/api/me/wizard-draft", {method: "DELETE", quiet: true}).catch(() => {}); }
}
function fillForm(item) {
  const f = $("#listingForm");
  const d = item.details || {};
  for (const k of ["title", "price", "description", "contact"]) f[k].value = item[k] || "";
  f.price_amount.value = item.price_amount ?? "";
  for (const k of ["salary_period", "employment", "schedule", "housing_type", "goods_category", "condition"]) f[k].value = item[k] || "";
  for (const k of ["deposit", "area_m2", "rooms"]) f[k].value = item[k] ?? "";
  for (const k of ["housing_provided", "meals_provided", "pets_allowed", "furnished", "negotiable"]) f[k].checked = Boolean(item[k]);
  for (const k of ["company", "position", "salary_max", "vacancies", "start_date", "maintenance_fee", "floor",
                   "available_from", "contract_term", "station", "brand", "model", "year", "mileage_km", "service_area",
                   "experience_years"]) f["d_" + k].value = d[k] ?? "";
  for (const k of ["transport", "experience", "delivery"]) f["d_" + k].checked = Boolean(d[k]);
  document.querySelectorAll("#listingForm [data-visas] input").forEach((c) => { c.checked = (item.visas || []).includes(c.value); });
  $("#priceNego").checked = item.price === "Договорная" && item.price_amount == null;
  f.price_amount.disabled = $("#priceNego").checked;
}
async function openEdit(item) {
  await loadFields();
  currentEditId = item.id;
  const f = $("#listingForm");
  f.reset();
  formKind = kindOfItem(item);
  f.category.value = item.category;
  f.category.disabled = true;  // раздел менять нельзя
  await setGeo("#formGeo", item.region_code || "", item.district_id || "");
  fillForm(item);
  $("#modalTitle").textContent = "Изменить объявление";
  resetPhotos((item.photos || []).filter((p) => p.status !== "removal"));
  updateFormBlocks();
  $("#modal").classList.remove("hidden");
  showStep(1);
}
function listingPayload(f) {
  const v = (n) => f[n].value.trim();
  const num = (n) => (v(n) === "" ? undefined : Number(v(n)));
  const category = KIND_CATEGORY[formKind] || f.category.value;
  const data = {category, title: v("title"), region_code: f.region_code.value, price: v("price"),
                description: v("description"), contact: v("contact"), price_amount: $("#priceNego").checked ? undefined : num("price_amount")};
  if (f.district_id.value) data.district_id = Number(f.district_id.value);
  let details = {};
  if (category === "job") {
    Object.assign(data, {salary_period: v("salary_period") || undefined, employment: v("employment") || undefined,
      schedule: v("schedule") || undefined, housing_provided: f.housing_provided.checked, meals_provided: f.meals_provided.checked,
      visas: [...document.querySelectorAll("#listingForm [data-visas] input:checked")].map((c) => c.value)});
    details = {company: v("d_company"), position: v("d_position"), salary_max: num("d_salary_max"), vacancies: num("d_vacancies"),
               start_date: v("d_start_date"), transport: f.d_transport.checked, experience: f.d_experience.checked};
  } else if (category === "housing") {
    Object.assign(data, {housing_type: v("housing_type") || undefined, deposit: num("deposit"), area_m2: num("area_m2"),
      rooms: num("rooms"), pets_allowed: f.pets_allowed.checked, furnished: f.furnished.checked});
    details = {maintenance_fee: num("d_maintenance_fee"), floor: num("d_floor"), available_from: v("d_available_from"),
               contract_term: v("d_contract_term"), station: v("d_station")};
  } else {
    const goodsCategory = formKind === "car" ? "auto" : formKind === "service" ? "services" : (v("goods_category") || undefined);
    Object.assign(data, {goods_category: goodsCategory, negotiable: f.negotiable.checked});
    if (formKind !== "service") data.condition = v("condition") || undefined;
    details = formKind === "service"
      ? {service_area: v("d_service_area"), experience_years: num("d_experience_years")}
      : {brand: v("d_brand"), delivery: f.d_delivery.checked, ...(formKind === "car"
          ? {model: v("d_model"), year: num("d_year"), mileage_km: num("d_mileage_km")} : {})};
  }
  data.details = Object.fromEntries(Object.entries(details).filter(([, x]) => x !== undefined && x !== ""));
  return Object.fromEntries(Object.entries(data).filter(([, x]) => x !== undefined));
}
async function saveListing(send) {
  const f = $("#listingForm");
  const data = listingPayload(f);
  if (send) {
    for (const n of [1, 3]) if (!validateStep(n)) { showStep(n); return; }
    if (!$("#rulesOk").checked) { toast("Отметьте, что объявление правдивое и без предоплат"); return; }
  } else if (data.title.length < 3 || !data.district_id) { toast("Для черновика укажите заголовок и город"); return; }
  try {
    let id = currentEditId;
    const wasDraft = id ? ownItems[id]?.status === "draft" : true;
    const wasLive = id ? ownItems[id]?.status === "approved" : false;
    if (id) {
      delete data.category;
      await api(`/api/listings/${id}`, {method: "PUT", body: JSON.stringify(data)});
    } else {
      data.draft = true;  // сначала черновик, чтобы загрузить фото; потом отправим на проверку
      id = (await api("/api/listings", {method: "POST", body: JSON.stringify(data)})).id;
    }
    await uploadPhotos(id);
    if (send && wasDraft) await api(`/api/listings/${id}/submit`, {method: "POST"});
    const wasEdit = Boolean(currentEditId);
    if (!wasEdit) { localStorage.removeItem(draftKey()); api("/api/me/wizard-draft", {method: "DELETE", quiet: true}).catch(() => {}); }
    currentEditId = null;
    $("#modal").classList.add("hidden");
    homeLoaded = false;
    if (send) {
      $("#doneTitle").textContent = wasLive ? "Изменения отправлены" : "Отправлено на проверку";
      $("#doneText").textContent = wasLive ? "Пока модератор проверяет, остаётся прежняя версия."
        : "Мы сообщим в Telegram, когда модератор примет решение. Объявление будет показано 30 дней.";
      $("#doneSheet").classList.remove("hidden");
    } else { toast("Черновик сохранён: Профиль → Мои объявления → Черновики"); showView("profile"); showView("mine", {push: true}); }
    loadProfile();
  } catch (err) { toast(err.message); }
}
$("#listingForm").onsubmit = (e) => { e.preventDefault(); if (wizStep < 4) { $("#nextBtn").onclick(); return; } saveListing(true); };
$("#draftBtn").onclick = () => saveListing(false);
$("#doneMine").onclick = () => { $("#doneSheet").classList.add("hidden"); showView("profile"); showView("mine", {push: true}); };
$("#doneMore").onclick = () => { $("#doneSheet").classList.add("hidden"); $("#addSheet").classList.remove("hidden"); };
$("#closeModal").onclick = () => $("#modal").classList.add("hidden");
$("#addBtn").onclick = async () => { if (await requireConsent()) $("#addSheet").classList.remove("hidden"); };
$("#closeAdd").onclick = () => $("#addSheet").classList.add("hidden");
document.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => openAdd(b.dataset.add));

// ---------- сообщество и гид ----------
let groupKind = "nation";
let groupItems = [];
function renderGroups() {
  const items = groupItems.filter((g) => (g.kind || "nation") === groupKind);
  $("#groups").innerHTML = items.length ? items.map((g) => {
    const members = g.members_count ? `<span class="members">${new Intl.NumberFormat("ru-RU").format(g.members_count)} участников</span>` : "";
    const inner = `<span class="gflag">${esc(g.flag)}</span><span class="gtext"><b>${esc(g.name)}</b><small>${esc(g.description)}</small>${members ? "<br>" + members : ""}</span><span class="chev">›</span>`;
    return g.telegram_link
      ? `<a class="grow" data-tglink href="${esc(g.telegram_link)}" target="_blank" rel="noopener">${inner}</a>`
      : `<div class="grow off">${inner}</div>`;
  }).join("") : '<div class="empty">Групп пока нет. Скоро добавим.</div>';
}
document.querySelectorAll("#groupTabs .chip").forEach((chip) => chip.addEventListener("click", () => {
  groupKind = chip.dataset.kind;
  document.querySelectorAll("#groupTabs .chip").forEach((c) => c.classList.toggle("active", c === chip));
  renderGroups();
}));
async function loadGroups() {
  try { groupItems = await api("/api/groups"); renderGroups(); }
  catch (e) { $("#groups").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
let guideTimer = null;
const articleRow = (a) => `<article class="listing convo" data-article="${a.id}"><h3>${esc(a.title)}</h3>
  <p class="muted">${esc(SECTIONS[a.section] || "")}${a.saved ? " · ★ в закладках" : ""}</p></article>`;
async function loadGuide() {
  try {
    const items = await api("/api/articles");
    $("#guideSections").innerHTML = Object.entries(SECTIONS).map(([key, title]) => {
      const count = items.filter((a) => a.section === key).length;
      return `<article class="listing convo" data-section="${key}"><h3>${esc(title)}</h3>
        <p>${count ? count + " статей" : "Материалы готовятся: статьи появятся после проверки по официальным источникам"}</p></article>`;
    }).join("");
    let marks = [];
    try { marks = await api("/api/me/bookmarks", {quiet: true}); } catch (e) { /* без входа закладок нет */ }
    $("#guideMarks").innerHTML = marks.length ? `<h4 class="grp">Мои закладки</h4><div class="listings">${marks.map(articleRow).join("")}</div>` : "";
  } catch (e) { $("#guideSections").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
$("#guideSearch").addEventListener("input", () => {
  clearTimeout(guideTimer);
  guideTimer = setTimeout(async () => {
    const q = $("#guideSearch").value.trim();
    const box = $("#guideFound");
    if (!q) { box.innerHTML = ""; $("#guideSections").hidden = false; return; }
    $("#guideSections").hidden = true;
    try {
      const rows = await api("/api/articles?q=" + encodeURIComponent(q), {quiet: true});
      box.innerHTML = rows.length ? rows.map(articleRow).join("") : '<div class="empty">Ничего не найдено в проверенных статьях</div>';
    } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }, 300);
});
async function openSection(key) {
  const items = (await api("/api/articles")).filter((a) => a.section === key);
  $("#articleTitle").textContent = SECTIONS[key] || "Раздел";
  $("#articleBody").innerHTML = items.length ? items.map((a) => `
    <article class="listing convo" data-article="${a.id}"><h3>${esc(a.title)}</h3>
    <p class="muted">Проверено: ${esc(a.checked_at ? a.checked_at.slice(0, 10) : "—")}</p></article>`).join("")
    : '<div class="empty">Статьи в этом разделе пока готовятся.</div>';
  showView("article", {push: true});
}
let currentArticle = null;
async function openArticle(id) {
  const a = await api(`/api/articles/${id}`);
  currentArticle = a.id;
  let saved = false;
  try { saved = (await api("/api/me/bookmarks", {quiet: true})).some((x) => x.id === a.id); } catch (e) { /* без входа закладок нет */ }
  $("#articleTitle").innerHTML = `${esc(a.title)} <button class="mark" id="markBtn" aria-label="В закладки">${saved ? "★" : "☆"}</button>`;
  $("#markBtn").onclick = async () => {
    try { const r = await api(`/api/articles/${currentArticle}/bookmark`, {method: "POST"}); $("#markBtn").textContent = r.saved ? "★" : "☆"; toast(r.saved ? "Добавлено в закладки" : "Убрано из закладок"); }
    catch (err) { toast(err.message); }
  };
  $("#articleBody").innerHTML = `<article class="listing"><p class="muted">Источник: <a href="${esc(a.source_url)}" target="_blank" rel="noopener">${esc(a.source_url)}</a></p>
    <p>${esc(a.body).replace(/\n/g, "<br>")}</p><p class="muted">Дата проверки: ${esc((a.checked_at || "").slice(0, 10))}</p></article>`;
  $("#scroll").scrollTop = 0;
}
$("#articleBack").onclick = goBack;
$("#detailBack").onclick = goBack;

// ---------- продвижение (ТЗ № 2): оплата Telegram Stars ----------
let servicesFor = null;     // объявление, для которого открыта витрина
let promoCode = "";        // применённый промокод
let promoPrices = {};      // service code -> цена со скидкой
const SAMPLE = {bump: ["⬆ Поднято", ""], vip: ["VIP", ""], pin: ["📌 Закреплено", ""], highlight: ["★ Выделено", "hl"]};
function renderServices(data) {
  $("#serviceFor").textContent = "Для объявления: " + data.title;
  $("#serviceList").innerHTML = data.options.map((o) => {
    const price = promoPrices[o.code] != null && promoPrices[o.code] !== o.stars
      ? `<s>${o.stars}</s>${promoPrices[o.code]} ⭐` : `${o.stars} ⭐`;
    const [badge, cls] = SAMPLE[o.kind] || ["", ""];
    const until = o.active_until ? ` до ${fmtDate(o.active_until).split(",")[0]}` : "";
    return `<article class="listing svc"><div class="svc-head"><h3>${esc(o.title)}</h3><span class="svc-price">${price}</span></div>
      <p>${esc(o.what)}${o.days > 1 ? " · " + o.days + " дн." : ""}</p>
      <div class="sample ${cls}"><span class="promo ${o.kind}" style="margin:0">${badge}</span> так выглядит в ленте</div>
      ${o.available ? `<div class="actions"><button data-buy="${esc(o.code)}" data-listing="${servicesFor}">Оплатить ${promoPrices[o.code] ?? o.stars} ⭐</button></div>`
        : `<p class="why">${esc(o.reason)}${until}</p>`}</article>`;
  }).join("") || '<div class="empty">Для этого раздела услуг пока нет</div>';
}
let servicesData = null;
async function openServices(listingId, category) {
  servicesFor = listingId;
  promoCode = ""; promoPrices = {};
  $("#promoInput").value = ""; $("#promoNote").textContent = "";
  servicesData = await api(`/api/listings/${listingId}/services`);
  renderServices(servicesData);
  $("#services").classList.remove("hidden");
}
$("#promoApply").onclick = async () => {
  const code = $("#promoInput").value.trim();
  if (!code || !servicesData) return;
  promoPrices = {};
  let applied = "";
  for (const o of servicesData.options.filter((x) => x.available)) {
    try { const r = await api(`/api/promo/check?code=${encodeURIComponent(code)}&service_code=${encodeURIComponent(o.code)}`, {quiet: true}); promoPrices[o.code] = r.stars; applied = r.code; }
    catch (e) { $("#promoNote").textContent = e.message; promoCode = ""; renderServices(servicesData); return; }
  }
  promoCode = applied;
  $("#promoNote").textContent = applied ? `✔ Промокод ${applied} применён: цены ниже уже со скидкой` : "";
  renderServices(servicesData);
};
async function buy(listingId, code) {
  const r = await api("/api/payments/invoice", {method: "POST", body: JSON.stringify({listing_id: Number(listingId), service_code: code, promo_code: promoCode})});
  if (!tg?.openInvoice) { toast("Оплата открывается только внутри Telegram"); return; }
  tg.openInvoice(r.invoice_url, (status) => {
    $("#services").classList.add("hidden");
    if (status === "paid") toast("Оплата прошла. Услуга активируется");
    else if (status === "failed") toast("Оплата не прошла");
    loadProfile();
  });
}
$("#closeServices").onclick = () => $("#services").classList.add("hidden");

// ---------- знакомства ----------
let datingMode = "feed";
let pendingSelfie = null;
let datingActive = false;
$("#dActive").onclick = () => { datingActive = !datingActive; $("#dActive").classList.toggle("on", datingActive); loadDating(); };
async function loadDating() {
  const box = $("#datingList");
  box.innerHTML = '<div class="skeleton"></div>';
  await loadGeo();
  const reg = $("#dRegion");
  if (!reg.options.length) reg.innerHTML = '<option value="">Вся Корея</option>' + (geoData || []).map((r) => `<option value="${r.code}">${esc(r.name)}</option>`).join("");
  const params = new URLSearchParams();
  if (datingGender) params.set("gender", datingGender);
  const age = $("#dAge").value;
  if (age) { const [lo, hi] = age.split("-"); params.set("age_min", lo); params.set("age_max", hi); }
  if (reg.value) params.set("region", reg.value);
  if (datingActive) params.set("active", "true");
  try {
    const likes = await api("/api/dating/likes-me", {quiet: true}).catch(() => ({count: 0, items: []}));
    $("#dLikesN").textContent = likes.count;
    $("#dLikes").classList.toggle("on", datingMode === "likes");
    const items = datingMode === "likes" ? likes.items : await api("/api/dating?" + params);
    box.innerHTML = items.length ? items.map((p) => `
      <article class="dcard" data-profile="${p.id}">
        <img src="${esc(p.photo_url)}" alt="" loading="lazy">
        <button class="dh" data-act="like" aria-label="Нравится">♡</button>
        <button class="dr" data-act="dreport" aria-label="Пожаловаться">⚑</button>
        <div class="dinfo"><b>${p.name ? esc(p.name) + ", " : ""}${p.age}${p.name ? "" : " лет"}${p.verified ? " ✔" : ""}</b><span>📍 ${esc(p.city)}</span></div>
      </article>`).join("")
      : `<div class="empty">${datingMode === "likes" ? "Пока никто не лайкнул. Загляните позже." : "Анкет по этим условиям нет. Измените возраст или регион."}</div>`;
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
$("#dAge").onchange = () => { datingMode = "feed"; loadDating(); };
$("#dRegion").onchange = () => { datingMode = "feed"; loadDating(); };
$("#dLikes").onclick = () => { datingMode = datingMode === "likes" ? "feed" : "likes"; loadDating(); };
document.querySelectorAll("#datingChips .chip").forEach((chip) => chip.addEventListener("click", () => {
  datingGender = chip.dataset.gender;
  datingMode = "feed";
  document.querySelectorAll("#datingChips .chip").forEach((c) => c.classList.toggle("active", c === chip));
  loadDating();
}));
async function openMyDating() {
  if (!(await requireConsent())) return;
  const f = $("#datingForm");
  await setGeo("#datingGeo", me?.region_code || "", me?.district_id || "");
  try {
    const mine = await api("/api/dating/me");
    if (mine) {
      f.birth_year.value = mine.age ? new Date().getFullYear() - mine.age : "";
      f.gender.value = mine.gender;
      f.first_name.value = mine.name || "";
      await setGeo("#datingGeo", mine.region_code || "", mine.district_id || "");
      toast("Статус анкеты: " + ({pending: "на проверке", published: "опубликована", rejected: "отклонена"}[mine.status] || mine.status) + (mine.status === "rejected" && mine.note ? ". Причина: " + mine.note : "") + (mine.paused ? ". Анкета на паузе" : ""));
      $("#datingPause").hidden = false;
      $("#datingPause").textContent = mine.paused ? "▶ Вернуть анкету в поиск" : "⏸ Поставить анкету на паузу";
      $("#datingPause").dataset.on = mine.paused ? "" : "1";
    }
  } catch (e) { toast(e.message); }
  $("#addSheet").classList.add("hidden");
  $("#datingModal").classList.remove("hidden");
}
$("#myDatingBtn").onclick = openMyDating;
$("#datingPause").onclick = async () => {
  try { const r = await api("/api/dating/pause", {method: "POST", body: JSON.stringify({on: $("#datingPause").dataset.on === "1"})}); toast(r.paused ? "Анкета на паузе: её никто не видит" : "Анкета снова в поиске"); $("#datingModal").classList.add("hidden"); loadDating(); }
  catch (err) { toast(err.message); }
};
$("#closeDating").onclick = () => $("#datingModal").classList.add("hidden");
$("#datingForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/dating/profile", {method: "PUT", body: JSON.stringify({
      birth_year: Number(f.birth_year.value), gender: f.gender.value, region_code: f.region_code.value,
      district_id: f.district_id.value ? Number(f.district_id.value) : null, bio: f.bio.value.trim(),
      first_name: f.first_name.value.trim()})});
    if (f.selfie.files[0]) pendingSelfie = f.selfie.files[0];
    if (f.photo.files[0]) {
      const data = new FormData();
      data.append("file", f.photo.files[0]);
      const headers = inTelegram ? {"X-Telegram-Init-Data": tg.initData}
        : {"X-Web-Session": localStorage.getItem("webSession") || ""};
      const r = await fetch("/api/dating/photo", {method: "POST", body: data, headers});
      if (!r.ok) throw new Error((await r.json()).detail || "Не удалось загрузить фото");
    }
    if (pendingSelfie) {
      const data = new FormData();
      data.append("file", pendingSelfie);
      const headers = inTelegram ? {"X-Telegram-Init-Data": tg.initData} : {"X-Web-Session": localStorage.getItem("webSession") || ""};
      const r = await fetch("/api/dating/selfie", {method: "POST", body: data, headers});
      pendingSelfie = null;
      if (!r.ok) throw new Error((await r.json()).detail || "Не удалось загрузить фото-подтверждение");
    }
    $("#datingModal").classList.add("hidden");
    toast("Анкета отправлена на проверку");
  } catch (err) { toast(err.message); }
};

// ---------- общий обработчик нажатий ----------
document.addEventListener("click", async (e) => {
  const tgLink = e.target.closest("[data-tglink]");
  if (tgLink && tg?.openTelegramLink) { e.preventDefault(); tg.openTelegramLink(tgLink.href); return; }

  const buyBtn = e.target.closest("[data-buy]");
  if (buyBtn) { try { await buy(buyBtn.dataset.listing, buyBtn.dataset.buy); } catch (err) { toast(err.message); } return; }

  const btn = e.target.closest("[data-act]");
  if (btn) {
    const owner = btn.closest("[data-id],[data-profile]");
    const id = owner?.dataset.id;
    try {
      if (btn.dataset.act === "fav") {
        if (!(await requireConsent())) return;
        const r = await api(`/api/favorites/${id}`, {method: "POST"});
        r.favorited ? favIds.add(Number(id)) : favIds.delete(Number(id));
        document.querySelectorAll(`[data-id="${id}"] [data-act="fav"]`).forEach((b) => {
          if (b.classList.contains("fav")) { b.textContent = r.favorited ? "♥" : "♡"; b.classList.toggle("on", r.favorited); }
          else b.textContent = b.classList.contains("rnd") ? (r.favorited ? "♥" : "♡") : (r.favorited ? "♥ В избранном" : "♡ В избранное");
        });
        toast(r.favorited ? "Добавлено в избранное" : "Убрано из избранного");
      }
      if (btn.dataset.act === "chat") {
        if (!(await requireConsent())) return;
        const c = await api(`/api/listings/${id}/conversation`, {method: "POST"});
        openChat(c.id, owner.querySelector("h1,h3")?.textContent);
      }
      if (btn.dataset.act === "apply") {
        if (!(await requireConsent())) return;
        applyListingId = id;
        $("#applyForm").contact.value = me?.last_contact || "";
        $("#applyResume").hidden = true;
        api("/api/me/resume", {quiet: true}).then((cv) => { if (cv && cv.status === "approved") { window.__cv = cv; $("#applyResume").hidden = false; } }).catch(() => {});
        $("#applyModal").classList.remove("hidden");
      }
      if (btn.dataset.act === "share") shareListing(id, owner.querySelector("h1,h3")?.textContent || "KOREA HUB");
      if (btn.dataset.act === "like") {
        if (!(await requireConsent())) return;
        const r = await api(`/api/dating/${owner.dataset.profile}/like`, {method: "POST"});
        if (r.match) { toast("Взаимная симпатия! Можно писать"); openChat(r.conversation_id, "Знакомство"); }
        else { owner.remove(); toast("Отправлено"); }
      }
      if (btn.dataset.act === "dreport") {
        if (!(await requireConsent())) return;
        const reason = await askReason();
        if (!reason) return;
        await api("/api/reports", {method: "POST", body: JSON.stringify({target_type: "dating", target_id: owner.dataset.profile, reason})});
        toast("Жалоба отправлена. Спасибо!");
      }
      if (btn.dataset.act === "report") {
        if (!(await requireConsent())) return;
        const reason = await askReason();
        if (!reason) return;
        await api("/api/reports", {method: "POST", body: JSON.stringify({target_type: "listing", target_id: id, reason})});
        toast("Жалоба отправлена. Спасибо!");
      }
    } catch (err) { toast(err.message); }
    return;
  }

  const menu = e.target.closest("[data-menu]");
  if (menu) { try { await openMenu(menu.dataset.menu); } catch (err) { toast(err.message); } return; }
  const unblock = e.target.closest("[data-unblock]");
  if (unblock) {
    try { await api(`/api/me/blocks/${unblock.dataset.unblock}`, {method: "DELETE"}); toast("Пользователь разблокирован"); openMenu("blocks"); }
    catch (err) { toast(err.message); }
    return;
  }
  const os = e.target.closest("[data-opensearch]");
  if (os) {
    const sp = window.__searches?.[os.dataset.opensearch]?.params || {};
    const {region, ...rest} = sp;
    filters = {...DEFAULT_FILTERS, ...rest, region_code: region || ""};
    $("#search").value = filters.q || "";
    homeLoaded = false; showView("home"); reloadCatalog();
    return;
  }
  const ps = e.target.closest("[data-pausesearch]");
  if (ps) {
    try { await api(`/api/me/searches/${ps.dataset.pausesearch}/pause`, {method: "POST", body: JSON.stringify({on: ps.dataset.on === "1"})}); openMenu("searches"); }
    catch (err) { toast(err.message); }
    return;
  }
  const del = e.target.closest("[data-delsearch]");
  if (del) {
    try { await api(`/api/me/searches/${del.dataset.delsearch}`, {method: "DELETE"}); openMenu("searches"); }
    catch (err) { toast(err.message); }
    return;
  }
  const rc = e.target.closest("[data-receipt]");
  if (rc) { try { await showReceipt(rc.dataset.receipt); } catch (err) { toast(err.message); } return; }
  const rp = e.target.closest("[data-replyreview]");
  if (rp) {
    const text = await askText("Ответ на отзыв", "Коротко и вежливо. Без телефонов и ссылок");
    if (!text) return;
    try { await api(`/api/reviews/${rp.dataset.replyreview}/reply`, {method: "POST", body: JSON.stringify({text})}); toast("Ответ опубликован"); rp.remove(); } catch (err) { toast(err.message); }
    return;
  }
  const rr = e.target.closest("[data-reportreview]");
  if (rr) {
    if (!(await requireConsent())) return;
    const reason = await askReason();
    if (!reason) return;
    try { await api("/api/reports", {method: "POST", body: JSON.stringify({target_type: "review", target_id: rr.dataset.reportreview, reason})}); toast("Жалоба отправлена. Спасибо!"); }
    catch (err) { toast(err.message); }
    return;
  }
  const rv = e.target.closest("[data-review]");
  if (rv) {
    if (!(await requireConsent())) return;
    reviewFor = +rv.dataset.review; reviewRating = 0; reviewTags = new Set(); $("#reviewText").value = ""; drawStars(); drawTags();
    $("#reviewSheet").classList.remove("hidden");
    return;
  }
  const dr = e.target.closest("[data-delreview]");
  if (dr) {
    if (!(await confirmDialog("Удалить ваш отзыв?"))) return;
    try { await api(`/api/reviews/${dr.dataset.delreview}`, {method: "DELETE"}); toast("Отзыв удалён"); const id = $("#detailBody article")?.dataset.id; if (id) openDetail(+id, false); }
    catch (err) { toast(err.message); }
    return;
  }
  const au = e.target.closest("[data-author]");
  if (au) {
    try {
      const [r, rev] = await Promise.all([api(`/api/authors/${au.dataset.author}`), api(`/api/authors/${au.dataset.author}/reviews`, {quiet: true}).catch(() => null)]);
      openSub("author");
      const c = r.card;
      $("#subBody").innerHTML = `<p class="author">${c.verified ? `<b class="badge-ok">✔ Проверенный работодатель${c.company ? ": " + esc(c.company) : ""}</b> · ` : ""}На сайте с ${c.since || "—"} · Активных: ${c.active}${rev?.reviews ? ` · <b class="rating">★ ${rev.rating}</b> (${rev.reviews})` : ""}</p>` +
        (rev?.items?.length ? `<h3 class="dsub">Отзывы</h3>` + rev.items.map((v) => `<div class="review"><b class="rating">${"★".repeat(v.rating)}</b> ${esc(v.name)} · ${esc(v.at)}${(v.tags || []).map((t) => `<span class="tagchip">${esc(t)}</span>`).join("")}${v.text ? `<p>${esc(v.text)}</p>` : ""}${v.reply ? `<p class="muted"><b>Ответ автора:</b> ${esc(v.reply)}</p>` : ""}${v.can_reply ? `<button class="linkbtn" data-replyreview="${v.id}">Ответить</button>` : ""}<button class="linkbtn" data-reportreview="${v.id}">⚑</button></div>`).join("") : "") +
        r.items.map(listingCard).join("");
    } catch (err) { toast(err.message); }
    return;
  }
  const wd = e.target.closest("[data-withdraw]");
  if (wd) {
    if (!(await confirmDialog("Отозвать отклик? Работодатель его больше не увидит."))) return;
    try { await api(`/api/applications/${wd.dataset.withdraw}/withdraw`, {method: "POST"}); toast("Отклик отозван"); openMenu("applications"); }
    catch (err) { toast(err.message); }
    return;
  }
  const st = e.target.closest("[data-appstatus]");
  if (st) {
    try {
      const reply = await askText(st.dataset.appstatus === "invited" ? "Что передать кандидату?" : "Что ответить кандидату?",
                                  st.dataset.appstatus === "invited" ? "Когда и куда прийти" : "Например: место уже занято");
      if (reply === null) return;
      await api(`/api/applications/${st.dataset.appid}`, {method: "PUT", body: JSON.stringify({status: st.dataset.appstatus, reply})});
      const badge = st.closest("[data-app]").querySelector(".status");
      badge.textContent = APP_STATUS[st.dataset.appstatus];
      badge.className = "status " + (st.dataset.appstatus === "invited" ? "approved" : "rejected");
      toast("Кандидат уведомлён");
    } catch (err) { toast(err.message); }
    return;
  }
  const tk = e.target.closest("[data-ticket]");
  if (tk) { try { await openTicket(tk.dataset.ticket); } catch (err) { toast(err.message); } return; }
  const own = e.target.closest("[data-own]");
  if (own) {
    const cardEl = own.closest("[data-id]");
    const id = cardEl?.dataset.id;
    const item = ownItems[id];
    try {
      const act = own.dataset.own;
      if (act === "employer-apply") { $("#employerModal").classList.remove("hidden"); return; }
      if (act === "edit") { openEdit(item); return; }
      if (act === "reserve") { await api(`/api/listings/${id}/reserve`, {method: "POST", body: JSON.stringify({on: !item.reserved})}); toast(item.reserved ? "Резерв снят" : "Отмечено: зарезервировано"); homeLoaded = false; loadProfile(); return; }
      if (act === "stats") {
        const st = await api(`/api/listings/${id}/stats`);
        let line = cardEl.querySelector(".statline");
        if (!line) { line = document.createElement("p"); line.className = "meta-line statline"; cardEl.appendChild(line); }
        line.textContent = `👁 ${st.views} · ♥ в избранном ${st.favorites} · 💬 чатов ${st.chats}` + (st.applications ? ` · 📨 откликов ${st.applications}` : "");
        return;
      }
      if (act === "appeal") {
        const text = await askText("Почему решение неверно?", "Объясните, что изменилось или что не учли");
        if (!text) return;
        await api("/api/tickets", {method: "POST", body: JSON.stringify({kind: "appeal", listing_id: +id, text})});
        toast("Обращение отправлено. Ответ придёт в уведомления");
        return;
      }
      if (act === "applicants") { await showApplicants(id); return; }
      if (act === "promo") { await openServices(id, item.category); return; }
      if (act === "archive" && !(await confirmDialog("Удалить объявление? Его не будет видно другим."))) return;
      const done = await api(`/api/listings/${id}/${{close: "close", sold: "sold", reopen: "reopen", archive: "archive", renew: "renew", submit: "submit"}[act]}`, {method: "POST"});
      toast(act === "renew" && done?.expires_at ? "Продлено до " + new Date(done.expires_at).toLocaleDateString("ru-RU")
        : act === "archive" ? "Объявление удалено" : "Готово");
      homeLoaded = false;
      loadProfile();
    } catch (err) { toast(err.message); }
    return;
  }

  const convo = e.target.closest("[data-chat]");
  if (convo) { openChat(convo.dataset.chat, convo.dataset.title); return; }
  const section = e.target.closest("[data-section]");
  if (section) { openSection(section.dataset.section); return; }
  const article = e.target.closest("[data-article]");
  if (article) { openArticle(article.dataset.article); return; }
  const open = e.target.closest("[data-open]");
  if (open && !e.target.closest(".cmp, .cmpbar")) { openDetail(open.dataset.open); return; }
});

document.querySelectorAll(".bottom [data-view], .tiles [data-view]").forEach((b) => b.onclick = () => showView(b.dataset.view));

// ---------- согласие, город, работодатель ----------
$("#consentOk").onclick = async () => {
  if (!$("#consentRules").checked || !$("#consentPrivacy").checked) { toast("Отметьте оба пункта, чтобы продолжить"); return; }
  try {
    await api("/api/me/consent", {method: "POST", body: JSON.stringify({accept: true})});
    $("#consent").classList.add("hidden");
    toast("Спасибо! Теперь можно пользоваться приложением");
  } catch (err) { toast(err.message); }
};
function showCityChip() { $("#cityChipText").textContent = (me?.city || "Город").split(",")[0]; }
$("#cityChip").onclick = () => openCity();
$("#langBtn").onclick = () => toast("Сейчас доступен только русский язык");
function showBell(n) { $("#bellBadge").textContent = n > 99 ? "99+" : n; $("#bellBadge").hidden = !n; }
$("#bellBtn").onclick = async () => { if (await requireConsent()) showView("notifs", {push: true}); };
async function loadNotifs() {
  const box = $("#notifList");
  box.innerHTML = '<div class="skeleton"></div>';
  try {
    const r = await api("/api/me/notifications");
    box.innerHTML = r.items.length ? r.items.map((n) => `<article class="listing nitem ${n.read ? "" : "unread"}">
      <small>${esc(n.label)} · ${esc(fmtDate(n.created_at))}</small><p>${esc(n.text)}</p></article>`).join("")
      : '<div class="empty">Пока ничего нового.<br>Здесь появятся сообщения, отклики и решения модератора.</div>';
    if (r.unread) { await api("/api/me/notifications/read", {method: "POST", body: JSON.stringify({})}); showBell(0); }
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
$("#readAll").onclick = async () => {
  try { await api("/api/me/notifications/read", {method: "POST", body: JSON.stringify({})}); showBell(0); loadNotifs(); }
  catch (err) { toast(err.message); }
};
async function openCity() {
  await setGeo("#cityGeo", me?.region_code || "", me?.district_id || "");
  $("#cityModal").classList.remove("hidden");
}
$("#cityBtn").onclick = openCity;
$("#closeCity").onclick = () => $("#cityModal").classList.add("hidden");
$("#cityForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const f = e.target;
    await api("/api/me/settings", {method: "PUT", body: JSON.stringify({
      region_code: f.region_code.value, district_id: f.district_id.value ? Number(f.district_id.value) : null})});
    $("#cityModal").classList.add("hidden");
    toast("Город сохранён");
    await loadMe(); showCityChip();
    loadProfile();
    if (currentView === "sub" && currentSubKey === "settings") openMenu("settings");
  } catch (err) { toast(err.message); }
};
$("#closeEmployer").onclick = () => $("#employerModal").classList.add("hidden");
$("#employerForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/me/employer", {method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(e.target)))});
    $("#employerModal").classList.add("hidden");
    toast("Заявка отправлена на проверку");
    me.employer_status = "pending";
    openMenu("employer");
  } catch (err) { toast(err.message); }
};

// ---------- тема Telegram ----------
function applyTelegramTheme() {
  // Фирменные цвета приложения не меняются под тему Telegram (дизайн по макету); подстраиваем только окно Telegram
  try { tg?.setBackgroundColor?.("#f3f6fb"); tg?.setHeaderColor?.("#ffffff"); tg?.setBottomBarColor?.("#ffffff"); } catch (e) { /* старая версия */ }
}
applyTelegramTheme();
tg?.onEvent?.("themeChanged", applyTelegramTheme);

// ---------- потянуть вниз, чтобы обновить ----------
{
  const box = $("#scroll");
  const ind = document.createElement("div"); ind.className = "ptr"; ind.textContent = "Отпустите, чтобы обновить";
  box.parentNode.insertBefore(ind, box);
  let y0 = null;
  box.addEventListener("touchstart", (e) => { y0 = box.scrollTop === 0 && e.touches.length === 1 ? e.touches[0].clientY : null; }, {passive: true});
  box.addEventListener("touchmove", (e) => { if (y0 !== null) ind.classList.toggle("show", e.touches[0].clientY - y0 > 80); }, {passive: true});
  box.addEventListener("touchend", (e) => {
    const pulled = y0 !== null && e.changedTouches[0].clientY - y0 > 80;
    y0 = null; ind.classList.remove("show");
    if (!pulled || currentView === "chat") return;
    const refresh = {home: reloadCatalog, saved: loadFavorites, chats: loadConversations, profile: loadProfile, mine: loadProfile, notifs: loadNotifs, dating: loadDating, community: loadGroups, guide: loadGuide}[currentView];
    if (refresh) { refresh(); toast("Обновлено"); }
  }, {passive: true});
}
// ---------- старт ----------
setTimeout(showWelcomeOnce, 1200);
// Офлайн: страница-оболочка и скрипты кэшируются, чтобы приложение открывалось и при плохой связи
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
function openFromLink() {
  const params = new URLSearchParams(location.search);
  const section = params.get("section");
  if (["job", "housing", "marketplace"].includes(section)) { filters.category = section; return false; }
  if (["dating", "community", "guide"].includes(section)) { showView(section); return true; }
  const listing = params.get("listing");
  if (listing && /^\d+$/.test(listing)) { openDetail(listing, false); return true; }
  return false;
}
if (!inTelegram) $("#logoutItem").hidden = !localStorage.getItem("webSession");
const openedFromLink = openFromLink();
updateFilterUi();
loadFields().then(() => loadCatalog());  // подписи полей нужны карточкам с первой отрисовки
loadGeo();
loadFavIds().then(() => {
  document.querySelectorAll("#listings .fav").forEach((b) => {
    const id = Number(b.closest("[data-id]")?.dataset.id);
    if (favIds.has(id)) { b.textContent = "♥"; b.classList.add("on"); }
  });
});
api("/api/me", {quiet: true})
  .then((user) => { me = user; showCityChip(); showBell(user.unread || 0); if (!me.consent) $("#consent").classList.remove("hidden"); })
  .catch(() => { if (!inTelegram) $("#loginTop").hidden = false; });

// ---------- страница не двигается ----------
// Страница целиком неподвижна: прокручиваются только внутренние области, а когда прокрутка
// доходит до края, остаток жеста не «тянет» всё приложение (резинка в iOS и сворачивание в Telegram).
(() => {
  let startY = 0;
  const scroller = (el) => {
    for (; el && el !== document.body; el = el.parentElement) {
      const o = getComputedStyle(el).overflowY;
      if ((o === "auto" || o === "scroll") && el.scrollHeight > el.clientHeight) return el;
    }
    return null;
  };
  document.addEventListener("touchstart", (e) => {
    startY = e.touches[0].clientY;
    const el = scroller(e.target);
    if (!el) return;
    if (el.scrollTop <= 0) el.scrollTop = 1;
    else if (el.scrollTop + el.clientHeight >= el.scrollHeight) el.scrollTop = el.scrollHeight - el.clientHeight - 1;
  }, {passive: true});
  document.addEventListener("touchmove", (e) => {
    if (e.touches.length > 1) return;  // щипок оставляем системе
    if (e.target.closest("textarea, input, select")) return;
    const el = scroller(e.target);
    if (!el) { e.preventDefault(); return; }
    const dy = e.touches[0].clientY - startY;
    if ((dy > 0 && el.scrollTop <= 0) || (dy < 0 && el.scrollTop + el.clientHeight >= el.scrollHeight)) e.preventDefault();
  }, {passive: false});
})();


// ---------- кабинет и окна ----------
document.querySelectorAll("[data-back]").forEach((b) => b.onclick = goBack);
$("#profileAdd").onclick = () => $("#addBtn").onclick();
// Окно закрывается нажатием на затемнённый фон, клавишей Esc и кнопкой «Назад» Telegram.
// Обязательное окно согласия и страница создания объявления так не закрываются.
const modalOpen = () => [...document.querySelectorAll(".modal:not(.hidden):not(.page):not(#consent):not(#webLogin)")].pop();
function closeModal(el) {
  const closer = el.querySelector(".sheet-top button");
  if (closer) closer.click(); else el.classList.add("hidden");
}
document.addEventListener("click", (e) => {
  if (e.target.classList?.contains("modal") && !e.target.classList.contains("page") && e.target.id !== "consent") closeModal(e.target);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { const m = modalOpen(); if (m) closeModal(m); } });

// Ссылки на правила открываются во внешнем браузере/окне Telegram, а не заменяют приложение
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-legal]");
  if (!a) return;
  e.preventDefault();
  const url = location.origin + a.getAttribute("href");
  if (tg?.openLink) tg.openLink(url); else window.open(url, "_blank", "noopener");
});
