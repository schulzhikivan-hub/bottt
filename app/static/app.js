const tg = window.Telegram?.WebApp; tg?.ready(); tg?.expand();
const $ = (s) => document.querySelector(s);
const esc = (s = "") => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const CAT = {job: "Работа", housing: "Жильё", marketplace: "Барахолка"};
const STATUS = {pending: ["На модерации", "pending"], approved: ["Опубликовано", "approved"],
                rejected: ["Отклонено", "rejected"], needs_fix: ["Нужно исправить", "pending"],
                hidden: ["Скрыто", "rejected"], closed: ["Закрыто", "rejected"], sold: ["Продано", "approved"],
                archived: ["В архиве", "rejected"]};
const REASONS = {fraud: "Мошенничество", fake: "Поддельное объявление", spam: "Спам",
                 offensive: "Оскорбления", inappropriate: "Неприемлемый контент", rights: "Нарушение прав",
                 minor: "Несовершеннолетний", profile: "Жалоба на профиль"};
const SECTIONS = {visas: "Визы и документы", registration: "Регистрация и адрес", medicine: "Медицина",
                  banks: "Банки и мобильная связь", housing: "Жильё", work: "Работа и трудовые права",
                  emergency: "Экстренные ситуации"};
let currentCategory = null;
let currentChat = null;
let currentChatUser = null;
let chatTimer = null;
let me = null;

// Все запросы подписываются данными Telegram: сервер проверяет подпись.
async function api(path, options = {}) {
  const headers = {"X-Telegram-Init-Data": tg?.initData || ""};
  if (options.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}});
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) throw new Error(data?.detail || "Ошибка сервера (" + r.status + ")");
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block";
  setTimeout(() => (t.style.display = "none"), 3000);
  tg?.HapticFeedback?.notificationOccurred("success");
}
function fmtDate(s) { return s ? new Date(s.replace(" ", "T")).toLocaleString("ru-RU") : ""; }

function card(x, opts = {}) {
  const fav = opts.favorite
    ? '<button data-act="fav" class="on">♥ В избранном</button>'
    : '<button data-act="fav" class="secondary">♡ В избранное</button>';
  const chat = opts.chat === false ? "" : '<button data-act="chat">Написать</button>';
  const promo = opts.promo ? '<button data-act="promo" class="secondary">🚀 Продвинуть</button>' : "";
  const report = opts.noActions ? "" : '<button data-act="report" class="secondary">⚑ Пожаловаться</button>';
  const status = opts.showStatus ? `<span class="status ${STATUS[x.status]?.[1] || ""}">${STATUS[x.status]?.[0] || esc(x.status)}</span>` : "";
  let actions = `<div class="actions">${chat}${fav}${promo}${report}</div>`;
  if (opts.ownerView) actions = opts.promo ? `<div class="actions">${promo}</div>` : "";
  return `<article class="listing" data-id="${x.id}" data-cat="${x.category}">
    <div class="listing-top"><div><h3>${esc(x.title)}</h3><p>📍 ${esc(x.city)}</p></div><span class="tag">${esc(CAT[x.category] || x.category)}</span></div>
    <div class="price">${esc(x.price || "Цена договорная")}</div>
    <p>${esc(x.description || "Подробности уточняйте у автора")}</p>
    ${status}${actions}
  </article>`;
}

async function loadMe() {
  me = await api("/api/me");
  return me;
}

async function requireConsent() {
  await loadMe();
  if (!me.consent) {
    $("#consent").classList.remove("hidden");
    return false;
  }
  return true;
}

async function loadListings() {
  const box = $("#listings");
  box.innerHTML = '<div class="empty">Загружаем…</div>';
  const params = new URLSearchParams();
  if (currentCategory) params.set("category", currentCategory);
  const q = $("#search").value.trim();
  if (q) params.set("q", q);
  try {
    const items = await api("/api/listings?" + params);
    box.innerHTML = items.length ? items.map((x) => card(x)).join("")
      : '<div class="empty">Пока нет опубликованных объявлений. Добавьте первое!</div>';
  } catch (e) {
    box.innerHTML = `<div class="empty">Не удалось загрузить: ${esc(e.message)}</div>`;
  }
}

