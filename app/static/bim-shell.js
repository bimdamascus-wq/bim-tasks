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
})();
