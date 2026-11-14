"use strict";
const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";
const byId = (id) => document.getElementById(id);
const show = (id, text) => { byId(id).textContent = text; };
let toastTimer;
function toast(message) {
  const node = byId("toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 3400);
}
async function api(path, options={}) {
  if (!initData) throw new Error("Открой приложение из Telegram-бота");
  const res = await fetch(path, {
    ...options,
    headers: {
      "X-Telegram-Init-Data": initData,
      ...(options.body ? {"Content-Type": "application/json"} : {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || "Ошибка сервера");
  }
  return res.json();
}
const post = (path, value={}) => api(path, {method:"POST",body:JSON.stringify(value)});
function element(tag, klass, text) {
  const el = document.createElement(tag);
  if (klass) el.className = klass;
  if (text !== undefined) el.textContent = text;
  return el;
}
function action(label, fn, klass="") {
  const el = element("button", klass, label);
  el.type = "button";
  el.addEventListener("click", async () => {
    el.disabled = true;
    try { await fn(); }
    catch (error) { toast(error.message); }
    finally { el.disabled = false; }
  });
  return el;
}
async function sendQueue(kind, playlistId) {
  const result = await post("/api/queue", {kind, playlist_id:playlistId ?? null});
  toast(`Отправлено ${result.sent} треков в чат с ботом ▶️`);
  tg?.HapticFeedback?.notificationOccurred("success");
}
function renderPlaylists(items) {
  const host = byId("playlists");
  host.replaceChildren();
  if (!items.length) host.append(element("div","empty","Плейлистов пока нет"));
  for (const p of items) {
    const row = element("div","item");
    const info = element("div","info");
    info.append(element("div","name",p.name),
                element("div","sub",`${p.count} треков`));
    row.append(info,action("▶️ Слушать",()=>sendQueue(p.kind==="liked"?"liked":"playlist",p.id)));
    host.append(row);
  }
}
function renderTracks(items) {
  const host=byId("tracks");
  host.replaceChildren();
  show("count", String(items.length));
  if (!items.length) host.append(element("div","empty","Треков пока нет. Отправь музыку боту."));
  for (const t of items) {
    const row=element("div","item");
    const info=element("div","info");
    info.append(element("div","name",t.title),element("div","sub",t.artist));
    row.append(info,action(t.liked?"♥":"♡", async()=>{
      await post("/api/like/"+t.id);
      await refreshTracks();
    }, t.liked?"heart liked":"heart"));
    host.append(row);
  }
}
async function refreshPlaylists(){ renderPlaylists(await api("/api/playlists")); }
async function refreshTracks() {
  const q=byId("query").value.trim();
  renderTracks(await api("/api/tracks"+(q?"?q="+encodeURIComponent(q):"")));
}
async function refresh() { await Promise.all([refreshPlaylists(),refreshTracks()]); }
if (tg && initData) {
  tg.ready();
  tg.expand();
  show("status","Нативное прослушивание в Telegram");
  byId("wave").addEventListener("click",()=>sendQueue("wave").catch(e=>toast(e.message)));
  byId("discover").addEventListener("click",()=>sendQueue("discover").catch(e=>toast(e.message)));
  byId("create").addEventListener("click",async()=>{
    const name=window.prompt("Название плейлиста");
    if(!name) return;
    try {await post("/api/playlists",{name});await refreshPlaylists();}
    catch(e) {toast(e.message);}
  });
  let searchTimer;
  byId("query").addEventListener("input",()=>{
    clearTimeout(searchTimer);
    searchTimer=setTimeout(()=>refreshTracks().catch(e=>toast(e.message)),230);
  });
  refresh().catch(e=>{show("status","Не удалось открыть библиотеку");toast(e.message);});
} else {
  show("status","Открой это приложение через кнопку бота в Telegram.");
  byId("main").hidden = true;
}
