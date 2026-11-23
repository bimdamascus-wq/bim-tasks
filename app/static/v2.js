/* BIM Tasks v2 — shared helpers for the new interface (owner 2026-11-23). */
(()=>{
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ar=v=>String(v??'').replace(/\d/g,d=>'٠١٢٣٤٥٦٧٨٩'[d]);
const parseD=v=>{if(!v)return null;const d=new Date(String(v).replace(' ','T'));return isNaN(d)?null:d};
const utc=v=>parseD(String(v||'').includes('T')?v:String(v||'').replace(' ','T')+'Z');
const day=d=>new Intl.DateTimeFormat('ar-SY',{weekday:'long',day:'numeric',month:'long'}).format(d);
const full=d=>new Intl.DateTimeFormat('ar-SY',{weekday:'long',day:'numeric',month:'long',hour:'numeric',minute:'2-digit'}).format(d);
const time=d=>new Intl.DateTimeFormat('ar-SY',{hour:'numeric',minute:'2-digit'}).format(d);
const same=(a,b)=>a.toDateString()===b.toDateString();
function dur(ms){const m=Math.max(1,Math.round(ms/60000));if(m<60)return ar(m)+' دقيقة';const h=Math.round(m/60);if(h<24)return ar(h)+' ساعة';return ar(Math.round(h/24))+' يوم'}
const DONE=['تم','ملغاة'];
const isLate=t=>!DONE.includes(t.status)&&parseD(t.due_at)&&parseD(t.due_at)<new Date();
const isToday=t=>!DONE.includes(t.status)&&!isLate(t)&&parseD(t.due_at)&&same(parseD(t.due_at),new Date());
const isAlert=t=>t.source==='telegram_alert'&&!DONE.includes(t.status);
const doneToday=t=>t.status==='تم'&&utc(t.completed_at)&&same(utc(t.completed_at),new Date());
function when(t){
  if(t.status==='تم'){const d=utc(t.completed_at);return `<span class="done-t">خلصت${d?' '+time(d):''}</span>`}
  if(t.status==='ملغاة')return '<span class="soft">ملغاة</span>';
  const d=parseD(t.due_at);if(!d)return '<span class="soft">بلا موعد</span>';
  if(d<new Date())return `<span class="late-t">متأخرة ${dur(Date.now()-d)}</span>`;
  return same(d,new Date())?`<span class="due-t">اليوم ${time(d)}</span>`:`<span class="soft">${full(d)}</span>`;
}
function status(t){const c=isLate(t)?'late':{'تم':'done','قيد التنفيذ':'work','بدأت':'work','بانتظار':'wait'}[t.status]||'';return `<span class="vs ${c}">${isLate(t)?'متأخرة':esc(t.status)}</span>`}
function toast(msg,err){let t=$('vToast');if(!t){t=document.createElement('div');t.id='vToast';t.className='v-toast';t.setAttribute('role','status');document.body.appendChild(t)}
  t.textContent=msg;t.className='v-toast show'+(err?' err':'');clearTimeout(t._h);t._h=setTimeout(()=>t.className='v-toast',3000)}
async function api(url,opt){const r=await fetch(url,Object.assign({cache:'no-store'},opt));if(r.status===401){location.href='/login';throw new Error('')}
  const x=await r.json().catch(()=>({}));if(!r.ok)throw new Error(x.detail||'ما زبطت العملية');return x}
const send=(url,body,method='POST')=>api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
async function act(el,p,ok,after){if(el)el.disabled=true;try{const x=await p;toast(ok);if(after)await after(x);return x}catch(e){if(e.message)toast(e.message,true)}finally{if(el)el.disabled=false}}
function remindWhatsapp(id,after){const w=window.open('','_blank');send(`/api/tasks/${id}/remind`,{channel:'whatsapp'}).then(x=>{if(w)w.location=x.url;else location.href=x.url;after&&after()}).catch(e=>{if(w)w.close();toast(e.message,true)})}
function quickAdd(form,input,hint,onDone){let parsed=null,pt;const btn=form.querySelector('button');
  input.addEventListener('input',()=>{const v=input.value.trim();btn.disabled=!v;clearTimeout(pt);if(!v){hint.textContent='';return}
    pt=setTimeout(async()=>{try{parsed=await api('/api/v2/parse-task?text='+encodeURIComponent(v));parsed._for=v;hint.textContent=parsed.employee?'إلى '+parsed.employee:'بلا موظف'}catch{}},250)});
  form.addEventListener('submit',async e=>{e.preventDefault();const v=input.value.trim();if(!v)return;btn.disabled=true;
    try{if(!parsed||parsed._for!==v)parsed=await api('/api/v2/parse-task?text='+encodeURIComponent(v));
      const x=await send('/api/tasks',{employee_id:parsed.employee_id,title:parsed.title||v,priority:parsed.priority||'عادي',reminder:{type:'بدون'}});
      input.value='';hint.textContent='';toast('✓ انضافت المهمة');onDone&&onDone(x)}catch(err){toast(err.message,true);btn.disabled=false}})}
window.V={$,esc,ar,parseD,utc,day,full,time,same,dur,DONE,isLate,isToday,isAlert,doneToday,when,status,toast,api,send,act,remindWhatsapp,quickAdd};
const s=$('vSearch');if(s)s.addEventListener('keydown',e=>{if(e.key==='Enter'&&s.value.trim())location.href='/v2/tasks?q='+encodeURIComponent(s.value.trim())});
})();