async function loadFavorites() {
  const box = $("#favorites");
  try {
    const items = await api("/api/favorites");
    box.innerHTML = items.length ? items.map((x) => card(x, {favorite: true})).join("")
      : '<div class="empty">Здесь будут объявления, которые вы сохранили ♡</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

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
  $("#chatTitle").textContent = title || "Чат";
  showView("chat");
  await loadMessages();
  clearInterval(chatTimer);
  chatTimer = setInterval(loadMessages, 5000);
}

async function loadMessages() {
  if (!currentChat) return;
  try {
    const items = await api(`/api/conversations/${currentChat}/messages`);
    const box = $("#messages");
    box.innerHTML = items.length ? items.map((m) => `
      <div class="bubble ${m.mine ? "mine" : ""}">${esc(m.text)}<small>${new Date(m.created_at).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"})}</small></div>`).join("")
      : '<div class="empty">Напишите первое сообщение</div>';
    box.scrollTop = box.scrollHeight;
  } catch (e) { toast(e.message); }
}

let ownItems = {};

function ownCard(x) {
  const acts = [];
  if (["pending", "approved", "rejected", "needs_fix"].includes(x.status)) acts.push('<button data-own="edit">✏️ Изменить</button>');
  if (x.status === "approved") {
    acts.push('<button data-own="close" class="secondary">Закрыть</button>');
    if (x.category === "marketplace") acts.push('<button data-own="sold" class="secondary">Продано</button>');
    acts.push('<button data-own="promo">🚀 Продвинуть</button>');
  }
  if (["closed", "sold"].includes(x.status)) acts.push('<button data-own="reopen">Вернуть</button>');
  if (x.status !== "archived") acts.push('<button data-own="archive" class="secondary">Удалить</button>');
  const note = x.note ? `<p class="note">Комментарий модератора: ${esc(x.note)}</p>` : "";
  return `<article class="listing" data-id="${x.id}" data-cat="${x.category}">
    <div class="listing-top"><div><h3>${esc(x.title)}</h3><p>📍 ${esc(x.city)}</p></div>
    <span class="status ${STATUS[x.status]?.[1] || ""}">${STATUS[x.status]?.[0] || esc(x.status)}</span></div>
    <div class="price">${esc(x.price || "Цена договорная")}</div>${note}
    <div class="actions">${acts.join("")}</div></article>`;
}

async function loadProfile() {
  try {
    const [user, mine, payments] = await Promise.all([api("/api/me"), api("/api/me/listings"), api("/api/me/payments")]);
    me = user;
    $("#pCity").textContent = "Город: " + user.city;
    const favs = await api("/api/favorites");
    const convs = await api("/api/conversations");
    $("#sListings").textContent = mine.filter((x) => x.status !== "archived").length;
    $("#sFav").textContent = favs.length;
    $("#sChats").textContent = convs.length;
    ownItems = Object.fromEntries(mine.map((x) => [x.id, x]));
    $("#myListings").innerHTML = mine.length ? mine.filter((x) => x.status !== "archived").map(ownCard).join("")
      : '<div class="empty">Вы ещё не публиковали объявления. Нажмите «+» внизу.</div>';
    $("#menuBody").dataset.payments = JSON.stringify(payments);
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
  if (key === "payments") {
    body.innerHTML = renderPayments(await api("/api/me/payments"));
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
      <p>4. Жалобы на объявления и людей — кнопка «Пожаловаться» в карточке.</p>
      <p>Вопросы: команда /help в боте.</p></article>`;
  }
  if (key === "export") return exportMyData();
  if (key === "delete") return deleteMyAccount();
}

function openAdd(category) {
  if (category === "dating") return openMyDating();
  currentEditId = null;
  $("#modalTitle").textContent = "Новое объявление";
  $("#listingForm").category.value = category;
  $("#addSheet").classList.add("hidden");
  $("#modal").classList.remove("hidden");
}

async function loadGroups() {
  try {
    const items = await api("/api/groups");
    $("#groups").innerHTML = items.map((g) => `
      <article class="listing"><h3>${esc(g.flag)} ${esc(g.name)}</h3><p>${esc(g.description)}</p>
      ${g.telegram_link
        ? `<div class="actions"><a class="btn" href="${esc(g.telegram_link)}" target="_blank" rel="noopener">Перейти в Telegram-группу</a></div>`
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
  showView("article");
}

async function openArticle(id) {
  const a = await api(`/api/articles/${id}`);
  $("#articleTitle").textContent = a.title;
  $("#articleBody").innerHTML = `<article class="listing"><p class="muted">Источник: <a href="${esc(a.source_url)}" target="_blank" rel="noopener">${esc(a.source_url)}</a></p>
    <p>${esc(a.body).replace(/\n/g, "<br>")}</p><p class="muted">Дата проверки: ${esc((a.checked_at || "").slice(0, 10))}</p></article>`;
}

// Продвижение (ТЗ № 2): выбор услуги и оплата Telegram Stars
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
  const r = await api("/api/payments/invoice", {method: "POST",
    body: JSON.stringify({listing_id: Number(listingId), service_code: code})});
  if (!tg?.openInvoice) {
    toast("Оплата открывается только внутри Telegram");
    return;
  }
  tg.openInvoice(r.invoice_url, (status) => {
    $("#services").classList.add("hidden");
    if (status === "paid") toast("Оплата прошла. Услуга активируется");
    else if (status === "failed") toast("Оплата не прошла");
    loadProfile();
  });
}

async function loadDating() {
  const box = $("#datingList");
  box.innerHTML = '<div class="empty">Загружаем анкеты…</div>';
  const params = new URLSearchParams();
  if ($("#datingGender").value) params.set("gender", $("#datingGender").value);
  try {
    const items = await api("/api/dating?" + params);
    box.innerHTML = items.length ? items.map((p) => `
      <article class="listing" data-profile="${p.id}">
        <img class="photo" src="${esc(p.photo_url)}" alt="" loading="lazy">
        <h3>${p.age} · ${p.gender === "f" ? "девушка" : "парень"}</h3>
        <p>📍 ${esc(p.city)}</p><p>${esc(p.bio)}</p>
        <div class="actions"><button data-act="like">♥ Нравится</button>
        <button data-act="dreport" class="secondary">⚑ Пожаловаться</button></div>
      </article>`).join("")
      : '<div class="empty">Анкет пока нет. Загляните позже.</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function openMyDating() {
  if (!(await requireConsent())) return;
  const f = $("#datingForm");
  try {
    const me = await api("/api/dating/me");
    if (me) {
      f.birth_year.value = me.age ? new Date().getFullYear() - me.age : "";
      f.gender.value = me.gender; f.city.value = me.city;
      toast("Статус анкеты: " + ({pending: "на проверке", published: "опубликована", rejected: "отклонена"}[me.status] || me.status));
    }
  } catch (e) { toast(e.message); }
  $("#datingModal").classList.remove("hidden");
}

$("#datingForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target;
  try {
    await api("/api/dating/profile", {method: "PUT", body: JSON.stringify({
      birth_year: Number(f.birth_year.value), gender: f.gender.value,
      city: f.city.value.trim(), bio: f.bio.value.trim()})});
    if (f.photo.files[0]) {
      const data = new FormData();
      data.append("file", f.photo.files[0]);
      const r = await fetch("/api/dating/photo", {method: "POST", body: data,
        headers: {"X-Telegram-Init-Data": tg?.initData || ""}});
      if (!r.ok) throw new Error((await r.json()).detail || "Не удалось загрузить фото");
    }
    $("#datingModal").classList.add("hidden");
    toast("Анкета отправлена на проверку");
  } catch (err) { toast(err.message); }
};

function showView(name) {
  const main = ["home", "saved", "chats", "profile", "dating", "community", "guide"];
  if (main.includes(name)) tg?.BackButton?.hide(); else tg?.BackButton?.show();
  for (const v of ["home", "saved", "chats", "chat", "profile", "community", "guide", "article", "dating"]) {
    const el = $("#view-" + v);
    if (el) el.hidden = v !== name;
  }
  const navName = ["chat", "article"].includes(name) ? "" : name;
  document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === navName));
  if (name !== "chat") clearInterval(chatTimer);
  if (name === "home") loadListings();
  if (name === "saved") loadFavorites();
  if (name === "chats") loadConversations();
  if (name === "profile") loadProfile();
  if (name === "community") loadGroups();
  if (name === "guide") loadGuide();
  if (name === "dating") loadDating();
  window.scrollTo({top: 0});
}

// Действия на карточках и списках (делегирование событий)
document.addEventListener("click", async (e) => {
  const buyBtn = e.target.closest("[data-buy]");
  if (buyBtn) {
    try { await buy(buyBtn.dataset.listing, buyBtn.dataset.buy); }
    catch (err) { toast(err.message); }
    return;
  }
  const btn = e.target.closest("[data-act]");
  if (btn) {
    const article = btn.closest("[data-id]");
    const id = article.dataset.id;
    try {
      if (btn.dataset.act === "fav") {
        if (!(await requireConsent())) return;
        const r = await api(`/api/favorites/${id}`, {method: "POST"});
        btn.textContent = r.favorited ? "♥ В избранном" : "♡ В избранное";
        btn.classList.toggle("on", r.favorited);
        btn.classList.toggle("secondary", !r.favorited);
        toast(r.favorited ? "Добавлено в избранное" : "Убрано из избранного");
      }
      if (btn.dataset.act === "chat") {
        if (!(await requireConsent())) return;
        const c = await api(`/api/listings/${id}/conversation`, {method: "POST"});
        openChat(c.id, article.querySelector("h3")?.textContent);
      }
      if (btn.dataset.act === "like") {
        if (!(await requireConsent())) return;
        const profileId = article.dataset.profile;
        const r = await api(`/api/dating/${profileId}/like`, {method: "POST"});
        if (r.match) {
          toast("Взаимная симпатия! Можно писать");
          openChat(r.conversation_id, "Знакомство");
        } else {
          article.remove();
          toast("Отправлено");
        }
      }
      if (btn.dataset.act === "dreport") {
        if (!(await requireConsent())) return;
        const profileId = article.dataset.profile;
        const reason = prompt("Причина:\n" + Object.entries(REASONS).map(([k, v]) => k + " — " + v).join("\n"), "profile");
        if (!reason || !REASONS[reason.trim()]) return;
        await api("/api/reports", {method: "POST", body: JSON.stringify(
          {target_type: "dating", target_id: profileId, reason: reason.trim()})});
        toast("Жалоба отправлена. Спасибо!");
      }
      if (btn.dataset.act === "promo") {
        if (!(await requireConsent())) return;
        await openServices(id, article.dataset.cat);
      }
      if (btn.dataset.act === "report") {
        if (!(await requireConsent())) return;
        const reason = prompt("Причина жалобы:\n" + Object.entries(REASONS).map(([k, v]) => k + " — " + v).join("\n"), "spam");
        if (!reason || !REASONS[reason.trim()]) return;
        await api("/api/reports", {method: "POST", body: JSON.stringify(
          {target_type: "listing", target_id: id, reason: reason.trim()})});
        toast("Жалоба отправлена. Спасибо!");
      }
    } catch (err) { toast(err.message); }
    return;
  }
  const convo = e.target.closest("[data-chat]");
  if (convo) { openChat(convo.dataset.chat, convo.dataset.title); return; }
  const section = e.target.closest("[data-section]");
  if (section) { openSection(section.dataset.section); return; }
  const article = e.target.closest("[data-article]");
  if (article) { openArticle(article.dataset.article); return; }
});

document.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
  currentCategory = b.dataset.cat;
  $("#listTitle").textContent = b.querySelector("b").textContent;
  showView("home");
  window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"});
}));
document.querySelectorAll("[data-soon]").forEach((b) => b.onclick = () => toast(b.dataset.soon + ": раздел в разработке"));
document.querySelectorAll("[data-view]").forEach((b) => b.onclick = () => showView(b.dataset.view));
$("#searchBtn").onclick = loadListings;
$("#search").addEventListener("keydown", (e) => { if (e.key === "Enter") loadListings(); });
$("#allBtn").onclick = () => { currentCategory = null; $("#listTitle").textContent = "Последние объявления"; loadListings(); };
$("#addBtn").onclick = async () => { if (await requireConsent()) $("#addSheet").classList.remove("hidden"); };
$("#closeAdd").onclick = () => $("#addSheet").classList.add("hidden");
document.querySelectorAll("[data-add]").forEach((b) => b.onclick = () => openAdd(b.dataset.add));
$("#closeModal").onclick = () => $("#modal").classList.add("hidden");
$("#closeServices").onclick = () => $("#services").classList.add("hidden");
$("#chatBack").onclick = () => showView("chats");
$("#myDatingBtn").onclick = openMyDating;
$("#closeDating").onclick = () => $("#datingModal").classList.add("hidden");
$("#datingGender").onchange = loadDating;

