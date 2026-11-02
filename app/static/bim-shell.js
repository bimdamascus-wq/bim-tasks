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
  if(q)q.addEventListener('keydown',e=>{if(e.key==='Enter'&&q.value.trim())location.href='/tasks?view=table&q='+encodeURIComponent(q.value.trim())});
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
  bell();
}
/* /tasks#table, #quick, #board, #cards open that view of "المهام" (old links and bookmarks). */
if(location.pathname==='/tasks'&&/^#(cards|table|quick|board)$/.test(location.hash)&&!new URLSearchParams(location.search).get('view')){
  location.replace('/tasks?view='+location.hash.slice(1));
}
/* Employee name -> employee profile (pages build their rows in JS). */
window.bimEmpLink=(id,name)=>id?`<a class="emp-link" href="/employee/${encodeURIComponent(id)}">${window.bimEsc?bimEsc(name):name}</a>`:(window.bimEsc?bimEsc(name||''):(name||''));
/* Notification bell (managers and supervisors): unread count, list, mark as read, click opens the task. */
function bell(){
  const box=$('dsBell');if(!box)return;
  const btn=$('dsBellBtn'),panel=$('dsBellPanel'),list=$('dsBellList'),count=$('dsBellCount'),all=$('dsBellAll');
  const ICON={late:'⏰',comment:'💬',attachment:'📎',done:'✅',new:'🆕'};
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const ar=v=>String(v??'').replace(/\d/g,d=>'٠١٢٣٤٥٦٧٨٩'[+d]);
  const when=v=>{const d=new Date(String(v).replace(' ','T')+'Z');if(isNaN(d))return '';const m=Math.round((Date.now()-d)/60000);
    if(m<1)return 'الآن';if(m<60)return 'منذ '+ar(m)+' د';if(m<1440)return 'منذ '+ar(Math.round(m/60))+' س';
    return new Intl.DateTimeFormat('ar-SY',{day:'numeric',month:'long',hour:'numeric',minute:'2-digit'}).format(d)};
  let items=[];
  async function load(){try{const r=await fetch('/api/inbox',{cache:'no-store'});if(!r.ok)return;const d=await r.json();items=d.items||[];
    count.hidden=!d.unread;count.textContent=d.unread>99?'٩٩+':ar(d.unread);btn.setAttribute('aria-label','التنبيهات'+(d.unread?': '+ar(d.unread)+' غير مقروءة':''));draw()}catch(e){}}
  function draw(){list.innerHTML=items.length?items.map(i=>`<a class="ds-bell-item${i.read_at?'':' is-unread'}" href="${i.task_id?'/task/'+i.task_id:'#'}" data-id="${i.id}"><span class="ic" aria-hidden="true">${ICON[i.kind]||'🔔'}</span><span class="tx">${esc(i.text)}<small>${when(i.created_at)}</small></span></a>`).join(''):'<p class="ds-bell-empty">لا توجد تنبيهات</p>'}
  const post=u=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
  const open=v=>{panel.hidden=!v;btn.setAttribute('aria-expanded',String(v));if(v)load()};
  btn.addEventListener('click',e=>{e.stopPropagation();open(panel.hidden)});
  document.addEventListener('click',e=>{if(!panel.hidden&&!box.contains(e.target))open(false)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!panel.hidden){open(false);btn.focus()}});
  list.addEventListener('click',async e=>{const a=e.target.closest('.ds-bell-item');if(!a)return;e.preventDefault();await post('/api/inbox/'+a.dataset.id+'/read');if(a.getAttribute('href')!=='#')location.href=a.getAttribute('href');else load()});
  all.addEventListener('click',async()=>{await post('/api/inbox/read-all');load()});
  load();setInterval(()=>{if(!document.hidden)load()},60000);
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
/* Standing rule (owner 2026-11-02): every table column is as wide as its longest cell, no wider. Short columns never
   wrap; only a long-text column (task title, notes, message...) may wrap, up to a sensible width (bim-ds.css). */
const LONG=/المهمة|المهام|العنوان|ملاحظ|الرسالة|النص|الوصف|التفاصيل|السبب|التعليق|الخطأ|الإجراء الذي|البيان|المحتوى|التعليمات/;
function fitTables(root){
  for(const t of root.querySelectorAll('table')){
    if(t.closest('.ds-side,.bim-excel-menu'))continue;
    const p=t.parentElement;
    if(p&&!t.closest('.scroll-box,.ds-tscroll,.table-wrap')&&getComputedStyle(p).overflowX==='visible'){const w=document.createElement('div');w.className='ds-tscroll';p.insertBefore(w,t);w.append(t)}
    const heads=[...(t.tHead?.rows[0]?.cells||[])];const body=t.tBodies[0];if(!body)continue;
    const n=Math.max(heads.length,...[...body.rows].slice(0,60).map(r=>r.cells.length),0);
    for(let i=0;i<n;i++){
      const h=heads[i];let long=h&&LONG.test(h.textContent||'');
      if(!long){let max=0;for(const r of [...body.rows].slice(0,80)){const c=r.cells[i];if(c&&c.colSpan===1&&!c.querySelector('input,select,textarea,button')){max=Math.max(max,(c.textContent||'').trim().length)}}long=max>48}
      if(h)h.classList.toggle('ds-col-wrap',!!long);
      for(const r of body.rows){const c=r.cells[i];if(c&&c.colSpan===1)c.classList.toggle('ds-col-wrap',!!long)}
    }
  }
}
let pending=null;
function enhance(){pending=null;try{tagCells(document);arDates(document);fitTables(document)}catch(e){}}
function schedule(){if(!pending)pending=setTimeout(enhance,150)}
function startEnhance(){enhance();new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});setInterval(()=>{for(const i of document.querySelectorAll('input[data-ds-date]'))refreshDate(i)},2000)}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',startEnhance);else startEnhance();
})();
