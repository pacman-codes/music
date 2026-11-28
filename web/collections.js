"use strict";
// Views of the shared SQLite library; all playback is delivered to Telegram.
(() => {
  if (!tg || !initData) return;
  const historyHost=byId("history-results"), smartHost=byId("smart-results");
  let historyMode="tracks", historyTicket=0, smartTicket=0, smartView=null;
  function report(id,error){show(id,error.message);toast(error.message)}
  async function sendTrack(id){
    await post("/api/tracks/"+id+"/play");
    toast("Отправлено в Telegram");
  }
  async function sendCollection(url){
    const data=await post(url);
    toast("Отправлено в Telegram: "+data.sent);
    await refreshQueue();
  }
  function trackRow(track){
    const row=element("div","item");
    const info=element("div","info");
    info.append(element("div","name",track.title),element("div","sub",track.artist));
    const details=element("div","track-actions");
    details.hidden=true;
    const toggle=action("⋯",async()=>{
      details.hidden=!details.hidden;
      toggle.setAttribute("aria-expanded",String(!details.hidden));
    });
    toggle.setAttribute("aria-label","Действия: "+track.title);
    toggle.setAttribute("aria-expanded","false");
    const head=element("div","track-head");
    head.append(info,action("▶",()=>sendTrack(track.id)),toggle);
    details.append(action("Следующим",()=>queueAddNext(track.id)));
    const picker=element("div","track-actions playlist-picker");
    details.append(action("＋ Плейлист",()=>choosePlaylistForTrack(track,picker)));
    row.classList.add("track-card");
    row.append(head,details,picker);
    return row;
  }
  async function loadHistory(mode=historyMode,before=null){
    const ticket=++historyTicket;
    historyMode=mode;
    byId("history-tracks").setAttribute("aria-pressed",String(mode==="tracks"));
    byId("history-queues").setAttribute("aria-pressed",String(mode==="queues"));
    historyHost.replaceChildren();
    show("history-status","Загружаю…");
    const data=await api("/api/history?mode="+mode+(before===null?"":"&before="+before));
    if(ticket!==historyTicket)return;
    show("history-status",mode==="tracks"?"Последние отправленные песни":"Последние отправленные подборки");
    for(const entry of data.results){
      if(mode==="tracks")historyHost.append(trackRow({id:entry.track_id,title:entry.title,artist:entry.artist}));
      else{
        const row=element("div","item");
        const info=element("div","info");
        info.append(element("div","name",entry.label),
          element("div","sub",entry.delivered_count+" треков отправлено"));
        row.append(info,action("↻ Повторить",()=>sendCollection("/api/history/queues/"+entry.queue_id+"/replay")));
        historyHost.append(row);
      }
    }
    if(!data.results.length)historyHost.append(element("div","empty","История пока пуста."));
    const nav=element("div","actions");
    if(before!==null)nav.append(action("← К последним",()=>loadHistory(mode)));
    if(data.next_before!==null)nav.append(action("Дальше →",()=>loadHistory(mode,data.next_before)));
    historyHost.append(nav);
  }
  async function loadSmart(){
    const ticket=++smartTicket;
    smartView=null;
    smartHost.replaceChildren();show("smart-status","Загружаю подборки…");
    const data=await api("/api/smart-playlists");
    if(ticket!==smartTicket)return;
    show("smart-status","");
    for(const rule of data.results){
      const row=element("div","item playlist-row");
      const art=element("div","playlist-art","✦");
      art.setAttribute("aria-hidden","true");
      const info=element("button","playlist-open");
      info.type="button";
      info.append(element("span","name",rule.title),
        element("span","sub",rule.count+" треков"),element("span","smart-description",rule.description));
      info.addEventListener("click",()=>openSmart(rule.key).catch(e=>report("smart-status",e)));
      row.append(art,info);
      if(rule.count>0&&rule.count<=1000)row.append(action("▶",()=>sendCollection("/api/smart-playlists/"+rule.key+"/play")));
      smartHost.append(row);
    }
  }
  async function openSmart(key,page=0){
    const ticket=++smartTicket;
    smartView={key,page};
    smartHost.replaceChildren(action("← Все подборки",loadSmart));
    show("smart-status","Загружаю…");
    const data=await api("/api/smart-playlists/"+encodeURIComponent(key)+"?page="+page);
    if(ticket!==smartTicket)return;
    smartView={key,page:data.page};
    show("smart-status","");
    const header=element("div","item track-card");
    header.append(action("← Все подборки",loadSmart),element("h3","",data.title),
      element("div","catalog-caption",data.description+" · "+data.total+" треков"));
    if(data.total>0&&data.total<=1000){
      const controls=element("div","track-actions");
      controls.append(action("▶ Слушать",()=>sendCollection("/api/smart-playlists/"+key+"/play")),
        action("🔀 Перемешать",()=>sendCollection("/api/smart-playlists/"+key+"/play?shuffle=true")));
      header.append(controls);
    }
    smartHost.replaceChildren(header);
    if(!data.total)smartHost.append(element("div","empty","Пока нет подходящих песен."));
    for(const track of data.tracks)smartHost.append(trackRow(track));
    const nav=element("div","actions");
    if(data.page>0)nav.append(action("←",()=>openSmart(key,data.page-1)));
    nav.append(element("span","catalog-caption",(data.page+1)+"/"+data.pages));
    if(data.page+1<data.pages)nav.append(action("→",()=>openSmart(key,data.page+1)));
    smartHost.append(nav);
  }
  byId("history-tracks").addEventListener("click",()=>loadHistory("tracks").catch(e=>report("history-status",e)));
  byId("history-queues").addEventListener("click",()=>loadHistory("queues").catch(e=>report("history-status",e)));
  byId("history-refresh").addEventListener("click",()=>loadHistory().catch(e=>report("history-status",e)));
  const refreshSmart=()=> (smartView?openSmart(smartView.key,smartView.page):loadSmart()).catch(e=>report("smart-status",e));
  byId("smart-refresh").addEventListener("click",refreshSmart);
  for(const button of document.querySelectorAll('button[data-route="history"]'))
    button.addEventListener("click",()=>loadHistory().catch(e=>report("history-status",e)));
  for(const button of document.querySelectorAll('button[data-route="smart"]'))
    button.addEventListener("click",refreshSmart);
  document.addEventListener("visibilitychange",()=>{
    if(document.hidden)return;
    const route=byId("main").dataset.activeScreen;
    if(route==="history")loadHistory().catch(e=>report("history-status",e));
    if(route==="smart")refreshSmart();
  });
})();
