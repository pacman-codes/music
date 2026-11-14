"use strict";
/* Owner-only pending queue editor. Operates on Telegram-delivery queue,
   never pretends to modify currently playing native Telegram audio. */
let activeQueue=null;
const queueItemPath=(queueId)=>"/api/queue/"+queueId;

async function refreshQueue(){
  const state=await api("/api/queue/current");
  activeQueue=state;
  const host=byId("queue-items");
  host.replaceChildren();
  if(!state){
    show("queue-summary","Нет очереди. Запусти Волну или плейлист.");
    byId("queue-send").disabled=true;
    byId("queue-filters").hidden=true;
    return;
  }
  byId("queue-send").disabled=false;
  show("queue-summary",state.label+" · отправлено "+state.cursor+
       " · ожидает "+state.pending_total+
       (state.is_dynamic ? " · Волна продолжится новыми треками" : ""));
  byId("queue-filters").hidden=!state.is_dynamic;
  const filters=state.filters||{min_rating:0,liked_only:false,no_repeat_today:false};
  byId("queue-rating").value=String(filters.min_rating);
  byId("queue-liked").checked=filters.liked_only;
  byId("queue-no-repeat").checked=filters.no_repeat_today;
  if(!state.upcoming.length){
    host.append(element("div","empty",
      state.is_dynamic ? "Новая порция подберётся при продолжении." :
                         "Все песни этой очереди отправлены."));
  }
  for(const item of state.upcoming){
    const row=element("div","item track-card");
    const info=element("div","info");
    info.append(element("div","name",item.title),
                element("div","sub",item.artist+" · №"+(item.position+1)));
    row.append(info);
    const controls=element("div","track-actions");
    const move=async target=>{
      await api(queueItemPath(state.queue_id)+"/items",{
        method:"PUT",
        body:JSON.stringify({
          position:item.position,to_position:target,
          expected_cursor:state.cursor,
        }),
      });
      await refreshQueue();
    };
    if(item.position>state.cursor){
      controls.append(action("↑",()=>move(item.position-1),"secondary-action"));
    }
    if(item.position<state.cursor+state.pending_total-1){
      controls.append(action("↓",()=>move(item.position+1),"secondary-action"));
    }
    controls.append(action("✕ Убрать",async()=>{
      await api(queueItemPath(state.queue_id)+"/items/"+item.position,{
        method:"DELETE",
      });
      await refreshQueue();
    },"secondary-action"));
    row.append(controls);
    host.append(row);
  }
  if(state.pending_total>state.upcoming.length){
    host.append(element("div","empty",
      "Показано "+state.upcoming.length+" из "+state.pending_total+" будущих песен"));
  }
}

async function queueAddNext(trackId){
  if(!activeQueue) await refreshQueue();
  if(!activeQueue) throw new Error("Сначала запусти Волну или плейлист");
  await post(queueItemPath(activeQueue.queue_id)+"/items",{
    action:"next",track_id:trackId,expected_cursor:activeQueue.cursor,
  });
  await refreshQueue();
}

if(tg&&initData){
  byId("queue-refresh").addEventListener("click",()=>{
    refreshQueue().catch(error=>toast(error.message));
  });
  byId("queue-send").addEventListener("click",async()=>{
    const button=byId("queue-send");
    button.disabled=true;
    try {
      if(!activeQueue) throw new Error("Нет активной очереди");
      const result=await post(queueItemPath(activeQueue.queue_id)+"/send");
      toast("В Telegram отправлено "+result.sent+" песен");
      await refreshQueue();
    }catch(error){toast(error.message);}
    finally{button.disabled=false;}
  });
  byId("queue-save-filters").addEventListener("click",async()=>{
    try{
      if(!activeQueue||!activeQueue.is_dynamic)
        throw new Error("Фильтры работают в радио, а не в плейлисте");
      await api(queueItemPath(activeQueue.queue_id)+"/filters",{
        method:"PUT",
        body:JSON.stringify({
          min_rating:Number(byId("queue-rating").value),
          liked_only:byId("queue-liked").checked,
          no_repeat_today:byId("queue-no-repeat").checked,
        }),
      });
      toast("Фильтры применятся к следующей генерируемой порции");
      await refreshQueue();
    }catch(error){toast(error.message);}
  });
  refreshQueue().catch(error=>toast(error.message));
}
