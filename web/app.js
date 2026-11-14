"use strict";
const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";
const byId = (id) => document.getElementById(id);
const show = (id, text) => { byId(id).textContent = text; };
let toastTimer;
let directImportEnabled = false;
let youtubeSearchEnabled = false;
let youtubeDownloadEnabled = false;
let lastCatalogView = {rows:[], fromWishlist:false, matchingTrackId:null};
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
  const host = byId("tracks");
  host.replaceChildren();
  show("count", String(items.length));
  if (!items.length) {
    host.append(element("div", "empty", "Треков пока нет. Отправь музыку боту."));
  }
  for (const t of items) {
    const row = element("div", "item track-card");
    const head = element("div", "track-head");
    const info = element("div", "info");
    info.append(element("div", "name", t.title),
                element("div", "sub", t.artist));
    head.append(info, action(t.liked ? "♥" : "♡", async () => {
      if (t.liked) {
        await api("/api/like/" + t.id, {method: "DELETE"});
      } else {
        await post("/api/like/" + t.id);
      }
      await refreshTracks();
    }, t.liked ? "heart liked" : "heart"));

    const actions = element("div", "track-actions");
    actions.append(action("▶ Слушать", async () => {
      await post("/api/tracks/" + t.id + "/play");
      toast("Песня отправлена в Telegram ▶");
      tg?.HapticFeedback?.notificationOccurred("success");
    }));
    const rating = element("select", "track-rating");
    rating.setAttribute("aria-label", "Оценка от 1 до 10 для " + t.title);
    const empty = element("option", "", "⭐ —");
    empty.value = "";
    rating.append(empty);
    for (let score=1; score<=10; score++) {
      const option = element("option", "", "⭐ " + score);
      option.value = String(score);
      rating.append(option);
    }
    rating.value = t.rating == null ? "" : String(t.rating);
    rating.addEventListener("change", async () => {
      rating.disabled = true;
      try {
        if (rating.value === "") {
          await api("/api/rating/" + t.id, {method: "DELETE"});
          toast("Оценка убрана");
        } else {
          await post("/api/rating/" + t.id, {score: Number(rating.value)});
          toast("Оценка сохранена: " + rating.value + "/10");
        }
        await refreshTracks();
      } catch (error) {
        rating.value = t.rating == null ? "" : String(t.rating);
        toast(error.message);
      } finally {
        rating.disabled = false;
      }
    });
    actions.append(rating);
    actions.append(action("⏭ Пропустил", async () => {
      await post("/api/report/" + t.id, {event: "skip"});
      toast("Пропуск отмечен вручную");
      await refreshTracks();
    }, "secondary-action"));
    actions.append(action("✅ Дослушал", async () => {
      await post("/api/report/" + t.id, {event: "complete"});
      toast("Дослушивание отмечено вручную");
      await refreshTracks();
    }, "secondary-action"));
    actions.append(action("🎸 Радио", async () => {
      show("catalog-status", "Подбираю музыку по этому треку…");
      const result=await api("/api/tracks/" + t.id + "/radio");
      show("catalog-status", "По «" + t.title + "»: " +
        result.results.length + " новых записей, пока без аудиофайлов");
      renderExternalCatalog(result.results);
      byId("catalog-heading").scrollIntoView({behavior:"smooth"});
    }, "secondary-action"));
    actions.append(action(t.recording ? "🔗 Связано" : "🔍 Найти запись", async () => {
      if (t.recording) {
        if (t.recording.provenance !== "confirmed") {
          toast("Запись привязана при импорте и не меняется вручную");
          return;
        }
        if (!window.confirm("Убрать подтверждённую связь MusicBrainz с этим треком?")) return;
        await api("/api/tracks/" + t.id + "/recording", {method:"DELETE"});
        toast("Связь удалена");
        await refreshTracks();
        return;
      }
      show("catalog-status", "Ищу возможные записи для «" + t.title + "»…");
      const found=await api("/api/tracks/" + t.id + "/match-suggestions");
      renderExternalCatalog(found.results, false, t.id);
      show("catalog-status", "Выбери точное совпадение для трека #" + t.id +
        ". Названия могут совпадать у разных версий.");
      byId("catalog-heading").scrollIntoView({behavior:"smooth"});
    }, "secondary-action"));
    actions.append(action("＋ Плейлист", async () => {
      const list = (await api("/api/playlists")).filter(p => p.kind === "custom");
      if (!list.length) { toast("Сначала создай плейлист"); return; }
      const choice = window.prompt("Выбери номер плейлиста:\n" +
        list.map(p => p.id + " — " + p.name).join("\n"));
      if (!choice) return;
      const target = list.find(p => String(p.id) === choice.trim());
      if (!target) throw new Error("Неверный номер плейлиста");
      await post("/api/playlists/" + target.id + "/add", {track_id: t.id});
      toast("Добавлено в «" + target.name + "»");
      await refreshPlaylists();
    }, "secondary-action"));
    row.append(head, actions);
    if (t.reported_skips || t.reported_completions) {
      row.append(element("div", "track-feedback",
        "Ручные отметки за 30 дней: ⏭ " + t.reported_skips +
        " · ✅ " + t.reported_completions));
    }
    host.append(row);
  }
}


