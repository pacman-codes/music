"use strict";
/* Optional TMDB metadata UI; player and URLs remain native Telegram only. */
let screenEntries=[];
let screenSaved=new Map();
let screenMineMode=false;

const screenStatuses=[
  ["planned","📌 Планирую"],["watching","▶ Смотрю"],
  ["completed","✅ Просмотрено"],["on_hold","⏸ Пауза"],
  ["dropped","🚫 Брошено"],
];
const screenKey=(media)=>media.media_type+":"+String(media.tmdb_id??media.id);

async function refreshScreenSaved(){
  const payload=await api("/api/screen/watchlist");
  screenSaved=new Map(payload.results.map(item=>[screenKey(item),item]));
}
function renderScreen(items){
  screenEntries=items;
  const host=byId("screen-results");
  host.replaceChildren();
  if(!items.length)host.append(element("div","empty","Список пуст."));
  for(const item of items){
    const saved=screenSaved.get(screenKey(item));
    const card=element("div","item track-card");
    const info=element("div","info");
    info.append(element("div","name",item.title),
      element("div","sub",[
        item.media_type==="tv"?"Сериал":"Фильм",
        item.release_year,
        item.average_score!=null?("TMDB "+item.average_score+"/10"):null
      ].filter(Boolean).join(" · ")));
    const head=element("div","track-head");
    head.append(info);
    const link=element("a","catalog-link","TMDB ↗");
    const safeId=Number(item.tmdb_id??item.id);
    if(Number.isInteger(safeId)&&safeId>0&&["movie","tv"].includes(item.media_type)){
      link.href="https://www.themoviedb.org/"+item.media_type+"/"+safeId;
      link.rel="noopener noreferrer";
      link.target="_blank";
      head.append(link);
    }
    card.append(head);
    const actions=element("div","track-actions");
    const choice=element("select","track-rating");
    const none=element("option","","— Добавить");
    none.value="";
    choice.append(none);
    for(const [key,label] of screenStatuses){
      const opt=element("option","",label);
      opt.value=key;
      choice.append(opt);
    }
    choice.value=saved?.watch_status||"";
    choice.setAttribute("aria-label","Статус: "+item.title);
    choice.addEventListener("change",async()=>{
      choice.disabled=true;
      try{
        if(!choice.value){
          await api("/api/screen/watchlist/"+item.media_type+"/"+safeId,
                    {method:"DELETE"});
        }else{
          await post("/api/screen/watchlist/"+item.media_type+"/"+safeId,{
            watch_status:choice.value,
            season:saved?.season??0,episode:saved?.episode??0,
            score:saved?.score??null,
          });
        }
        await refreshScreenSaved();
        renderScreen(screenMineMode?Array.from(screenSaved.values()):screenEntries);
      }catch(error){toast(error.message);choice.value=saved?.watch_status||"";}
      finally{choice.disabled=false;}
    });
    actions.append(choice);
    if(saved){
      if(item.media_type==="tv"){
        actions.append(action("📺 S"+saved.season+" E"+saved.episode,async()=>{
          const v=window.prompt("Текущий сезон и серия (пример: 2 5)",
                                saved.season+" "+saved.episode);
          if(v===null)return;
          const match=/^(\d+)\s+(\d+)$/.exec(v.trim());
          if(!match)throw new Error("Формат: сезон серия");
          const season=Number(match[1]),episode=Number(match[2]);
          if(season>1000||episode>10000)throw new Error("Слишком большое число");
          await post("/api/screen/watchlist/tv/"+safeId,{
            watch_status:saved.watch_status,season,episode,score:saved.score
          });
          await refreshScreenSaved();
          renderScreen(screenMineMode?Array.from(screenSaved.values()):screenEntries);
        }));
      }
      actions.append(action("⭐ "+(saved.score??"—")+"/10",async()=>{
        const v=window.prompt("Твоя оценка 1–10",String(saved.score??""));
        if(v===null)return;
        const score=Number(v.trim());
        if(!Number.isInteger(score)||score<1||score>10)
          throw new Error("Оценка должна быть 1–10");
        await post("/api/screen/watchlist/"+item.media_type+"/"+safeId,{
          watch_status:saved.watch_status,
          season:saved.season,episode:saved.episode,score
        });
        await refreshScreenSaved();
        renderScreen(screenMineMode?Array.from(screenSaved.values()):screenEntries);
      }));
    }
    card.append(actions);
    host.append(card);
  }
}

async function screenSearch(q){
  show("screen-status","Ищу фильм или сериал…");
  const data=await api("/api/screen/search?q="+encodeURIComponent(q));
  await refreshScreenSaved();
  screenMineMode=false;
  show("screen-status",data.results.length+" результатов. Только каталог, без видео.");
  renderScreen(data.results);
}

if(tg&&initData){
  api("/api/screen/status").then(value=>{
    byId("screen-section").hidden=!value.enabled;
  }).catch(()=>{byId("screen-section").hidden=true;});
  byId("screen-form").addEventListener("submit",event=>{
    event.preventDefault();
    const q=byId("screen-query").value.trim();
    if(q.length>=2)screenSearch(q).catch(err=>toast(err.message));
  });
  byId("screen-watchlist").addEventListener("click",async()=>{
    try{
      await refreshScreenSaved();
      screenMineMode=true;
      show("screen-status","Мой список: "+screenSaved.size);
      renderScreen(Array.from(screenSaved.values()));
    }catch(error){toast(error.message);}
  });
}
