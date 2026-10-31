/* UI observations contain control captions only: never field values, form bodies or URLs with queries. */
(()=>{'use strict';if(location.pathname==='/login')return;let lastInput=Date.now(),lastBeat=0;
const send=(kind,label='')=>{fetch('/api/activity/events',{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-BIM-Activity':'1'},body:JSON.stringify({kind,label,page:location.pathname})}).catch(()=>{})};
document.addEventListener('click',e=>{lastInput=Date.now();const el=e.target.closest('button,a,summary,[role="button"],input[type="submit"]');if(!el||el.closest('[data-no-activity]'))return;let label=(el.getAttribute('aria-label')||el.textContent||el.getAttribute('title')||'زر').replace(/\s+/g,' ').trim().slice(0,100);send('click',label||'زر')},true);
document.addEventListener('change',e=>{if(!e.target.matches('select,input[type=checkbox],input[type=radio]'))return;lastInput=Date.now();const control=e.target;let label=control.id||control.name||'اختيار';const parent=control.closest('label');if(parent){const text=Array.from(parent.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join(' ').trim();if(text)label=text}send('click','تغيير اختيار: '+label.slice(0,80))},true);
document.addEventListener('submit',()=>{lastInput=Date.now();send('click','إرسال نموذج')},true);
for(const event of ['keydown','pointerdown','touchstart'])document.addEventListener(event,()=>{lastInput=Date.now()},{passive:true});
setInterval(()=>{if(document.visibilityState==='visible'&&Date.now()-lastInput<60000&&Date.now()-lastBeat>=30000){lastBeat=Date.now();send('heartbeat')}},30000);
})();
