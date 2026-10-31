/* Page shell of the 2026-10-31 design (base.html): menu on phones, search, date, server connection state. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
function init(){
  const side=$('dsSide'),menu=$('dsMenu'),shade=$('dsShade');
  if(menu&&side){
    const set=open=>{document.body.classList.toggle('ds-nav-open',open);menu.setAttribute('aria-expanded',String(open));menu.setAttribute('aria-label',open?'إغلاق القائمة':'فتح القائمة');if(shade)shade.hidden=!open};
    menu.addEventListener('click',()=>set(!document.body.classList.contains('ds-nav-open')));
    shade&&shade.addEventListener('click',()=>set(false));
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('ds-nav-open')){set(false);menu.focus()}});
    const act=side.querySelector('.is-active');if(act&&act.scrollIntoView)act.scrollIntoView({block:'nearest'});
  }
  const q=$('dsSearch');
  if(q)q.addEventListener('keydown',e=>{if(e.key==='Enter'&&q.value.trim())location.href='/tasks-table?q='+encodeURIComponent(q.value.trim())});
  const t=$('dsToday');
  if(t)t.textContent=new Intl.DateTimeFormat('ar-SY',{weekday:'long',day:'numeric',month:'long'}).format(new Date());
  const st=$('dsState');
  async function health(){
    if(!st)return;
    try{const c=new AbortController(),h=setTimeout(()=>c.abort(),7000);
      let r=await fetch('/api/client-status',{cache:'no-store',signal:c.signal});
      if(r.status===403)r=await fetch('/api/me',{cache:'no-store',signal:c.signal});
      clearTimeout(h);
      if(r.status===401){location.href='/login';return}
      if(r.ok){st.className='ds-state is-on';st.lastElementChild.textContent='متصل';return}
    }catch(e){}
    st.className='ds-state is-off';st.lastElementChild.textContent='غير متصل';
  }
  health();setInterval(health,15000);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();

/* Colour words: status/priority cells become pills, selects carry their value for colouring. Display only. */
const WORDS=new Set(['جديدة','بدأت','قيد التنفيذ','بانتظار','تم','ملغاة','عاجلة','عالية','عادي','منخفضة','نشط','معطل','مربوط','غير مربوط']);
function tagCells(root){
  for(const el of root.querySelectorAll('td,span.status-pill,span.prio-pill')){
    if(el.dataset.dsTag||el.children.length)continue;
    const t=(el.textContent||'').trim();
    if(!WORDS.has(t))continue;
    el.dataset.dsTag='1';
    if(el.tagName==='TD'){const s=document.createElement('span');s.className='ds-tag';s.dataset.v=t;s.textContent=t;el.textContent='';el.append(s)}
    else{el.classList.add('ds-tag');el.dataset.v=t}
  }
  for(const sel of root.querySelectorAll('select')){
    if(!sel.dataset.dsWatch){sel.dataset.dsWatch='1';sel.addEventListener('change',()=>{sel.dataset.v=sel.value})}
    if(WORDS.has(sel.value))sel.dataset.v=sel.value;else delete sel.dataset.v;
  }
}
/* Native date inputs show the browser's (often English) format: show the same value in Arabic on top of it. */
const fmtDT=new Intl.DateTimeFormat('ar-SY',{day:'numeric',month:'long',year:'numeric',hour:'numeric',minute:'2-digit'});
const fmtD=new Intl.DateTimeFormat('ar-SY',{day:'numeric',month:'long',year:'numeric'});
function dateText(inp){const v=inp.value;if(!v)return '';const d=new Date(inp.type==='date'?v+'T00:00':v);if(isNaN(d))return '';return (inp.type==='date'?fmtD:fmtDT).format(d).replace(/AM/i,'ص').replace(/PM/i,'م')}
function arDates(root){
  for(const inp of root.querySelectorAll('input[type=datetime-local],input[type=date]')){
    if(!inp.dataset.dsDate){
      inp.dataset.dsDate='1';
      const w=document.createElement('span');w.className='ds-date';inp.parentNode.insertBefore(w,inp);w.append(inp);
      const t=document.createElement('span');t.className='ds-date-text';t.setAttribute('aria-hidden','true');w.append(t);
      for(const ev of ['input','change','blur'])inp.addEventListener(ev,()=>refreshDate(inp));
    }
    refreshDate(inp);
  }
}
function refreshDate(inp){const w=inp.parentNode;if(!w||!w.classList||!w.classList.contains('ds-date'))return;const t=dateText(inp);const el=w.lastElementChild;el.textContent=t||(inp.type==='date'?'بدون تاريخ':'بدون موعد');el.classList.toggle('is-empty',!t);w.classList.add('has-text')}
let pending=null;
function enhance(){pending=null;try{tagCells(document);arDates(document)}catch(e){}}
function schedule(){if(!pending)pending=setTimeout(enhance,150)}
function startEnhance(){enhance();new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});setInterval(()=>{for(const i of document.querySelectorAll('input[data-ds-date]'))refreshDate(i)},2000)}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',startEnhance);else startEnhance();
})();