function renderExternalCatalog(rows, fromWishlist=false, matchingTrackId=null) {
  lastCatalogView = {rows, fromWishlist, matchingTrackId};
  const host = byId("catalog-results");
  host.replaceChildren();
  if (!rows.length) {
    host.append(element("div", "empty", "Ничего не найдено."));
    return;
  }
  for (const item of rows) {
    const card = element("div", "item track-card");
    const header = element("div", "track-head");
    const info = element("div", "info");
    info.append(element("div", "name", item.title),
                element("div", "sub", item.artist));
    header.append(info);
    const controls = element("div", "track-actions");
    if (item.local_track_id) {
      controls.append(element("span", "catalog-available", "✓ Связано с треком #" + item.local_track_id));
      controls.append(action("▶ Слушать", async () => {
        await post("/api/tracks/" + item.local_track_id + "/play");
        toast("Песня отправлена в Telegram ▶");
        tg?.HapticFeedback?.notificationOccurred("success");
      }));
    } else if (item.possible_match_track_id) {
      controls.append(element("span", "catalog-unavailable",
        "Возможно совпадение с #" + item.possible_match_track_id + " — ещё не подтверждено"));
    } else {
      controls.append(element("span", "catalog-unavailable", "Пока без аудио"));
    }
    const toMatch = matchingTrackId ?? item.possible_match_track_id;
    if (toMatch && item.local_track_id == null) {
      controls.append(action("🔗 Это та запись", async () => {
        const ok = window.confirm(
          "Подтвердить, что файл #" + toMatch + " — именно «" +
          item.artist + " — " + item.title + "»?\n" +
          "Разные версии и live-записи следует связывать только при уверенности."
        );
        if (!ok) return;
        await post("/api/tracks/" + toMatch + "/recording", {mbid:item.mbid});
        toast("Совпадение подтверждено");
        await refreshTracks();
        item.local_track_id = toMatch;
        item.match_confirmed = true;
        item.possible_match_track_id = null;
        renderExternalCatalog(rows, fromWishlist, matchingTrackId);
      }, "secondary-action"));
    }
    if (!item.local_track_id && youtubeSearchEnabled && item.mbid) {
      controls.append(action("▶ Найти на YouTube", async () => {
        show("youtube-status", "Ищу возможные совпадения по названию и длительности…");
        const result=await api("/api/discovery/" +
          encodeURIComponent(item.mbid) + "/youtube-suggestions");
        show("youtube-status", "Для «" + item.title + "»: " +
          result.suggestions.length + " вариантов. Совпадение не гарантировано.");
        renderYouTubeResults(result.suggestions);
        byId("youtube-section").scrollIntoView({behavior:"smooth"});
      }, "secondary-action"));
    }
    if (!item.local_track_id && directImportEnabled) {
      controls.append(action("📥 В Telegram", async () => {
        const url = window.prompt(
          "Прямая HTTPS-ссылка на MP3/M4A из разрешённого источника. " +
          "Это не YouTube-поиск и не торрент:"
        );
        if (!url) return;
        const result = await post(
          "/api/discovery/import/" + encodeURIComponent(item.mbid),
          {url}
        );
        if (result.reused) {
          toast("Песня уже сохранена в Telegram");
        } else {
          toast("Аудио загружено в Telegram 🎵");
        }
        // Refresh metadata badges; never create fake audio in Mini App.
        if (fromWishlist) await showExternalWishlist();
        else await externalSearch(byId("catalog-query").value.trim() || item.title);
      }, "secondary-action"));
    }
    controls.append(action(
      item.in_wishlist || fromWishlist ? "📌 Убрать" : "📌 Отложить",
      async () => {
        const id = encodeURIComponent(item.mbid);
        if (item.in_wishlist || fromWishlist) {
          await api("/api/discovery/wishlist/" + id, {method:"DELETE"});
          toast("Удалено из отложенного");
        } else {
          await post("/api/discovery/wishlist/" + id);
          toast("Сохранено в «Отложено»");
        }
        if (fromWishlist) {
          await showExternalWishlist();
        } else {
          item.in_wishlist = !item.in_wishlist;
          renderExternalCatalog(rows);
        }
      },
      "secondary-action"
    ));
    if (/^[0-9a-f]{8}-[0-9a-f-]{27,28}$/i.test(item.mbid)) {
      const link = element("a", "catalog-link", "MusicBrainz ↗");
      link.href = "https://musicbrainz.org/recording/" + encodeURIComponent(item.mbid);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      controls.append(link);
    }
    card.append(header, controls);
    if (item.variant) {
      card.append(element("div", "catalog-caption", "Может быть ремиксом, live или другой версией."));
    }
    host.append(card);
  }
}

