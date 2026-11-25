// Drag the edge of any table header to make that column wider or narrower (owner 2026-11-25: «أنا بدي أتحكم بالأعمدة»).
// Double-click the edge = back to automatic widths. Widths are remembered per page + table in this browser only.
(()=>{
const css=document.createElement('style');
css.textContent='th.bim-colw-th{position:relative}.bim-col-grip{position:absolute;top:0;bottom:0;inset-inline-end:-5px;width:10px;cursor:col-resize;z-index:3;touch-action:none;background:linear-gradient(to right,transparent 4px,rgba(255,255,255,.45) 4px,rgba(255,255,255,.45) 6px,transparent 6px)}.bim-col-grip:hover,.bim-col-grip.on{background:rgba(255,255,255,.55)}table.bim-colw-fixed td{overflow-wrap:anywhere}';
document.head.append(css);
const heads=t=>[...t.querySelectorAll('thead tr:first-child th')];
const key=(t,hs)=>{const v=new URLSearchParams(location.search).get('view');return 'bim-colw:'+location.pathname+(v?'?view='+v:'')+'|'+hs.map(h=>h.textContent.trim()).join('|')};
const read=k=>{try{return JSON.parse(localStorage.getItem(k)||'null')}catch(e){return null}};
const write=(k,v)=>{try{v?localStorage.setItem(k,JSON.stringify(v)):localStorage.removeItem(k)}catch(e){}};
function fix(t,hs,ws){hs.forEach((h,i)=>h.style.width=ws[i]+'px');t.style.width=ws.reduce((a,b)=>a+b,0)+'px';t.style.tableLayout='fixed';t.classList.add('bim-colw-fixed')}
function reset(t,hs){hs.forEach(h=>h.style.width='');t.style.width='';t.style.tableLayout='';t.classList.remove('bim-colw-fixed')}
function setup(t){
  const hs=heads(t);if(!hs.length||hs.every(h=>h.querySelector('.bim-col-grip')))return;
  const k=key(t,hs),saved=read(k);
  if(Array.isArray(saved)&&saved.length===hs.length)fix(t,hs,saved);
  hs.forEach((h,i)=>{if(h.querySelector('.bim-col-grip'))return;h.classList.add('bim-colw-th');
    const g=document.createElement('span');g.className='bim-col-grip';g.title='اسحب لتكبير أو تصغير العمود · دبل كليك للرجوع';
    g.addEventListener('click',e=>e.stopPropagation());
    g.addEventListener('dblclick',e=>{e.stopPropagation();reset(t,hs);write(k,null)});
    g.addEventListener('pointerdown',e=>{e.preventDefault();e.stopPropagation();
      const ws=hs.map(x=>x.getBoundingClientRect().width);fix(t,hs,ws);
      const rtl=getComputedStyle(t).direction==='rtl',x0=e.clientX,w0=ws[i];g.classList.add('on');g.setPointerCapture(e.pointerId);
      const move=ev=>{ws[i]=Math.max(40,w0+(rtl?x0-ev.clientX:ev.clientX-x0));fix(t,hs,ws)};
      const up=()=>{g.classList.remove('on');g.removeEventListener('pointermove',move);g.removeEventListener('pointerup',up);write(k,ws.map(Math.round))};
      g.addEventListener('pointermove',move);g.addEventListener('pointerup',up)});
    h.append(g)});
}
const scan=()=>document.querySelectorAll('main table').forEach(setup);
let q=false;new MutationObserver(()=>{if(q)return;q=true;requestAnimationFrame(()=>{q=false;scan()})}).observe(document.body,{childList:true,subtree:true});
scan();
})();