// Переход из меню бота: ?section=job|housing|marketplace|dating|community|guide
function openFromMenu() {
  const section = new URLSearchParams(location.search).get("section");
  if (!section) return false;
  if (["job", "housing", "marketplace"].includes(section)) {
    currentCategory = section;
    $("#listTitle").textContent = CAT[section];
    showView("home");
    return true;
  }
  if (["dating", "community", "guide"].includes(section)) { showView(section); return true; }
  return false;
}
$("#articleBack").onclick = () => showView("guide");

$("#consentOk").onclick = async () => {
  if (!$("#consentRules").checked || !$("#consentPrivacy").checked) {
    toast("Отметьте оба пункта, чтобы продолжить");
    return;
  }
  try {
    await api("/api/me/consent", {method: "POST", body: JSON.stringify({accept: true})});
    $("#consent").classList.add("hidden");
    toast("Спасибо! Теперь можно пользоваться приложением");
    loadListings();
  } catch (err) { toast(err.message); }
};

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
  if (!confirm("Удалить аккаунт? Объявления будут скрыты, избранное удалено. Действие необратимо.")) return;
  try {
    await api("/api/me/delete", {method: "POST"});
    toast("Аккаунт удалён");
    showView("home");
  } catch (err) { toast(err.message); }
}

let currentEditId = null;

