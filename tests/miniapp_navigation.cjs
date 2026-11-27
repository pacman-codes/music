// Offline navigation regression with a minimal DOM; no Telegram credentials.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const code = fs.readFileSync("web/navigation.js","utf8");
const html = fs.readFileSync("web/index.html","utf8");

function run(initData) {
  function node(data={}, nav=false) {
    const classes = new Set(), attrs = new Map(), events = {};
    return {
      dataset:data, hidden:false, events, attrs,
      classList:{toggle(name,on){if(on) classes.add(name);else classes.delete(name)},contains:name=>classes.has(name)},
      closest:selector=>selector==="nav" && nav,
      setAttribute:(name,val)=>attrs.set(name,val),removeAttribute:name=>attrs.delete(name),
      addEventListener:(name,fn)=>events[name]=fn,
    };
  }
  const screens = [...html.matchAll(/<section[^>]*data-screen="([^"]+)"[^>]*>/g)].map(m=>{
    const n=node({screen:m[1]});n.hidden=/\bhidden\b/.test(m[0]);return n;
  });
  const buttons = ["home","search","library","albums","queue","more","artists"].map(r=>node({route:r},true));
  const main=node();main.querySelectorAll=()=>screens;
  const nav=node();nav.hidden=true;
  const back={visible:false,show(){this.visible=true},hide(){this.visible=false},onClick(fn){this.click=fn}};
  const window={Telegram:{WebApp:{initData,BackButton:back}},scrollTo(){}};
  const document={getElementById:id=>id==="main"?main:nav,querySelectorAll:()=>buttons};
  vm.runInNewContext(code,{window,document});
  return {window,nav,main,back,screens,buttons};
}
const denied=run("");
assert.equal(denied.nav.hidden,true);
assert.equal(denied.window.navigateMusicScreen,undefined);
const ui=run("owner-context");
assert.equal(ui.nav.hidden,false);
assert.equal(ui.main.dataset.activeScreen,"home");
for(const route of ["search","library","albums","queue","more","artists","home"]) {
  const button=ui.buttons.find(b=>b.dataset.route===route);
  button.events.click();
  assert.equal(ui.main.dataset.activeScreen,route);
  for(const section of ui.screens)
    assert.equal(section.classList.contains("screen-inactive"),section.dataset.screen!==route);
  assert.equal(button.attrs.get("aria-current"),"page");
  assert.equal(ui.back.visible,route!=="home");
}
ui.window.navigateMusicScreen("search");
assert.equal(ui.screens.find(s=>s.dataset.screen==="search"&&s.hidden).hidden,true);
ui.back.click();
assert.equal(ui.main.dataset.activeScreen,"home");
assert.equal(ui.window.navigateMusicScreen("bad"),false);
assert.equal(ui.main.dataset.activeScreen,"home");
console.log("Mini App navigation checks passed");