async function externalSearch(query) {
  show("catalog-status", "Ищу записи в MusicBrainz…");
  const value = await api("/api/discovery/search?q=" + encodeURIComponent(query));
  show("catalog-status", "Найдено " + value.results.length +
    " записей · это каталог, ещё не Telegram-аудио.");
  renderExternalCatalog(value.results);
}

async function personalDiscover() {
  show("catalog-status", "Подбираю новые песни по твоим оценкам…");
  const value = await api("/api/discovery/for-you");
  show("catalog-status", "✨ На основе: " + value.seed_artists.join(", ") +
       ". " + value.results.length + " новых записей — пока без аудио.");
  renderExternalCatalog(value.results);
}

async function externalRadio() {
  const seed = byId("artist-seed").value.trim();
  const difficulty = byId("radio-difficulty").value;
  show("catalog-status", "Подбираю похожие записи через ListenBrainz…");
  const qs = new URLSearchParams({artist:seed, difficulty});
  const value = await api("/api/discovery/radio?" + qs.toString());
  show("catalog-status", "Радио по «" + value.artist_seed + "»: " +
    value.results.length + " предложений без аудиофайлов.");
  renderExternalCatalog(value.results);
}

async function showExternalWishlist() {
  const value = await api("/api/discovery/wishlist");
  show("catalog-status", "Отложенная музыка: " + value.results.length + " записей");
  renderExternalCatalog(value.results, true);
}