$("#listingForm").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try {
    if (currentEditId) {
      delete data.category;
      await api(`/api/listings/${currentEditId}`, {method: "PUT", body: JSON.stringify(data)});
      toast("Изменения отправлены на модерацию");
    } else {
      await api("/api/listings", {method: "POST", body: JSON.stringify(data)});
      toast("Объявление отправлено на модерацию");
    }
    e.target.reset();
    currentEditId = null;
    $("#modal").classList.add("hidden");
    showView("profile");
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
    await loadMessages();
  } catch (err) { toast(err.message); }
};

// Тема Telegram: цвета приложения совпадают с цветами чата
function applyTelegramTheme() {
  const t = tg?.themeParams || {};
  const st = document.documentElement.style;
  if (t.bg_color) st.setProperty("--tg-bg", t.bg_color);
  if (t.secondary_bg_color) st.setProperty("--tg-card", t.secondary_bg_color);
  if (t.text_color) st.setProperty("--tg-text", t.text_color);
  if (t.hint_color) st.setProperty("--tg-hint", t.hint_color);
  if (t.button_color) st.setProperty("--tg-button", t.button_color);
}
applyTelegramTheme();
tg?.BackButton?.onClick(() => showView("home"));

$("#cityBtn").onclick = () => { $("#cityForm").city.value = me?.city || ""; $("#cityModal").classList.remove("hidden"); };
$("#closeCity").onclick = () => $("#cityModal").classList.add("hidden");
$("#cityForm").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/me/settings", {method: "PUT", body: JSON.stringify({city: e.target.city.value.trim()})});
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
    openMenu("employer");
  } catch (err) { toast(err.message); }
};

