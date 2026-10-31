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
const ALL_VIEWS = [...MAIN_VIEWS, "detail", "chat", "article"];

let currentChat = null;
let chatTimer = null;
let lastMessageKey = "";
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
async function api(path, options = {}) {
  const headers = {};
  if (inTelegram) headers["X-Telegram-Init-Data"] = tg.initData;
  else if (localStorage.getItem("webSession")) headers["X-Web-Session"] = localStorage.getItem("webSession");
  if (options.body && typeof options.body === "string") headers["Content-Type"] = "application/json";
  const r = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}});
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (r.status === 401 && !inTelegram && !options.quiet) showWebLogin();
  if (!r.ok) throw new Error(data?.detail || "Ошибка сервера (" + r.status + ")");
  return data;
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.style.display = "none"), 3000);
  tg?.HapticFeedback?.notificationOccurred("success");
}
const fmtDate = (s) => (s ? new Date(s.replace(" ", "T")).toLocaleString("ru-RU") : "");
const fmtKRW = (n) => new Intl.NumberFormat("ru-RU").format(n) + " ₩";

function confirmDialog(message) {
  if (tg?.showConfirm) return new Promise((resolve) => tg.showConfirm(message, resolve));
  return Promise.resolve(confirm(message));
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
  const parent = name === "detail" || name === "article" ? backStack[backStack.length - 1] : name;
  const navName = name === "chat" ? "chats" : (NAV_VIEWS.includes(parent) ? parent : "");
  document.querySelectorAll(".bottom [data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === navName));
  if (name !== "chat") clearInterval(chatTimer);
  if (NAV_VIEWS.includes(name) && backStack.length === 0) tg?.BackButton?.hide(); else tg?.BackButton?.show();
  if (name === "home" && !homeLoaded) loadCatalog();
  if (name === "saved") loadFavorites();
  if (name === "chats") loadConversations();
  if (name === "profile") loadProfile();
  if (name === "community") loadGroups();
  if (name === "guide") loadGuide();
  if (name === "dating") loadDating();
  box.scrollTop = opts.restore ? (scrollPos[name] || 0) : 0;
}
function goBack() {
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
const promoBadge = (x) => (x.promo ? `<span class="promo ${x.promo}">${PROMO[x.promo] || ""}</span>` : "");
function priceText(x) {
  if (x.price_amount == null) return x.price || "Цена договорная";
  let text = fmtKRW(x.price_amount);
  if (x.category === "job" && x.details?.salary_max) text = new Intl.NumberFormat("ru-RU").format(x.price_amount) + " – " + fmtKRW(x.details.salary_max);
  const suffix = x.category === "job" ? labelOf("salary_period", x.salary_period || "month") : x.category === "housing" ? "в месяц" : "";
  return [text, suffix, x.price].filter(Boolean).join(" ");
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
      ["Состояние", x.condition && labelOf("condition", x.condition)], ["Бренд", d.brand],
      ["Торг", yes(x.negotiable, "Возможен", "Нет")], ["Доставка", yes(d.delivery, "Есть", "Нет")]);
  }
  const html = rows.filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== false)
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  return html ? `<dl class="specs">${html}</dl>` : "";
}
function listingCard(x) {
  const fav = favIds.has(x.id);
  return `<article class="listing ${x.promo === "highlight" ? "hl" : ""}" data-id="${x.id}" data-cat="${x.category}" data-open="${x.id}">
    ${x.cover ? `<img class="cover" src="${esc(x.cover)}" alt="" loading="lazy">` : ""}
    ${promoBadge(x)}
    <h3>${esc(x.title)}</h3>
    <div class="meta">📍 ${esc(x.city)} · ${esc(CAT[x.category] || x.category)}</div>
    <div class="price">${esc(priceText(x))}</div>
    <div class="pills">${pills(x)}</div>
    <button class="fav ${fav ? "on" : ""}" data-act="fav" aria-label="В избранное">${fav ? "♥" : "♡"}</button>
  </article>`;
}

// ---------- каталог: поиск, фильтры, страницы ----------
const DEFAULT_FILTERS = {q: "", category: "", region_code: "", district_id: "", price_min: "", price_max: "", sort: "new",
                         housing_provided: false, meals_provided: false, condition: "", rooms_min: "",
                         visa: "", schedule: "", employment: "", housing_type: "", deposit_max: "", area_min: "",
                         pets_allowed: false, furnished: false, goods_category: ""};
