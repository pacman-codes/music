"use strict";
/* Private AniList metadata UI. Runs after app.js; no iframe video playback. */
let animeItems = [];
let animeMine = new Map();
let showingMyAnime = false;
const animeStates = [
  ["planned","📌 В планах"],["watching","▶️ Смотрю"],
  ["completed","✅ Просмотрено"],["on_hold","⏸ Пауза"],["dropped","🚫 Брошено"],
];

function animeToast(error) { toast(error?.message || "Ошибка аниме-каталога"); }

function renderAnimeResults(rows) {
  animeItems=rows;
  const host=byId("anime-results");
  host.replaceChildren();
  if (!rows.length) {
    host.append(element("div","empty","Пока пусто. Найди аниме по названию."));
    return;
  }
  for (const anime of rows) {
    const saved=animeMine.get(anime.id);
    const card=element("div","item track-card");
    const head=element("div","track-head");
    const info=element("div","info");
    info.append(element("div","name",anime.title));
    info.append(element("div","sub",[
      anime.year, anime.format, anime.episodes ? anime.episodes+" серий" : "число серий неизвестно",
    ].filter(Boolean).join(" · ")));
    const link=element("a","catalog-link","AniList ↗");
    if (Number.isSafeInteger(anime.id) && anime.id > 0) {
      link.href="https://anilist.co/anime/"+anime.id;
      link.rel="noopener noreferrer";
      link.target="_blank";
    }
    head.append(info,link);
    card.append(head);
    const controls=element("div","track-actions");
    const state=element("select","track-rating");
    state.setAttribute("aria-label","Статус: "+anime.title);
    const empty=element("option","","— В список");
    empty.value="";
    state.append(empty);
    for (const [key,label] of animeStates) {
      const option=element("option","",label);
      option.value=key;
      state.append(option);
    }
    state.value=saved?.watch_status || "";
    state.addEventListener("change",async () => {
      state.disabled=true;
      try {
        if (state.value) {
          await post("/api/anime/watchlist/"+anime.id,{
            watch_status:state.value,
            progress:saved?.progress ?? 0,
            score:saved?.score ?? null,
          });
        } else {
          await api("/api/anime/watchlist/"+anime.id,{method:"DELETE"});
        }
        await refreshAnimeSaved();
        toast("Список аниме обновлён");
        renderAnimeResults(showingMyAnime?Array.from(animeMine.values()):animeItems);
      } catch(error) { animeToast(error); state.value=saved?.watch_status||""; }
      finally { state.disabled=false; }
    });
    controls.append(state);
    if (saved) {
      controls.append(action("📺 Серия "+saved.progress, async()=>{
        const value=window.prompt("Сколько серий просмотрено?",String(saved.progress));
        if (value===null) return;
        const progress=Number(value.trim());
        if (!Number.isSafeInteger(progress) || progress<0 ||
            (anime.episodes && progress>anime.episodes)) {
          throw new Error("Некорректное количество серий");
        }
        await post("/api/anime/watchlist/"+anime.id,{
          watch_status:saved.watch_status,progress,score:saved.score,
        });
        await refreshAnimeSaved();
        renderAnimeResults(showingMyAnime?Array.from(animeMine.values()):animeItems);
      }));
      controls.append(action("⭐ "+(saved.score||"—")+"/10", async()=>{
        const value=window.prompt("Оценка 1–10",String(saved.score || ""));
        if (value===null) return;
        const score=Number(value.trim());
        if (!Number.isInteger(score) || score<1 || score>10) {
          throw new Error("Поставь оценку от 1 до 10");
        }
        await post("/api/anime/watchlist/"+anime.id,{
          watch_status:saved.watch_status,progress:saved.progress,score,
        });
        await refreshAnimeSaved();
        renderAnimeResults(showingMyAnime?Array.from(animeMine.values()):animeItems);
      }));
    }
    card.append(controls);
    host.append(card);
  }
}

async function refreshAnimeSaved() {
  const response=await api("/api/anime/watchlist");
  animeMine=new Map(response.results.map(item=>[item.id,item]));
}

async function animeSearch(q) {
  show("anime-status","Ищу аниме в AniList…");
  const data=await api("/api/anime/search?q="+encodeURIComponent(q));
  await refreshAnimeSaved();
  showingMyAnime=false;
  show("anime-status",data.results.length+" найдено · только каталог");
  renderAnimeResults(data.results);
}

if (tg && initData) {
  byId("anime-form").addEventListener("submit",event=>{
    event.preventDefault();
    const query=byId("anime-query").value.trim();
    if (query.length>=2) animeSearch(query).catch(animeToast);
  });
  byId("anime-watchlist").addEventListener("click",async()=>{
    try {
      await refreshAnimeSaved();
      showingMyAnime=true;
      show("anime-status","Мой список: "+animeMine.size);
      renderAnimeResults(Array.from(animeMine.values()));
    } catch(error) { animeToast(error); }
  });
}
