"use strict";
(() => {
  if(!tg||!initData)return;
  const host=byId("taste-profile-results");
  const genreHost=byId("taste-genres-results");
  let ticket=0,genreTicket=0;
  async function load(){
    const current=++ticket;
    show("taste-profile-status","Собираю профиль…");
    const data=await api("/api/taste/profile");
    if(current!==ticket)return;
    host.replaceChildren();
    const counts=element("div","taste-summary");
    for(const [label,value] of [
      ["Медиатека",data.library_count],["Доступно для Волны",data.eligible_count],
      ["Избранное",data.liked_count],["Оценено",data.rated_count]]){
      const metric=element("div","item taste-metric");
      metric.append(element("strong","",String(value)),element("span","sub",label));
      counts.append(metric);
    }
    host.append(counts,element("h3","","Исполнители в профиле"));
    for(const artist of data.artists){
      const row=element("div","item track-card taste-artist");
      const head=element("div","track-head");
      const info=element("div","info");
      info.append(element("div","name",artist.name),
        element("div","sub",artist.preferred?"Предпочитаемый исполнитель":"Слабый сигнал"));
      head.append(info,element("span","catalog-caption",String(artist.affinity.toFixed(2))));
      const meter=element("progress","taste-meter");
      meter.max=4.5;meter.value=artist.affinity;
      meter.setAttribute("aria-label","Внутренний вес "+artist.name);
      row.append(head,meter);
      host.append(row);
    }
    if(!data.artists.length)host.append(element("div","empty","Пока мало положительных сигналов."));
    host.append(element("p","catalog-hint","Вес — внутренний коэффициент от 0 до 4,5, а не процент совпадения."));
    host.append(element("h3","","Недавние предпочтения · 30 дней"));
    for(const artist of data.recent_positive_artists){
      const row=element("div","item");
      row.append(element("div","name",artist.name),element("span","sub",artist.tracks+" треков"));
      host.append(row);
    }
    if(!data.recent_positive_artists.length)host.append(element("div","empty","Нет недавних положительных отметок."));
    host.append(element("p","catalog-hint","Показываются текущие положительные оценки и реакции с недавней датой. История изменения оценок пока не хранится."));
    const names={familiar:"Любимые",similar:"Похожие",new:"Другие"};
    host.append(element("h3","","Состав доступной медиатеки"));
    for(const [key,value] of Object.entries(data.categories)){
      const row=element("div","item");
      row.append(element("div","name",names[key]||key),element("span","sub",String(value)));
      host.append(row);
    }
    host.append(action("⚙ Настройки Волны",async()=>{
      window.navigateMusicScreen("home");
      if(byId("wave-settings-panel").hidden)byId("wave-settings-open").click();
    }));
    show("taste-profile-status","Профиль обновлён");
  }
  async function genres(){
    const current=++genreTicket;
    genreHost.replaceChildren(element("div","catalog-caption","Запрашиваю MusicBrainz…"));
    try{
      const data=await api("/api/taste/profile/genres");
      if(current!==genreTicket)return;
      genreHost.replaceChildren();
      for(const artist of data.artists){
        const row=element("div","item track-card");
        row.append(element("div","name",artist.name),
          element("div","sub",artist.genres.join(" · ")||"Жанры не указаны"));
        genreHost.append(row);
      }
      if(!data.requested)genreHost.append(element("div","empty","Сначала сохрани исполнителей."));
      if(data.unavailable)genreHost.append(element("div","catalog-caption","Недоступно артистов: "+data.unavailable));
    }catch(error){
      if(current===genreTicket)genreHost.replaceChildren(element("div","catalog-caption",error.message));
    }
  }
  const refresh=()=>load().catch(error=>{show("taste-profile-status",error.message);toast(error.message)});
  for(const button of document.querySelectorAll('button[data-route="taste"]'))button.addEventListener("click",refresh);
  byId("taste-profile-refresh").addEventListener("click",refresh);
  byId("taste-genres-load").addEventListener("click",genres);
})();
