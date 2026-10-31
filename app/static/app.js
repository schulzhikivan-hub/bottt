const tg = window.Telegram?.WebApp; tg?.ready(); tg?.expand();
const $ = (s) => document.querySelector(s);
const esc = (s = "") => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const CAT = {job: "Работа", housing: "Жильё", marketplace: "Барахолка"};
const STATUS = {pending: ["На модерации", "pending"], approved: ["Опубликовано", "approved"], rejected: ["Отклонено", "rejected"]};
let currentCategory = null;
let currentChat = null;
let chatTimer = null;

// Все запросы подписываются данными Telegram: сервер проверяет подпись.
function authHeaders(extra = {}) {
  return {"X-Telegram-Init-Data": tg?.initData || "", ...extra};
}
async function api(path, options = {}) {
  const headers = authHeaders(options.body ? {"Content-Type": "application/json"} : {});
  const r = await fetch(path, {...options, headers: {...headers, ...(options.headers || {})}});
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) throw new Error(data?.detail || "Ошибка сервера (" + r.status + ")");
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block";
  setTimeout(() => (t.style.display = "none"), 2600);
  tg?.HapticFeedback?.notificationOccurred("success");
}

function card(x, opts = {}) {
  const fav = opts.favorite ? '<button data-act="fav" class="on">♥ В избранном</button>' : '<button data-act="fav" class="secondary">♡ В избранное</button>';
  const chat = opts.chat === false ? "" : '<button data-act="chat">Написать</button>';
  const status = opts.showStatus ? `<span class="status ${STATUS[x.status]?.[1] || ""}">${STATUS[x.status]?.[0] || esc(x.status)}</span>` : "";
  return `<article class="listing" data-id="${x.id}">
    <div class="listing-top"><div><h3>${esc(x.title)}</h3><p>📍 ${esc(x.city)}</p></div><span class="tag">${esc(CAT[x.category] || x.category)}</span></div>
    <div class="price">${esc(x.price || "Цена договорная")}</div>
    <p>${esc(x.description || "Подробности уточняйте у автора")}</p>
    ${status}
    ${opts.noActions ? "" : `<div class="actions">${chat}${fav}</div>`}
  </article>`;
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

async function loadProfile() {
  const box = $("#myListings");
  try {
    const items = await api("/api/me/listings");
    box.innerHTML = items.length ? items.map((x) => card(x, {showStatus: true, noActions: true})).join("")
      : '<div class="empty">Вы ещё не публиковали объявления. Нажмите «Добавить».</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function showView(name) {
  for (const v of ["home", "saved", "chats", "chat", "profile"]) {
    const el = $("#view-" + v);
    if (el) el.hidden = v !== name;
  }
  document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  if (name !== "chat") clearInterval(chatTimer);
  if (name === "home") loadListings();
  if (name === "saved") loadFavorites();
  if (name === "chats") loadConversations();
  if (name === "profile") loadProfile();
  window.scrollTo({top: 0});
}

// Действия на карточках (делегирование событий)
document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (btn) {
    const article = btn.closest("[data-id]");
    const id = article.dataset.id;
    try {
      if (btn.dataset.act === "fav") {
        const r = await api(`/api/favorites/${id}`, {method: "POST"});
        btn.textContent = r.favorited ? "♥ В избранном" : "♡ В избранное";
        btn.classList.toggle("on", r.favorited);
        btn.classList.toggle("secondary", !r.favorited);
        toast(r.favorited ? "Добавлено в избранное" : "Убрано из избранного");
      }
      if (btn.dataset.act === "chat") {
        const c = await api(`/api/listings/${id}/conversation`, {method: "POST"});
        openChat(c.id, article.querySelector("h3")?.textContent);
      }
    } catch (err) { toast(err.message); }
    return;
  }
  const convo = e.target.closest("[data-chat]");
  if (convo) { openChat(convo.dataset.chat, convo.dataset.title); return; }
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
$("#addBtn").onclick = () => $("#modal").classList.remove("hidden");
$("#closeModal").onclick = () => $("#modal").classList.add("hidden");
$("#chatBack").onclick = () => showView("chats");

$("#listingForm").onsubmit = async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  try {
    await api("/api/listings", {method: "POST", body: JSON.stringify(data)});
    e.target.reset();
    $("#modal").classList.add("hidden");
    toast("Объявление отправлено на модерацию.");
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

loadListings();