document.addEventListener("click", async (e) => {
  const menu = e.target.closest("[data-menu]");
  if (menu) { try { await openMenu(menu.dataset.menu); } catch (err) { toast(err.message); } return; }
  const unblock = e.target.closest("[data-unblock]");
  if (unblock) {
    try {
      await api(`/api/users/${unblock.dataset.unblock}/block`, {method: "DELETE"});
      toast("Пользователь разблокирован");
      openMenu("blocks");
    } catch (err) { toast(err.message); }
    return;
  }
  const own = e.target.closest("[data-own]");
  if (!own) return;
  const card = own.closest("[data-id]");
  const id = card?.dataset.id;
  const item = ownItems[id];
  try {
    const act = own.dataset.own;
    if (act === "employer-apply") { $("#employerModal").classList.remove("hidden"); return; }
    if (act === "edit") {
      currentEditId = id;
      const f = $("#listingForm");
      for (const k of ["title", "city", "price", "description", "contact", "category"]) f[k].value = item[k] || "";
      $("#modalTitle").textContent = "Изменить объявление";
      $("#modal").classList.remove("hidden");
      return;
    }
    if (act === "promo") { await openServices(id, item.category); return; }
    if (act === "archive" && !confirm("Удалить объявление? Его не будет видно другим.")) return;
    const path = {close: "close", sold: "sold", reopen: "reopen", archive: "archive"}[act];
    await api(`/api/listings/${id}/${path}`, {method: "POST"});
    toast("Готово");
    loadProfile();
  } catch (err) { toast(err.message); }
});

loadMe().then(() => { if (!me.consent) $("#consent").classList.remove("hidden"); }).catch(() => {});
if (!openFromMenu()) loadListings();