function renderYouTubeResults(videos) {
  const host = byId("youtube-results");
  host.replaceChildren();
  if (!videos.length) {
    host.append(element("div", "empty", "Результатов нет или YouTube ограничил поиск."));
    return;
  }
  for (const video of videos) {
    const card = element("div", "item track-card");
    const header = element("div", "track-head");
    const info = element("div", "info");
    info.append(
      element("div", "name", video.title),
      element("div", "sub", video.channel +
        (video.duration_sec ? " · " + Math.floor(video.duration_sec / 60) + " мин" : ""))
    );
    header.append(info);
    const buttons = element("div", "track-actions");
    if (/^[A-Za-z0-9_-]{11}$/.test(video.video_id)) {
      const link=element("a","catalog-link","▶ YouTube");
      link.href="https://www.youtube.com/watch?v="+video.video_id;
      link.target="_blank";
      link.rel="noopener noreferrer";
      buttons.append(link);
    }
    if (video.match_score != null) {
      buttons.append(element("span","catalog-caption",
        "Совпадение: " + Math.round(video.match_score * 100) + "% (оценка, не гарантия)"));
      for (const caution of (video.warnings || [])) {
        buttons.append(element("span","catalog-unavailable","⚠ " + caution));
      }
    }
    if (video.track_id) {
      buttons.append(element("span","catalog-available",
        "✓ Уже сохранено как Telegram-аудио #" + video.track_id));
    } else if (youtubeDownloadEnabled) {
      buttons.append(action("🎧 В Telegram", async () => {
        const linkedRecording = video.recording_mbid || null;
        const prompt = linkedRecording
          ? "Подтверди две вещи:\n1. Это именно нужная запись MusicBrainz: " +
            linkedRecording + " (проверь live/remix и длительность).\n" +
            "2. У тебя есть право скачать её и отправить в Telegram.\n" +
            "Совпадение по названию само по себе не является подтверждением."
          : "Подтверждаешь, что у тебя есть право скачать этот материал " +
            "и сохранить его в Telegram?";
        if (!window.confirm(prompt)) return;
        const result=await post("/api/youtube/import/" +
          encodeURIComponent(video.video_id),
          linkedRecording ? {recording_mbid:linkedRecording} : {});
        toast(result.reused ?
          "Аудио отправлено повторно" : "MP3 отправлен в Telegram");
        video.track_id=result.track_id;
        if (linkedRecording) {
          toast("Сохранено с подтверждённым MusicBrainz ID ✅");
          await refreshTracks();
          const {rows, fromWishlist, matchingTrackId} = lastCatalogView;
          for (const item of rows) {
            if (item.mbid === linkedRecording) {
              item.local_track_id = result.track_id;
              item.match_confirmed = true;
              item.possible_match_track_id = null;
            }
          }
          renderExternalCatalog(rows, fromWishlist, matchingTrackId);
        }
        renderYouTubeResults(videos);
      }, "secondary-action"));
    } else {
      buttons.append(element("span","catalog-unavailable",
        "Загрузка отключена, доступен только поиск"));
    }
    card.append(header,buttons);
    host.append(card);
  }
}

async function youtubeSearch(query) {
  show("youtube-status","Ищу на YouTube…");
  const result=await api("/api/youtube/search?q="+encodeURIComponent(query));
  show("youtube-status",result.results.length+
       " результатов. Это ссылки на ролики, не локальная библиотека.");
  renderYouTubeResults(result.results);
}

async function refreshYoutubeSettings() {
  const status=await api("/api/youtube/status");
  youtubeSearchEnabled=status.search_enabled === true;
  youtubeDownloadEnabled=status.download_enabled === true;
  byId("youtube-section").hidden=!youtubeSearchEnabled;
}

async function refreshPlaylists(){ renderPlaylists(await api("/api/playlists")); }
async function refreshTracks() {
  const q=byId("query").value.trim();
  renderTracks(await api("/api/tracks"+(q?"?q="+encodeURIComponent(q):"")));
}
async function refreshImportSettings() {
  const config = await api("/api/discovery/import-config");
  directImportEnabled = config.enabled === true;
}
async function refresh() {
  await Promise.all([
    refreshPlaylists(), refreshTracks(),
    refreshImportSettings(), refreshYoutubeSettings()
  ]);
}
if (tg && initData) {
  tg.ready();
  tg.expand();
  show("status","Нативное прослушивание в Telegram");
  byId("wave").addEventListener("click",()=>sendQueue("wave").catch(e=>toast(e.message)));
  byId("discover").addEventListener("click",()=>sendQueue("discover").catch(e=>toast(e.message)));
  byId("youtube-form").addEventListener("submit", async event => {
    event.preventDefault();
    const query=byId("youtube-query").value.trim();
    if (query.length < 2 || !youtubeSearchEnabled) return;
    try { await youtubeSearch(query); }
    catch(error) {show("youtube-status","YouTube-поиск недоступен");toast(error.message);}
  });
  byId("catalog-form").addEventListener("submit", async event => {
    event.preventDefault();
    const query=byId("catalog-query").value.trim();
    if (query.length < 2) return;
    try { await externalSearch(query); }
    catch(error) { show("catalog-status", "Не удалось получить результаты"); toast(error.message); }
  });
  byId("artist-radio").addEventListener("click", async () => {
    try { await externalRadio(); }
    catch(error) { show("catalog-status", "Радио недоступно"); toast(error.message); }
  });
  byId("personal-discover").addEventListener("click", async () => {
    try { await personalDiscover(); }
    catch(error) { show("catalog-status","Не удалось собрать персональные открытия"); toast(error.message); }
  });
  byId("show-wishlist").addEventListener("click", () =>
    showExternalWishlist().catch(error => toast(error.message)));
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