const SECTION_FILTERS = ["housing_provided", "meals_provided", "condition", "rooms_min", "visa", "schedule", "employment",
                         "housing_type", "deposit_max", "area_min", "pets_allowed", "furnished", "goods_category"];
let filters = {...DEFAULT_FILTERS};
const page = {offset: 0, loading: false};

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
}

async function loadCatalog(append = false) {
  if (page.loading) return;
  page.loading = true;
  const box = $("#listings");
  if (!append) {
    page.offset = 0;
    $("#moreBtn").hidden = true;
    box.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  }
  try {
    const r = await api("/api/catalog?" + catalogParams(page.offset), {quiet: true});
    const html = r.items.map(listingCard).join("");
    if (append) box.insertAdjacentHTML("beforeend", html);
    else box.innerHTML = html || `<div class="empty">${activeFilterCount() || filters.q || filters.category
      ? "Ничего не нашли. Измените запрос или сбросьте фильтры." : "Пока нет объявлений. Добавьте первое кнопкой «+»."}</div>`;
    page.offset += r.items.length;
    $("#resultCount").textContent = r.total ? `Найдено: ${r.total}` : "Ничего не найдено";
    $("#moreBtn").hidden = !r.has_more;
    homeLoaded = true;
  } catch (e) {
    if (!append) box.innerHTML = `<div class="empty">Не удалось загрузить: ${esc(e.message)}</div>`;
  }
  page.loading = false;
}
function reloadCatalog() { homeLoaded = false; updateFilterUi(); loadCatalog(); $("#scroll").scrollTop = 0; }

