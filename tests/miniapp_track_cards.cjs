// Offline DOM regression for compact track actions; no Telegram credentials.
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
class Node {
  constructor(tag){this.tagName=tag;this.childNodes=[];this.attrs=new Map();this.events={};this.hidden=false;this.textContent="";this.className="";this.value="";}
  append(...nodes){this.childNodes.push(...nodes)}
  replaceChildren(...nodes){this.childNodes=nodes}
  setAttribute(name,val){this.attrs.set(name,val)}
  getAttribute(name){return this.attrs.get(name)}
  addEventListener(name,fn){this.events[name]=fn}
  querySelector(selector){return all(this).find(n=>n.className.split(" ").includes(selector.slice(1)))}
}
function all(node){return [node,...node.childNodes.flatMap(all)]}
function find(node,klass){return all(node).find(n=>n.className.split(" ").includes(klass))}
function button(node,label){return all(node).find(n=>n.tagName==="button"&&n.textContent.includes(label))}
const nodes=new Map();
const document={createElement:tag=>new Node(tag),getElementById:id=>{
  if(!nodes.has(id))nodes.set(id,new Node("div"));return nodes.get(id);
}};
const ctx=vm.createContext({document,window:{},setTimeout,clearTimeout,console});
vm.runInContext(fs.readFileSync("web/app.js","utf8"),ctx);
const render=code=>vm.runInContext(code,ctx);
const rows=()=>nodes.get("tracks").childNodes;
render('var tracksForTest=[{id:1,title:"First",artist:"Artist",liked:true,rating:9,decision:"add",recording:{provenance:"confirmed"},reported_skips:1,reported_completions:2},{id:2,title:"Second",artist:"Band",liked:false}];renderTracks(tracksForTest)');
let [first,second]=rows();
assert.equal(find(first,"track-details").hidden,true);
assert.equal(find(first,"heart").getAttribute("aria-pressed"),"true");
assert.match(find(first,"sub").textContent,/9\/10.*Добавлю/);
assert.equal(find(first,"track-play").getAttribute("aria-label"),"Отправить в Telegram: First");
const toggle=find(first,"track-more");
assert.equal(toggle.getAttribute("aria-controls"),find(first,"track-details").id);
toggle.events.click();
assert.equal(find(first,"track-details").hidden,false);
assert.equal(toggle.getAttribute("aria-expanded"),"true");
assert.deepEqual(find(first,"decision-select").childNodes.map(n=>n.value),["","maybe","add","never","later","interesting"]);
assert.equal(find(first,"track-rating").childNodes.length,11);
for(const label of ["Следующим","Обновить теги","Пропустил","Дослушал","Радио","Связано","Исполнитель","Плейлист"])
  assert.ok(button(find(first,"track-details"),label),label);
find(second,"track-more").events.click();
assert.equal(find(first,"track-details").hidden,true);
assert.equal(find(second,"track-details").hidden,false);
render("renderTracks(tracksForTest)");
[first,second]=rows();
assert.equal(find(first,"track-details").hidden,true);
assert.equal(find(second,"track-details").hidden,false);
find(second,"track-more").events.click();
assert.equal(find(second,"track-details").hidden,true);
render('fingerprintEnabled=true;renderTracks(tracksForTest)');
assert.ok(button(rows()[0],"Узнать песню"));

render('var catalogForTest=[{mbid:"00000000-0000-0000-0000-000000000001",title:"Unlinked",artist:"Band",possible_match_track_id:1,variant:true},{mbid:"00000000-0000-0000-0000-000000000002",title:"Saved",artist:"Artist",local_track_id:2}];renderExternalCatalog(catalogForTest)');
let [unlinked,linked]=nodes.get("catalog-results").childNodes;
assert.equal(find(unlinked,"track-details").hidden,true);
assert.match(find(unlinked,"sub").textContent,/Без аудио/);
assert.equal(find(unlinked,"track-play"),undefined);
assert.ok(find(unlinked,"compact-warning"));
assert.ok(find(linked,"track-play"));
assert.equal(find(linked,"track-details").hidden,true);
find(unlinked,"track-more").events.click();
assert.ok(button(find(unlinked,"track-details"),"Это та запись"));
assert.ok(button(find(unlinked,"track-details"),"Отложить"));
assert.equal(find(unlinked,"decision-select").childNodes.length,6);
render("renderExternalCatalog(catalogForTest,false,1)");
[unlinked,linked]=nodes.get("catalog-results").childNodes;
assert.equal(find(unlinked,"track-details").hidden,true);
assert.ok(button(find(unlinked,"compact-match"),"Это та запись"));
assert.equal(find(unlinked,"track-play"),undefined);
console.log("Compact track card checks passed");
