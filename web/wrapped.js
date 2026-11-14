"use strict";
/* Music Wrapped: personal stats, no invented playback telemetry or minutes. */
function renderWrapped(stats){
  const host=byId("wrapped-content");
  host.replaceChildren();
  const facts=[
    ["🎧 Отправок в Telegram",stats.deliveries],
    ["🎶 Уникальных отправленных",stats.distinct_sent_tracks],
    ["♥ В избранном",stats.favorites_count],
    ["⭐ Оценок",stats.rated_count],
    ["🧭 Новых находок",stats.saved_discoveries],
    ["💿 Сохранено из каталога",stats.imported_catalog_audio],
  ];
  for(const [name,value] of facts){
    const card=element("div","wrapped-tile");
    card.append(element("div","wrapped-value",String(value)),
                element("div","wrapped-label",name));
    host.append(card);
  }
  const makePanel=(title)=>{
    const panel=element("section","wrapped-panel");
    panel.append(element("h3","",title));
    host.append(panel);
    return panel;
  };
  let panel=makePanel("🎸 Самые частые отправки по артистам");
  if(!stats.top_artists.length) panel.append(element("p","","Пока нет отправленных треков"));
  for(const row of stats.top_artists.slice(0,6)){
    panel.append(element("div","wrapped-list-row",
      row.artist+" · "+row.deliveries+" отправок"));
  }
  panel=makePanel("♥ Музыкальный профиль");
  if(!stats.favorite_artists.length) panel.append(element("p","","Добавь песни в избранное"));
  for(const item of stats.favorite_artists.slice(0,6)){
    panel.append(element("div","wrapped-list-row",
      item.artist+" · "+item.liked_tracks+" любимых · средняя "+
      item.average_rating+"/10"));
  }
  panel=makePanel("⭐ Лучшие оценки");
  for(const item of stats.highly_rated.slice(0,6)){
    panel.append(element("div","wrapped-list-row",
      item.artist+" — "+item.title+" · "+item.score+"/10"));
  }
  panel=makePanel("📆 Отправки за последние 7 дней");
  const chart=element("div","wrapped-chart");
  const max=Math.max(1,...stats.daily_deliveries.map(item=>item.deliveries));
  for(const day of stats.daily_deliveries){
    const col=element("div","wrapped-column");
    const bar=element("div","wrapped-bar");
    bar.style.height=(10+Math.round(day.deliveries/max*90))+"%";
    bar.title=day.date+" · "+day.deliveries+" отправок";
    col.append(bar,element("small","",day.date.slice(5)));
    chart.append(col);
  }
  panel.append(chart);
  panel=makePanel("✅ Ручная обратная связь");
  panel.append(element("p","",
    stats.manual_completions+" отмеченных дослушиваний · "+
    stats.manual_skips+" отмеченных пропусков"));
  panel.append(element("p","catalog-hint",
    "Отправка песни не означает, что она была воспроизведена."));
}

async function refreshWrapped(){
  const period=byId("wrapped-period").value;
  const host=byId("wrapped-content");
  host.replaceChildren(element("div","empty","Собираю статистику…"));
  const report=await api("/api/stats/wrapped?period="+encodeURIComponent(period));
  renderWrapped(report);
}

if(tg&&initData){
  byId("wrapped-refresh").addEventListener("click",()=>{
    refreshWrapped().catch(error=>toast(error.message));
  });
  byId("wrapped-period").addEventListener("change",()=>{
    refreshWrapped().catch(error=>toast(error.message));
  });
  refreshWrapped().catch(error=>toast(error.message));
}