let searchTimer = null;
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { filters.q = $("#search").value.trim(); reloadCatalog(); }, 350);
});
$("#search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { clearTimeout(searchTimer); filters.q = $("#search").value.trim(); reloadCatalog(); e.target.blur(); }
});
document.querySelectorAll("#chips .chip").forEach((chip) => chip.addEventListener("click", () => {
  filters.category = chip.dataset.cat;
  // Особые фильтры подходят только одному разделу
  for (const k of SECTION_FILTERS) filters[k] = DEFAULT_FILTERS[k];
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
  $("#filterHint").hidden = Boolean(filters.category);
  $("#filters").classList.remove("hidden");
}
$("#filterBtn").onclick = openFilters;
$("#closeFilters").onclick = () => $("#filters").classList.add("hidden");
$("#filterForm").onsubmit = (e) => {
  e.preventDefault();
  const f = e.target;
  filters.region_code = f.region_code.value; filters.district_id = f.district_id.value;
  filters.price_min = f.price_min.value; filters.price_max = f.price_max.value;
  for (const k of SECTION_FILTERS) {
    const el = f.elements[k];
    // Скрытые поля других разделов не учитываются
    const visible = el && !el.closest("[data-for]")?.hidden;
    filters[k] = visible ? (el.type === "checkbox" ? el.checked : el.value) : DEFAULT_FILTERS[k];
  }
  $("#filters").classList.add("hidden");
  reloadCatalog();
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
  else if (a.since) parts.push("<b>Автор</b>");
  if (a.active) parts.push(`Активных объявлений: ${a.active}`);
  if (a.since) parts.push(`На сайте с ${a.since}`);
  return parts.length ? `<p class="author">${parts.join(" · ")}</p>` : "";
}
const APP_STATUS = {new: "Отправлен", viewed: "Просмотрен", invited: "Приглашение", rejected: "Отказ"};
let applyListingId = null;
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
async function openDetail(id, push = true) {
  try {
    await loadFields();
    const x = await api(`/api/listings/${id}`, {quiet: true});
    const {phone, user} = contactLinks(x.contact);
    const fav = favIds.has(x.id);
    $("#detailBody").innerHTML = `<article class="detail" data-id="${x.id}" data-cat="${x.category}">
      ${(x.photos || []).length ? `<div class="gallery">${x.photos.map((p) => `<img src="${esc(p.url)}" alt="" loading="lazy">`).join("")}</div>` : ""}
      ${promoBadge(x)}
      <h1>${esc(x.title)}</h1>
      <div class="meta">📍 ${esc(x.city)} · ${esc(CAT[x.category] || x.category)}</div>
      <div class="price big">${esc(priceText(x))}</div>
      ${specs(x)}
      <p class="desc">${esc(x.description || "Подробности уточняйте у автора")}</p>
      ${authorBlock(x)}
      <p class="muted">Опубликовано: ${esc(fmtDate(x.created_at).split(",")[0] || "")}${x.contact && !phone && !user ? " · Контакт: " + esc(x.contact) : ""}</p>
      <div class="cta">
        ${x.category === "job" ? '<button class="primary" data-act="apply">Откликнуться</button>' : ""}
        <button class="${x.category === "job" ? "secondary" : "primary"}" data-act="chat">Написать</button>
        ${phone ? `<a class="btn secondary" href="tel:${esc(phone)}">Позвонить</a>` : ""}
        ${user ? `<a class="btn secondary" data-tglink href="https://t.me/${esc(user)}" target="_blank" rel="noopener">Telegram автора</a>` : ""}
      </div>
      <div class="cta">
        <button class="secondary" data-act="fav">${fav ? "♥ В избранном" : "♡ В избранное"}</button>
        <button class="secondary" data-act="share">Поделиться</button>
      </div>
      <button class="linkbtn" data-act="report">⚑ Пожаловаться</button>
    </article>`;
    showView("detail", {push});
  } catch (e) { toast(e.message); }
}
async function shareListing(id, title) {
  const url = `${location.origin}/?listing=${id}`;
  if (tg?.openTelegramLink) tg.openTelegramLink("https://t.me/share/url?url=" + encodeURIComponent(url) + "&text=" + encodeURIComponent(title));
  else if (navigator.share) navigator.share({title, url}).catch(() => {});
  else { try { await navigator.clipboard.writeText(url); toast("Ссылка скопирована"); } catch (e) { toast(url); } }
}

// ---------- избранное ----------
async function loadFavIds() {
  try { favIds = new Set((await api("/api/favorites", {quiet: true})).map((x) => x.id)); } catch (e) { favIds = new Set(); }
}
async function loadFavorites() {
  const box = $("#favorites");
  try {
    const items = await api("/api/favorites");
    favIds = new Set(items.map((x) => x.id));
    box.innerHTML = items.length ? items.map(listingCard).join("")
      : '<div class="empty">Здесь будут объявления, которые вы сохранили ♡</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ---------- чаты ----------
async function loadConversations() {
  const box = $("#conversations");
  try {
    const items = await api("/api/conversations");
    box.innerHTML = items.length ? items.map((c) => `
      <article class="listing convo" data-chat="${c.id}" data-title="${esc(c.listing_title)}">
        <h3>${esc(c.listing_title)}</h3><p>${esc(c.last_text || "Нет сообщений")}</p></article>`).join("")
      : '<div class="empty">Переписок пока нет. Откройте объявление и нажмите «Написать».</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function openChat(id, title) {
  currentChat = id;
  lastMessageKey = "";
  $("#chatTitle").textContent = title || "Чат";
  showView("chat", {push: true});
  await loadMessages();
  clearInterval(chatTimer);
  chatTimer = setInterval(loadMessages, 5000);
}
async function loadMessages() {
  if (!currentChat) return;
  try {
    const items = await api(`/api/conversations/${currentChat}/messages`);
    const key = items.length + ":" + (items[items.length - 1]?.id || 0);
    if (key === lastMessageKey) return;  // ничего нового: не перерисовываем, экран не дёргается
    lastMessageKey = key;
    const box = $("#messages");
    box.innerHTML = items.length ? items.map((m) => `
      <div class="bubble ${m.mine ? "mine" : ""}">${esc(m.text)}<small>${new Date(m.created_at).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"})}</small></div>`).join("")
      : '<div class="empty">Напишите первое сообщение</div>';
    box.scrollTop = box.scrollHeight;
  } catch (e) { toast(e.message); }
}
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
$("#chatForm").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#chatInput");
  const text = input.value.trim();
  if (!text || !currentChat) return;
  try {
    await api(`/api/conversations/${currentChat}/messages`, {method: "POST", body: JSON.stringify({text})});
    input.value = "";
    lastMessageKey = "";
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
  if (x.status === "approved") {
    acts.push('<button data-own="close" class="secondary">Закрыть</button>');
    if (x.category === "marketplace") acts.push('<button data-own="sold" class="secondary">Продано</button>');
    acts.push('<button data-own="promo">🚀 Продвинуть</button>');
  }
  if (["closed", "sold"].includes(x.status)) acts.push('<button data-own="reopen">Вернуть</button>');
  if (x.status !== "archived") acts.push('<button data-own="archive" class="secondary">Удалить</button>');
  const note = x.note ? `<p class="note">Комментарий модератора: ${esc(x.note)}</p>` : "";
  const edit = x.has_edit ? '<p class="muted">Правка на проверке — пока видна прежняя версия</p>' : "";
  const until = x.status === "approved" && x.expires_at ? ` · до ${esc(fmtDate(x.expires_at).split(",")[0])}` : "";
  const meta = x.status === "approved" ? `<p class="meta-line">👁 ${x.views || 0}${until}</p>` : "";
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
  const body = $("#menuBody");
  body.innerHTML = `<h3>Отклики: ${esc(ownItems[id]?.title || "")}</h3>` + (rows.map((a) => `<article class="listing" data-app="${a.id}">
    <div class="listing-top"><div><b>${esc(a.contact)}</b><div class="meta">${a.city ? "📍 " + esc(a.city) : ""}${a.visa ? " · виза " + esc(a.visa) : ""}</div></div>
    <span class="status ${a.status === "invited" ? "approved" : a.status === "rejected" ? "rejected" : "pending"}">${APP_STATUS[a.status] || a.status}</span></div>
    ${a.message ? `<p>${esc(a.message)}</p>` : ""}<p class="muted">${esc(fmtDate(a.created_at))}</p>
    <div class="actions"><button data-appstatus="invited" data-appid="${a.id}">Пригласить</button>
    <button data-appstatus="rejected" data-appid="${a.id}" class="secondary">Отказать</button></div></article>`).join("")
    || '<div class="empty">Откликов пока нет</div>');
  body.scrollIntoView({block: "nearest"});
  loadProfile();
}
function fillProfileHead() {
  const u = tg?.initDataUnsafe?.user;
  if (!u) return;
  $("#pName").textContent = [u.first_name, u.last_name].filter(Boolean).join(" ") || "Мой профиль";
  const initials = ((u.first_name || "?")[0] + ((u.last_name || "")[0] || "")).toUpperCase();
  $("#pAvatar").innerHTML = u.photo_url ? `<img src="${esc(u.photo_url)}" alt="">` : esc(initials);
}
async function loadProfile() {
  fillProfileHead();
  try {
    const [user, mine, favs, convs] = await Promise.all([api("/api/me"), api("/api/me/listings"), api("/api/favorites"), api("/api/conversations")]);
    me = user;
    favIds = new Set(favs.map((x) => x.id));
    $("#pCity").textContent = "Город: " + user.city;
    $("#sListings").textContent = mine.filter((x) => x.status !== "archived").length;
    $("#sFav").textContent = favs.length;
    $("#sChats").textContent = convs.length;
    ownItems = Object.fromEntries(mine.map((x) => [x.id, x]));
    mineAll = mine;
    renderOwn();
  } catch (e) { $("#myListings").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function renderPayments(rows) {
  return rows.length ? rows.map((p) => `
    <article class="listing"><h3>${esc(p.service)}</h3>
    <p>${p.stars} ⭐ · ${esc({pending: "ожидает оплаты", paid: "оплачено", refunded: "возврат выполнен", failed: "ошибка"}[p.status] || p.status)} · объявление #${p.listing_id}</p>
    <p class="muted">${fmtDate(p.paid_at || p.created_at)}</p></article>`).join("")
    : '<div class="empty">Платежей пока нет</div>';
}
async function openMenu(key) {
  const body = $("#menuBody");
  if (key === "favorites") return showView("saved");
  if (key === "chats") return showView("chats");
  if (key === "payments") body.innerHTML = renderPayments(await api("/api/me/payments"));
  if (key === "applications") {
    const rows = await api("/api/me/applications");
    body.innerHTML = rows.length ? rows.map((a) => `<article class="listing" data-open="${a.listing_id}">
      <div class="listing-top"><div><h3>${esc(a.title)}</h3><div class="meta">📍 ${esc(a.city)}</div></div>
      <span class="status ${a.status === "invited" ? "approved" : a.status === "rejected" ? "rejected" : "pending"}">${APP_STATUS[a.status] || a.status}</span></div>
      <p class="muted">${esc(fmtDate(a.created_at))}</p></article>`).join("")
      : '<div class="empty">Вы пока не откликались на вакансии</div>';
  }
  if (key === "visa") {
    await loadFields();
    body.innerHTML = `<article class="listing"><h3>Моя виза</h3>
      <p class="muted">Видна только вам. Используется в фильтре «Моя виза» и в откликах.</p>
      <label>Тип визы<select id="myVisa"><option value="">Не указана</option>${fieldDefs.visas.map((v) => `<option value="${v.value}">${v.label}</option>`).join("")}</select></label>
      <div class="actions"><button id="saveVisa">Сохранить</button></div></article>`;
    $("#myVisa").value = me?.visa || "";
    $("#saveVisa").onclick = async () => {
      try {
        const r = await api("/api/me/settings", {method: "PUT", body: JSON.stringify({visa: $("#myVisa").value})});
        me.visa = r.visa; toast("Сохранено");
      } catch (err) { toast(err.message); }
    };
  }
  if (key === "employer") {
    const st = me?.employer_status;
    const text = {none: "Вы ещё не подавали заявку.", pending: "Заявка на проверке модератора.",
                  approved: "Статус работодателя подтверждён.", rejected: "Заявка отклонена. Можно подать снова с верными данными."}[st] || "";
    body.innerHTML = `<article class="listing"><p>${text}</p>
      ${(st === "none" || st === "rejected") ? '<div class="actions"><button data-own="employer-apply">Подать заявку</button></div>' : ""}</article>`;
  }
  if (key === "blocks") {
    const ids = await api("/api/me/blocks");
    body.innerHTML = ids.length ? ids.map((id) => `
      <article class="listing"><h3>Пользователь ${esc(id)}</h3>
      <div class="actions"><button data-unblock="${esc(id)}" class="secondary">Разблокировать</button></div></article>`).join("")
      : '<div class="empty">Вы никого не блокировали</div>';
  }
  if (key === "help") {
    body.innerHTML = `<article class="listing"><h3>Как пользоваться</h3>
      <p>1. Опубликуйте объявление: модератор проверит его обычно в течение 24 часов.</p>
      <p>2. Общение только внутри приложения: телефон в объявлении указывайте по желанию.</p>
      <p>3. Оплата услуг — в звёздах Telegram (⭐). Возврат — через поддержку.</p>
      <p>4. Жалобы на объявления и людей — кнопка «Пожаловаться».</p>
      <p>Вопросы: команда /help в боте.</p></article>`;
  }
  if (key === "logout") {
    await api("/api/auth/logout", {method: "POST", quiet: true});
    localStorage.removeItem("webSession");
    location.reload();
    return;
  }
  if (key === "export") return exportMyData();
  if (key === "delete") return deleteMyAccount();
  body.scrollIntoView({block: "nearest"});
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
function updateFormBlocks() {
  const category = $("#formCategory").value;
  showBlocks("#listingForm", category);
  $("#photoHint").textContent = ["housing", "marketplace"].includes(category)
    ? "Для жилья и товаров нужно хотя бы одно фото." : "Фото по желанию.";
  $("#amountLabel").textContent = {job: "Зарплата от, ₩", housing: "Аренда в месяц, ₩", marketplace: "Цена, ₩"}[category] || "Сумма, ₩";
}
$("#formCategory").onchange = updateFormBlocks;
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
async function openAdd(category) {
  if (category === "dating") return openMyDating();
  await loadFields();
  currentEditId = null;
  const f = $("#listingForm");
  f.reset();
  f.category.value = category;
  await setGeo("#formGeo", me?.region_code || "", me?.district_id || "");
  f.category.disabled = false;
  $("#modalTitle").textContent = "Новое объявление";
  resetPhotos([]);
  updateFormBlocks();
  $("#addSheet").classList.add("hidden");
  $("#modal").classList.remove("hidden");
}
async function openEdit(item) {
  await loadFields();
  currentEditId = item.id;
  const f = $("#listingForm");
  f.reset();
  f.category.value = item.category;
  f.category.disabled = true;  // раздел менять нельзя
  await setGeo("#formGeo", item.region_code || "", item.district_id || "");
  const d = item.details || {};
  for (const k of ["title", "price", "description", "contact"]) f[k].value = item[k] || "";
  f.price_amount.value = item.price_amount ?? "";
  for (const k of ["salary_period", "employment", "schedule", "housing_type", "goods_category", "condition"]) f[k].value = item[k] || "";
  for (const k of ["deposit", "area_m2", "rooms"]) f[k].value = item[k] ?? "";
  for (const k of ["housing_provided", "meals_provided", "pets_allowed", "furnished", "negotiable"]) f[k].checked = Boolean(item[k]);
  for (const k of ["company", "position", "salary_max", "vacancies", "start_date", "maintenance_fee", "floor",
                   "available_from", "contract_term", "station", "brand"]) f["d_" + k].value = d[k] ?? "";
  for (const k of ["transport", "experience", "delivery"]) f["d_" + k].checked = Boolean(d[k]);
  document.querySelectorAll("#listingForm [data-visas] input").forEach((c) => { c.checked = (item.visas || []).includes(c.value); });
  $("#modalTitle").textContent = "Изменить объявление";
  resetPhotos((item.photos || []).filter((p) => p.status !== "removal"));
  updateFormBlocks();
  $("#modal").classList.remove("hidden");
}
function listingPayload(f) {
  const v = (n) => f[n].value.trim();
  const num = (n) => (v(n) === "" ? undefined : Number(v(n)));
  const category = f.category.value;
  const data = {category, title: v("title"), region_code: f.region_code.value, price: v("price"),
                description: v("description"), contact: v("contact"), price_amount: num("price_amount")};
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
    Object.assign(data, {goods_category: v("goods_category") || undefined, condition: v("condition") || undefined,
      negotiable: f.negotiable.checked});
    details = {brand: v("d_brand"), delivery: f.d_delivery.checked};
  }
  data.details = Object.fromEntries(Object.entries(details).filter(([, x]) => x !== undefined && x !== ""));
  return Object.fromEntries(Object.entries(data).filter(([, x]) => x !== undefined));
}
async function saveListing(send) {
  const f = $("#listingForm");
  const data = listingPayload(f);
  if (send && ["housing", "marketplace"].includes(data.category || f.category.value) && !formPhotos.length && !formFiles.length) {
    toast("Добавьте хотя бы одно фото"); return;
  }
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
    toast(!send ? "Черновик сохранён" : wasLive ? "Изменения отправлены на проверку" : "Отправлено на проверку");
    currentEditId = null;
    $("#modal").classList.add("hidden");
    showView("profile");
    loadProfile();
  } catch (err) { toast(err.message); }
}
$("#listingForm").onsubmit = (e) => { e.preventDefault(); saveListing(true); };
$("#draftBtn").onclick = () => saveListing(false);
$("#closeModal").onclick = () => $("#modal").classList.add("hidden");
$("#addBtn").onclick = async () => { if (await requireConsent()) $("#addSheet").classList.remove("hidden"); };
$("#closeAdd").onclick = () => $("#addSheet").classList.add("hidden");
document.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => openAdd(b.dataset.add));

// ---------- сообщество и гид ----------
async function loadGroups() {
  try {
    const items = await api("/api/groups");
    $("#groups").innerHTML = items.map((g) => `
      <article class="listing"><h3>${esc(g.flag)} ${esc(g.name)}</h3><p>${esc(g.description)}</p>
      ${g.telegram_link
        ? `<div class="actions"><a class="btn" data-tglink href="${esc(g.telegram_link)}" target="_blank" rel="noopener">Перейти в Telegram-группу</a></div>`
        : '<p class="muted">Ссылка появится после проверки</p>'}
      </article>`).join("");
  } catch (e) { $("#groups").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function loadGuide() {
  try {
    const items = await api("/api/articles");
    $("#guideSections").innerHTML = Object.entries(SECTIONS).map(([key, title]) => {
      const count = items.filter((a) => a.section === key).length;
      return `<article class="listing convo" data-section="${key}"><h3>${esc(title)}</h3>
        <p>${count ? count + " статей" : "Материалы готовятся: статьи появятся после проверки по официальным источникам"}</p></article>`;
    }).join("");
  } catch (e) { $("#guideSections").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function openSection(key) {
  const items = (await api("/api/articles")).filter((a) => a.section === key);
  $("#articleTitle").textContent = SECTIONS[key] || "Раздел";
  $("#articleBody").innerHTML = items.length ? items.map((a) => `
    <article class="listing convo" data-article="${a.id}"><h3>${esc(a.title)}</h3>
    <p class="muted">Проверено: ${esc(a.checked_at ? a.checked_at.slice(0, 10) : "—")}</p></article>`).join("")
    : '<div class="empty">Статьи в этом разделе пока готовятся.</div>';
  showView("article", {push: true});
}
async function openArticle(id) {
  const a = await api(`/api/articles/${id}`);
  $("#articleTitle").textContent = a.title;
  $("#articleBody").innerHTML = `<article class="listing"><p class="muted">Источник: <a href="${esc(a.source_url)}" target="_blank" rel="noopener">${esc(a.source_url)}</a></p>
    <p>${esc(a.body).replace(/\n/g, "<br>")}</p><p class="muted">Дата проверки: ${esc((a.checked_at || "").slice(0, 10))}</p></article>`;
  $("#scroll").scrollTop = 0;
}
$("#articleBack").onclick = goBack;
$("#detailBack").onclick = goBack;

// ---------- продвижение (ТЗ № 2): оплата Telegram Stars ----------
async function openServices(listingId, category) {
  const items = await api("/api/services?category=" + encodeURIComponent(category));
  $("#serviceList").innerHTML = items.map((s) => `
    <article class="listing"><h3>${esc(s.title)}</h3>
    <p>${s.stars} ⭐${s.duration_days > 1 ? " · " + s.duration_days + " дней" : " · поднимает на 24 часа"}</p>
    <div class="actions"><button data-buy="${esc(s.code)}" data-listing="${listingId}">Оплатить ${s.stars} ⭐</button></div></article>`).join("")
    || '<div class="empty">Для этого раздела услуг пока нет</div>';
  $("#services").classList.remove("hidden");
}
async function buy(listingId, code) {
  const r = await api("/api/payments/invoice", {method: "POST", body: JSON.stringify({listing_id: Number(listingId), service_code: code})});
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
async function loadDating() {
  const box = $("#datingList");
  box.innerHTML = '<div class="skeleton"></div>';
  const params = new URLSearchParams();
  if (datingGender) params.set("gender", datingGender);
  try {
    const items = await api("/api/dating?" + params);
    box.innerHTML = items.length ? items.map((p) => `
      <article class="listing" data-profile="${p.id}">
        <img class="photo" src="${esc(p.photo_url)}" alt="" loading="lazy">
        <h3>${p.age} · ${p.gender === "f" ? "девушка" : "парень"}</h3>
        <div class="meta">📍 ${esc(p.city)}</div><p>${esc(p.bio)}</p>
        <div class="actions"><button data-act="like">♥ Нравится</button>
        <button data-act="dreport" class="secondary">⚑ Пожаловаться</button></div>
      </article>`).join("")
      : '<div class="empty">Анкет пока нет. Загляните позже.</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
document.querySelectorAll("#datingChips .chip").forEach((chip) => chip.addEventListener("click", () => {
  datingGender = chip.dataset.gender;
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
      await setGeo("#datingGeo", mine.region_code || "", mine.district_id || "");
      toast("Статус анкеты: " + ({pending: "на проверке", published: "опубликована", rejected: "отклонена"}[mine.status] || mine.status));
    }
  } catch (e) { toast(e.message); }
  $("#addSheet").classList.add("hidden");
  $("#datingModal").classList.remove("hidden");
}
$("#myDatingBtn").onclick = openMyDating;
$("#closeDating").onclick = () => $("#datingModal").classList.add("hidden");
$("#datingForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/dating/profile", {method: "PUT", body: JSON.stringify({
      birth_year: Number(f.birth_year.value), gender: f.gender.value, region_code: f.region_code.value,
      district_id: f.district_id.value ? Number(f.district_id.value) : null, bio: f.bio.value.trim()})});
    if (f.photo.files[0]) {
      const data = new FormData();
      data.append("file", f.photo.files[0]);
      const headers = inTelegram ? {"X-Telegram-Init-Data": tg.initData}
        : {"X-Web-Session": localStorage.getItem("webSession") || ""};
      const r = await fetch("/api/dating/photo", {method: "POST", body: data, headers});
      if (!r.ok) throw new Error((await r.json()).detail || "Не удалось загрузить фото");
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
          else b.textContent = r.favorited ? "♥ В избранном" : "♡ В избранное";
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
    try { await api(`/api/users/${unblock.dataset.unblock}/block`, {method: "DELETE"}); toast("Пользователь разблокирован"); openMenu("blocks"); }
    catch (err) { toast(err.message); }
    return;
  }
  const st = e.target.closest("[data-appstatus]");
  if (st) {
    try {
      await api(`/api/applications/${st.dataset.appid}`, {method: "PUT", body: JSON.stringify({status: st.dataset.appstatus})});
      const badge = st.closest("[data-app]").querySelector(".status");
      badge.textContent = APP_STATUS[st.dataset.appstatus];
      badge.className = "status " + (st.dataset.appstatus === "invited" ? "approved" : "rejected");
      toast("Кандидат уведомлён");
    } catch (err) { toast(err.message); }
    return;
  }
  const own = e.target.closest("[data-own]");
  if (own) {
    const cardEl = own.closest("[data-id]");
    const id = cardEl?.dataset.id;
    const item = ownItems[id];
    try {
      const act = own.dataset.own;
      if (act === "employer-apply") { $("#employerModal").classList.remove("hidden"); return; }
      if (act === "edit") { openEdit(item); return; }
      if (act === "applicants") { await showApplicants(id); return; }
      if (act === "promo") { await openServices(id, item.category); return; }
      if (act === "archive" && !(await confirmDialog("Удалить объявление? Его не будет видно другим."))) return;
      await api(`/api/listings/${id}/${{close: "close", sold: "sold", reopen: "reopen", archive: "archive", renew: "renew", submit: "submit"}[act]}`, {method: "POST"});
      toast("Готово");
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
  if (open) { openDetail(open.dataset.open); return; }
});

document.querySelectorAll(".bottom [data-view], .quick [data-view]").forEach((b) => b.onclick = () => showView(b.dataset.view));

// ---------- согласие, город, работодатель ----------
$("#consentOk").onclick = async () => {
  if (!$("#consentRules").checked || !$("#consentPrivacy").checked) { toast("Отметьте оба пункта, чтобы продолжить"); return; }
  try {
    await api("/api/me/consent", {method: "POST", body: JSON.stringify({accept: true})});
    $("#consent").classList.add("hidden");
    toast("Спасибо! Теперь можно пользоваться приложением");
  } catch (err) { toast(err.message); }
};
$("#cityBtn").onclick = async () => {
  await setGeo("#cityGeo", me?.region_code || "", me?.district_id || "");
  $("#cityModal").classList.remove("hidden");
};
$("#closeCity").onclick = () => $("#cityModal").classList.add("hidden");
$("#cityForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const f = e.target;
    await api("/api/me/settings", {method: "PUT", body: JSON.stringify({
      region_code: f.region_code.value, district_id: f.district_id.value ? Number(f.district_id.value) : null})});
    $("#cityModal").classList.add("hidden");
    toast("Город сохранён");
    loadProfile();
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
  const t = tg?.themeParams || {};
  const st = document.documentElement.style;
  const set = (name, value) => { if (value) st.setProperty(name, value); };
  set("--bg", t.secondary_bg_color || t.bg_color);
  set("--card", t.section_bg_color || t.bg_color);
  set("--text", t.text_color);
  set("--hint", t.hint_color);
  set("--accent", t.button_color);
  set("--accent-text", t.button_text_color);
  set("--border", t.section_separator_color);
}
applyTelegramTheme();
tg?.onEvent?.("themeChanged", applyTelegramTheme);

// ---------- старт ----------
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
  .then((user) => { me = user; if (!me.consent) $("#consent").classList.remove("hidden"); })
  .catch(() => { if (!inTelegram) $("#loginTop").hidden = false; });
