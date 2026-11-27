"use strict";
// Artist profiles share the bot's MusicBrainz IDs and favorite collection.
(() => {
  if (!tg || !initData) return;
  const host = byId("artists-results");
  const status = text => show("artists-status",text);
  let request = 0;
  let mode = "saved";
  let offset = 0;
  const path = id => "/api/artists/"+encodeURIComponent(id);
  function nameRow(artist) {
    const row=element("div","item artist-row");
    const art=element("div","artist-avatar","♫");
    art.setAttribute("aria-hidden","true");
    const info=element("button","playlist-open");
    info.type="button";
    info.append(element("span","name",artist.name),
      element("span","sub",artist.disambiguation||"Исполнитель"));
    info.addEventListener("click",()=>openArtist(artist.mbid).catch(e=>toast(e.message)));
    const heart=action(artist.saved?"♥":"♡",async()=>{
      await api(path(artist.mbid),{method:"PUT",body:JSON.stringify({saved:!artist.saved})});
      artist.saved=!artist.saved;
      if(mode==="saved"&&!artist.saved) await loadSaved(offset);
      else {
        heart.textContent=artist.saved?"♥":"♡";
        heart.setAttribute("aria-pressed",String(Boolean(artist.saved)));
      }
    },"heart");
    heart.setAttribute("aria-label","Избранное: "+artist.name);
    heart.setAttribute("aria-pressed",String(Boolean(artist.saved)));
    row.append(art,info,heart);
    return row;
  }
  async function loadSaved(pageOffset=0) {
    const ticket=++request;
    mode="saved";offset=pageOffset;
    host.replaceChildren();status("Загружаю избранное…");
    const data=await api("/api/artists?offset="+pageOffset);
    if(ticket!==request)return;
    if(!data.results.length&&pageOffset>0)return loadSaved(0);
    status("Избранные исполнители");
    if(!data.results.length)host.append(element("div","empty","Найди исполнителя и добавь его в избранное."));
    for(const artist of data.results)host.append(nameRow(artist));
    const nav=element("div","actions");
    if(pageOffset>0)nav.append(action("←",()=>loadSaved(Math.max(0,pageOffset-20))));
    if(data.has_more)nav.append(action("→",()=>loadSaved(pageOffset+20)));
    host.append(nav);
  }
  async function search(query) {
    const ticket=++request;
    mode="search";host.replaceChildren();status("Ищу исполнителей…");
    const data=await api("/api/artists/search?q="+encodeURIComponent(query));
    if(ticket!==request)return;
    status("Найдено: "+data.results.length);
    if(!data.results.length)host.append(element("div","empty","Исполнители не найдены."));
    for(const artist of data.results)host.append(nameRow(artist));
  }
  async function openArtist(id,page=0) {
    const ticket=++request;
    mode="detail";host.replaceChildren();status("Открываю исполнителя…");
    const artist=await api(path(id)+"?page="+page);
    if(ticket!==request)return;
    status("");
    const card=element("div","item track-card");
    card.append(action("← Исполнители",()=>loadSaved()));
    const head=element("div","playlist-detail-title");
    const avatar=element("div","artist-avatar large","♫");
    const info=element("div","info");
    info.append(element("div","name",artist.name),
      element("div","sub",artist.disambiguation||"Исполнитель"));
    head.append(avatar,info);card.append(head);
    const controls=element("div","track-actions");
    const favorite=action(artist.saved?"♥ В избранном":"♡ В избранное",async()=>{
      const result=await api(path(id),{method:"PUT",body:JSON.stringify({saved:!artist.saved})});
      artist.saved=result.saved;
      favorite.textContent=result.saved?"♥ В избранном":"♡ В избранное";
      favorite.setAttribute("aria-pressed",String(result.saved));
    });
    favorite.setAttribute("aria-pressed",String(Boolean(artist.saved)));
    controls.append(favorite);
    if(artist.total&&!artist.truncated)controls.append(action("▶ Слушать сохранённые",async()=>{
      const result=await post(path(id)+"/play");
      toast("Отправлено в Telegram: "+result.sent);
      await refreshQueue();
    }));
    controls.append(action("Радио",async()=>{
      const result=await post(path(id)+"/radio");
      if(ticket!==request)return;
      renderExternalCatalog(result.results);
      show("catalog-status","Радио по "+result.artist+" · "+result.results.length+" находок");
      window.navigateMusicScreen("search");
    }));
    card.append(controls);host.append(card);
    const disc=element("div","list artist-discography");
    const discHeading=element("h3","","Альбомы и EP");
    host.append(discHeading,disc);
    let discRequest=0;
    async function releases(p=0){
      const serial=++discRequest;
      disc.replaceChildren(element("div","catalog-caption","Загружаю дискографию…"));
      try{
        const data=await api(path(id)+"/discography?page="+p);
        if(ticket!==request||serial!==discRequest)return;
        disc.replaceChildren();
        const grid=element("div","artist-album-grid");
        for(const album of data.results){
          const button=action("",async()=>{
            const resolved=await api("/api/artist-release-groups/"+encodeURIComponent(album.mbid));
            if(ticket!==request||serial!==discRequest)return;
            window.navigateMusicScreen("albums");
            await openAlbum(resolved.release_id);
          },"artist-album");
          button.append(albumCover(album),element("span","name",album.title),
            element("span","sub",[album.date?.slice(0,4),album.kind].filter(Boolean).join(" · ")));
          grid.append(button);
        }
        if(!data.results.length)disc.append(element("div","empty","На этой странице нет основных альбомов и EP."));
        disc.append(grid);
        const nav=element("div","actions");
        if(p>0)nav.append(action("←",()=>releases(p-1)));
        nav.append(element("span","catalog-caption","Страница "+(p+1)));
        if(data.has_more)nav.append(action("→",()=>releases(p+1)));
        disc.append(nav);
      }catch(error){
        if(ticket===request&&serial===discRequest)
          disc.replaceChildren(element("div","catalog-caption",error.message),action("Повторить",()=>releases(p)));
      }
    }
    const tracks=element("div","list");
    host.append(element("h3","","Сохранённые песни · "+artist.total+(artist.truncated?"+":"")),
      element("p","catalog-hint","Подтверждённые записи и совпадения имени в локальной медиатеке."),tracks);
    for(const t of artist.tracks){
      const row=element("div","item");
      const label=element("div","info");
      label.append(element("div","name",t.title),element("div","sub",t.artist));
      row.append(label,action("▶",async()=>{
        await post("/api/tracks/"+t.id+"/play");toast("Отправлено в Telegram");
      }),action("Следующим",()=>queueAddNext(t.id)));
      tracks.append(row);
    }
    if(!artist.total)tracks.append(element("div","empty","Сохранённых песен нет."));
    const nav=element("div","actions");
    if(artist.page>0)nav.append(action("←",()=>openArtist(id,artist.page-1)));
    if(artist.page+1<artist.pages)nav.append(action("→",()=>openArtist(id,artist.page+1)));
    tracks.append(nav);
    await releases();
  }
  function report(error){status(error.message);toast(error.message)}
  byId("artists-form").addEventListener("submit",event=>{
    event.preventDefault();
    const q=byId("artists-query").value.trim();
    if(q.length>=2)search(q).catch(report);
  });
  byId("artists-refresh").addEventListener("click",()=>loadSaved().catch(report));
  window.openMusicArtistSearch=async name=>{
    window.navigateMusicScreen("artists");
    const q=String(name||"").trim().slice(0,90);
    byId("artists-query").value=q;
    if(q.length>=2)await search(q);
    else await loadSaved();
  };
  for(const button of document.querySelectorAll('button[data-route="artists"]')){
    button.addEventListener("click",()=>{
      if(mode==="saved")loadSaved(offset).catch(report);
    });
  }
})();
