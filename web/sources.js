"use strict";
/* Owner-only Mini App: imported favorites are metadata, not Telegram audio. */
(() => {
  const root = document.getElementById("source-tabs");
  if (!root) return;
  const items = document.getElementById("source-items");
  const status = document.getElementById("source-status");
  const pageText = document.getElementById("source-page");
  const prev = document.getElementById("source-prev");
  const next = document.getElementById("source-next");
  let selected = "yandex";
  let offset = 0;
  const step = 12;
  let total = 0;
  const names = {yandex:"🎵 Яндекс Музыка",youtube_music:"▶ YouTube Music"};
  const say = msg => { status.textContent = msg; };

  async function refresh() {
    const sources = await api("/api/sources");
    root.replaceChildren();
    if (!sources.length) {
      root.append(element("div","empty","Источники ещё не подключены"));
      items.replaceChildren();
      pageText.textContent = "";
      return;
    }
    if (!sources.some(s => s.provider === selected)) selected = sources[0].provider;
    for (const source of sources) {
      root.append(action(names[source.provider] + " · " + source.count,
        async () => {
          selected = source.provider;
          offset = 0;
          await refreshItems();
        }, source.provider === selected ? "primary" : ""));
    }
    await refreshItems();
  }

  async function refreshItems() {
    const data = await api("/api/sources/" + selected + "/tracks?limit=" + step + "&offset=" + offset);
    total = data.total;
    items.replaceChildren();
    pageText.textContent = total
      ? (offset + 1) + "–" + Math.min(total,offset + data.items.length) + " из " + total : "Пусто";
    prev.disabled = offset === 0;
    next.disabled = offset + step >= total;
    for (const track of data.items) {
      const row = element("div","item track-card");
      const details = element("div","info");
      details.append(element("div","name",track.title),element("div","sub",track.artist));
      const link = element("a","secondary-action","🎧 Открыть в сервисе");
      link.href = track.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.addEventListener("click",event=>{
        if (tg?.openLink) {
          event.preventDefault();
          tg.openLink(track.source_url);
        }
      });
      row.append(details,link);
      items.append(row);
    }
    if (!data.items.length) items.append(element("div","empty","Пока нет сохранённых песен."));
  }

  document.getElementById("source-yandex-sync").addEventListener("click", async event => {
    const button = event.currentTarget;
    const url = document.getElementById("source-yandex-url").value.trim();
    button.disabled = true;
    try {
      say("Получаю полный плейлист из Яндекс Музыки…");
      const result = await post("/api/sources/yandex/sync",{url});
      say("Яндекс: " + result.active + " лайков, новых " + result.added + ", удалено " + result.removed + ".");
      selected = "yandex";
      offset = 0;
      await refresh();
    } catch(err) {say(err.message);}
    finally {button.disabled = false;}
  });

  document.getElementById("source-yandex-repeat").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      say("Проверяю новые лайки…");
      const result = await post("/api/sources/yandex/refresh");
      say("Яндекс: " + result.active + " лайков, новых " + result.added + ".");
      selected="yandex";offset=0;await refresh();
    } catch(err){say(err.message);}
    finally {event.currentTarget.disabled=false;}
  });

  document.getElementById("source-youtube-sync").addEventListener("click", async event => {
    const button=event.currentTarget;
    const raw=document.getElementById("source-youtube-data").value;
    const file=document.getElementById("source-youtube-file").files?.[0];
    button.disabled = true;
    try {
      let content=raw;
      let filename="playlist.txt";
      if (file) {
        if (file.size > 1500000) throw new Error("Для файла свыше 1,5 МиБ используй /ytmimport в чате.");
        content=await file.text();
        filename=file.name;
      } else if (/^(?:\uFEFF)?(?:Video ID|Song Title|Artist Name),/.test(content.trimStart())) {
        filename="library.csv";
      }
      if (!content.trim()) throw new Error("Выбери CSV/TXT файл или вставь текст музыкальной библиотеки.");
      say("Импортирую музыку без скачивания аудио…");
      const result=await post("/api/sources/youtube_music/import",{filename,content});
      say("YouTube Music: " + result.active + " любимых, новых " + result.added + ". " +
          "Повторный импорт не удаляет старые записи.");
      selected="youtube_music";offset=0;await refresh();
    } catch(err){say(err.message);}
    finally {button.disabled=false;}
  });

  document.getElementById("sources-refresh").addEventListener("click",
    ()=>refresh().catch(err=>say(err.message)));
  prev.addEventListener("click",()=>{offset=Math.max(0,offset-step);refreshItems().catch(err=>say(err.message));});
  next.addEventListener("click",()=>{offset+=step;refreshItems().catch(err=>say(err.message));});
  if (initData) refresh().catch(err=>say(err.message));
})();
