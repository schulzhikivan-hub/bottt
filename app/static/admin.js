const $ = (s) => document.querySelector(s);
const esc = (s = "") => String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const CAT = {job: "Работа", housing: "Жильё", marketplace: "Барахолка"};

function key() { return sessionStorage.getItem("adminKey") || ""; }

async function api(path, options = {}) {
  const r = await fetch(path, {...options, headers: {"X-Admin-Key": key(), "Content-Type": "application/json"}});
  if (r.status === 401) throw new Error("Неверный админ-ключ");
  if (!r.ok) throw new Error("Ошибка сервера (" + r.status + ")");
  return r.json();
}

function render(items) {
  $("#items").innerHTML = items.length ? items.map((x) => `
    <div class="card" data-id="${x.id}">
      <b>#${x.id} ${esc(x.title)}</b>
      <div class="meta">${esc(CAT[x.category] || x.category)} · ${esc(x.city)} · ${esc(x.price || "цена не указана")}</div>
      <p>${esc(x.description || "")}</p>
      <p class="muted">Контакт: ${esc(x.contact || "—")}</p>
      <button class="ok" data-act="approve">Одобрить</button>
      <button class="no" data-act="reject">Отклонить</button>
    </div>`).join("") : '<div class="card muted">Очередь модерации пуста.</div>';
}

async function load() {
  $("#status").textContent = "Загрузка…";
  try {
    render(await api("/api/admin/listings"));
    $("#status").textContent = "Обновлено " + new Date().toLocaleTimeString("ru-RU");
  } catch (e) {
    $("#status").textContent = e.message;
  }
}

async function decide(id, decision) {
  try {
    await api(`/api/admin/listings/${id}/${decision}`, {method: "POST"});
    await load();
  } catch (e) {
    $("#status").textContent = e.message;
  }
}

$("#enter").onclick = () => {
  const k = $("#key").value.trim();
  if (!k) return;
  sessionStorage.setItem("adminKey", k);
  $("#login").hidden = true;
  $("#queue").hidden = false;
  load();
  loadDating();
};
$("#reload").onclick = () => { load(); loadDating(); };

async function loadDating() {
  const box = $("#dating");
  try {
    const rows = await api("/api/admin/dating?status=pending");
    box.innerHTML = rows.length ? rows.map((p) => `
      <div class="card" data-pid="${p.id}">
        <b>Анкета #${p.id}</b> · ${p.age} лет · ${p.gender === "f" ? "ж" : "м"} · ${esc(p.city)}
        <p>${esc(p.bio)}</p>
        <img class="thumb" data-photo="${p.id}" alt="" style="max-width:200px;border-radius:10px" ${p.has_photo ? "" : "hidden"}>
        <p><button class="ok" data-dec="approve">Опубликовать</button>
        <button class="no" data-dec="reject">Отклонить</button></p>
      </div>`).join("") : '<div class="card muted">Анкет на проверке нет.</div>';
    // Фото загружаем с ключом (картинка по обычной ссылке не отправит заголовок)
    for (const img of document.querySelectorAll("img[data-photo]")) {
      if (img.hidden) continue;
      const r = await fetch(`/api/admin/dating/${img.dataset.photo}/photo`, {headers: {"X-Admin-Key": key()}});
      if (r.ok) img.src = URL.createObjectURL(await r.blob());
    }
  } catch (e) { box.innerHTML = `<div class="card muted">${esc(e.message)}</div>`; }
}

$("#dating").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-dec]");
  if (!btn) return;
  const id = btn.closest("[data-pid]").dataset.pid;
  try {
    await api(`/api/admin/dating/${id}/${btn.dataset.dec}`, {method: "POST"});
    loadDating();
  } catch (err) { $("#status").textContent = err.message; }
});
$("#items").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const id = btn.closest(".card").dataset.id;
  decide(id, btn.dataset.act);
});

if (key()) {
  $("#login").hidden = true;
  $("#queue").hidden = false;
  load();
  loadDating();
}
