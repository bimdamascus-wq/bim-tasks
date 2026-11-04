/* Task reminder form: three options (owner 2026-11-04). Used by the new-task window, the quick task list and the
   task page. bimReminder.render(box, current) draws it, bimReminder.value(box) reads it, bimReminder.edit(taskId,
   current, onSaved) opens it in a small window and saves it. Rules live in app/reminders.py. */
(function(){
  const ar=v=>String(v??'').replace(/\d/g,d=>'٠١٢٣٤٥٦٧٨٩'[d]);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const TYPES=[['بدون','بدون تذكير'],['مرة واحدة','مرة واحدة'],['يتكرر','يتكرر'],['قبل الموعد','قبل الموعد']];
  const UNITS=['ساعة','يوم','أسبوع','شهر'];
  const BEFORE=[[30,'٣٠ دقيقة'],[60,'ساعة'],[180,'٣ ساعات'],[1440,'يوم'],[2880,'يومين']];
  const PL={'ساعة':'ساعات','يوم':'أيام','أسبوع':'أسابيع','شهر':'أشهر'},E1={'ساعة':'كل ساعة','يوم':'كل يوم','أسبوع':'كل أسبوع','شهر':'كل شهر'},E2={'ساعة':'كل ساعتين','يوم':'كل يومين','أسبوع':'كل أسبوعين','شهر':'كل شهرين'};
  const every=(n,u)=>{n=Math.max(1,+n||1);if(n===1)return E1[u];if(n===2)return E2[u];return `كل ${ar(n)} ${n<=10?PL[u]:u}`};
  const local=d=>{const p=x=>String(x).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`};
  const nextNine=()=>{const d=new Date();d.setMinutes(0,0,0);if(d.getHours()>=9)d.setDate(d.getDate()+1);d.setHours(9);return local(d)};
  const when=v=>{if(!v)return '';const d=new Date(String(v).replace(' ','T'));if(isNaN(d))return '';return new Intl.DateTimeFormat('ar-SY',{weekday:'long',day:'numeric',month:'long',hour:'numeric',minute:'2-digit'}).format(d)};
  function css(){if(document.getElementById('bimRemCss'))return;const s=document.createElement('style');s.id='bimRemCss';s.textContent=`
  .brm{display:flex;flex-direction:column;gap:10px}
  .brm-seg{display:flex;flex-wrap:wrap;gap:6px}
  body.ds .brm-seg button.btn-light{min-height:38px!important;height:38px!important;padding:0 16px!important;border-radius:999px!important;border:1.5px solid #E2D8CA!important;background:#fff!important;color:#2B1D0E!important;font-weight:700!important;font-size:14.5px!important;box-shadow:none!important;width:auto!important}
  body.ds .brm-seg button.btn-light.on{background:#2B1D0E!important;border-color:#2B1D0E!important;color:#fff!important}
  .brm-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-weight:600}
  body.ds .brm-row>input,body.ds .brm-row>select{width:auto!important;flex:0 0 auto;min-width:0;margin:0!important}
  body.ds .brm-row>input[type=number]{width:90px!important}body.ds .brm-row>select{min-width:120px}
  body.ds .brm-row>input[type=datetime-local]{min-width:230px}
  .brm-row>label,.brm-row>span{white-space:nowrap}
  .brm-say{background:#F7F3EE;border-radius:10px;padding:8px 12px;font-size:14.5px;color:#3D434B}
  .brm-say b{color:#2B1D0E}.brm-warn{color:#A23B00}
  .brm-dlg{border:0;border-radius:16px;padding:0;width:min(520px,94vw);box-shadow:0 20px 60px rgba(0,0,0,.25)}
  .brm-dlg::backdrop{background:rgba(20,15,10,.35)}
  .brm-dlg-in{padding:18px 18px 14px;direction:rtl;display:flex;flex-direction:column;gap:12px}
  .brm-dlg h3{margin:0;font-size:19px}.brm-dlg .brm-t{color:#6A6F77;font-size:14px;margin-top:-8px}
  .brm-act{display:flex;justify-content:flex-start;gap:8px}`;document.head.appendChild(s)}
  function say(box){
    const v=read(box),out=box.querySelector('.brm-say');if(!out)return;
    if(v.type==='بدون'){out.innerHTML='لن يصل الموظف أي تذكير لهذه المهمة.';return}
    if(v.type==='مرة واحدة'){out.innerHTML=v.start_at?`سيصله تذكير واحد <b>${esc(when(v.start_at))}</b>.`:'<span class="brm-warn">اختر اليوم والساعة.</span>';return}
    if(v.type==='يتكرر'){out.innerHTML=`سيصله تذكير <b>${esc(every(v.every_n,v.every_unit))}</b>`+(v.start_at?`، أول مرة <b>${esc(when(v.start_at))}</b>`:'')+
      (v.every_unit==='ساعة'?'. التذكير بالساعات يعمل فقط في وقت دوام الموظف.':'.')+' ويتوقف تلقائياً عند إنجاز المهمة.';return}
    const due=box.dataset.due||'';out.innerHTML=`سيصله تذكير <b>قبل الموعد بـ ${esc((BEFORE.find(b=>b[0]===v.before_minutes)||[0,ar(v.before_minutes)+' دقيقة'])[1])}</b>`+(due?` (الموعد ${esc(when(due))})`:'. <span class="brm-warn">يجب أن يكون للمهمة موعد.</span>');
  }
  function read(box){
    const t=box.querySelector('.brm-seg .on')?.dataset.t||'بدون',r={type:t};
    if(t==='مرة واحدة')r.start_at=box.querySelector('[name=once]').value||'';
    if(t==='يتكرر'){r.every_n=+box.querySelector('[name=n]').value||1;r.every_unit=box.querySelector('[name=unit]').value;r.start_at=box.querySelector('[name=from]').value||''}
    if(t==='قبل الموعد')r.before_minutes=+box.querySelector('[name=before]').value;
    return r;
  }
  function render(box,cur){
    css();cur=cur||{};const t=TYPES.some(x=>x[0]===cur.type)?cur.type:'بدون';
    box.innerHTML=`<div class="brm"><div class="brm-seg" role="radiogroup" aria-label="التذكير">${TYPES.map(([k,l])=>`<button type="button" role="radio" data-t="${k}" class="btn-light${k===t?' on':''}" aria-checked="${k===t}">${l}</button>`).join('')}</div>
      <div class="brm-row" data-for="مرة واحدة"><label>اليوم والساعة</label><input type="datetime-local" name="once" value="${esc((cur.start_at||nextNine()).slice(0,16))}"></div>
      <div class="brm-row" data-for="يتكرر"><span>كل</span><input type="number" name="n" min="1" max="365" value="${esc(cur.every_n||1)}" aria-label="كل كم"><select name="unit" aria-label="الوحدة">${UNITS.map(u=>`<option ${u===(cur.every_unit||'يوم')?'selected':''}>${u}</option>`).join('')}</select><span>ابتداءً من</span><input type="datetime-local" name="from" value="${esc((cur.start_at||nextNine()).slice(0,16))}"></div>
      <div class="brm-row" data-for="قبل الموعد"><span>قبل موعد المهمة بـ</span><select name="before">${BEFORE.map(([m,l])=>`<option value="${m}" ${m===(+cur.before_minutes||60)?'selected':''}>${l}</option>`).join('')}</select></div>
      <div class="brm-say"></div></div>`;
    const sync=()=>{const on=box.querySelector('.brm-seg .on').dataset.t;box.querySelectorAll('[data-for]').forEach(x=>x.hidden=x.dataset.for!==on);say(box)};
    box.querySelectorAll('.brm-seg button').forEach(b=>b.onclick=()=>{box.querySelectorAll('.brm-seg button').forEach(x=>{x.classList.toggle('on',x===b);x.setAttribute('aria-checked',x===b)});sync()});
    box.querySelectorAll('input,select').forEach(i=>i.addEventListener('input',()=>say(box)));
    sync();
  }
  function fromTask(t){return {type:t.reminder_active?t.reminder_type:'بدون',start_at:t.reminder_start_at,every_n:t.reminder_every_n,every_unit:t.reminder_every_unit,before_minutes:t.reminder_before_minutes}}
  function edit(task,onSaved){
    css();const d=document.createElement('dialog');d.className='brm-dlg';
    d.innerHTML=`<form method="dialog" class="brm-dlg-in"><h3>🔔 تذكير المهمة</h3><div class="brm-t">${esc(task.title||'')}</div><div class="brm-box"></div>
      <div class="brm-act"><button type="button" class="brm-save">حفظ التذكير</button><button type="button" class="btn-light brm-cancel">إلغاء</button></div></form>`;
    document.body.appendChild(d);const box=d.querySelector('.brm-box');box.dataset.due=task.due_at||'';render(box,fromTask(task));
    d.querySelector('.brm-cancel').onclick=()=>d.close();d.addEventListener('close',()=>d.remove());
    d.querySelector('.brm-save').onclick=async()=>{const b=d.querySelector('.brm-save');b.disabled=true;
      try{await bimRequest('/api/tasks/'+task.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({reminder:read(box)})});
        (window.bimToast||alert)('تم حفظ التذكير');d.close();onSaved&&onSaved()}
      catch(e){(window.bimToast||alert)(e.message,'err');b.disabled=false}};
    d.showModal();
  }
  window.bimReminder={render,value:read,edit,fromTask,every};
})();
