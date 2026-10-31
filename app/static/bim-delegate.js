/* BIM Tasks V6.2 — task origin badge + distribute a task to staff by Telegram or WhatsApp */
(()=>{
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const css=document.createElement('style');
  css.textContent=`.bim-origin{display:inline-flex;align-items:center;border-radius:999px;padding:2px 8px;font-size:11px;font-weight:900;margin-inline-end:6px;white-space:nowrap;vertical-align:middle}
.bim-origin.manager{background:#fff0f1;color:#c8102e;border:1px solid #f4c8ca}.bim-origin.self{background:#edf4ff;color:#235fbf;border:1px solid #cfe0fb}
.bim-origin.assigned{background:#f2f4f7;color:#475467;border:1px solid #e1e5ea}.bim-origin.system{background:#f6f6f6;color:#7a8594;border:1px solid #ececec}
.bim-deleg-note{font-size:12px;color:#667085;margin-top:4px}
.bim-deleg-modal{display:none;position:fixed;inset:0;background:rgba(15,23,42,.35);z-index:5000;align-items:center;justify-content:center;padding:16px}
.bim-deleg-modal.show{display:flex}.bim-deleg-card{background:#fff;border-radius:16px;width:min(520px,100%);max-height:92vh;overflow:auto;padding:16px;box-shadow:0 24px 80px rgba(15,23,42,.25)}
.bim-deleg-card h2{margin:0 0 10px;font-size:18px}.bim-deleg-card label{display:block;font-size:12px;font-weight:900;color:#475467;margin:10px 0 4px}
.bim-deleg-card select,.bim-deleg-card textarea{width:100%;box-sizing:border-box;padding:9px;border:1px solid #d8dde5;border-radius:10px;font:inherit}
.bim-deleg-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}.bim-deleg-actions button{min-height:46px;font-weight:900;border-radius:12px;border:0;color:#fff;cursor:pointer}
html body .bim-deleg-modal .bim-deleg-card .bim-deleg-actions button.tg,html body .bim-deleg-modal .bim-deleg-card .bim-deleg-actions button.tg:hover{background:#229ed9!important;color:#fff!important;border:0!important}html body .bim-deleg-modal .bim-deleg-card .bim-deleg-actions button.wa,html body .bim-deleg-modal .bim-deleg-card .bim-deleg-actions button.wa:hover{background:#1fae54!important;color:#fff!important;border:0!important}.bim-deleg-actions button:disabled{opacity:.45;cursor:not-allowed}
.bim-deleg-close{float:left;background:transparent;border:0;font-size:22px;cursor:pointer;color:#667085}
.bim-deleg-preview{white-space:pre-wrap;background:#f7f8fa;border:1px solid #e5e9ef;border-radius:10px;padding:9px;font-size:12px;margin-top:10px}`;
  document.head.appendChild(css);

  window.bimOriginBadge=t=>t&&t.origin?`<span class="bim-origin ${esc(t.origin)}">${esc(t.origin_label)}</span>`:'';
  window.bimDelegationText=t=>{const d=t&&t.last_delegation;if(!d)return'';return `↪ وُزّعت إلى ${esc(d.employee_name)} عبر ${d.channel==='whatsapp'?'واتساب':'Telegram'}`};

  let modal=null,current=null,opts=null;
  function build(){
    modal=document.createElement('div');modal.className='bim-deleg-modal';modal.dir='rtl';
    modal.innerHTML=`<div class="bim-deleg-card"><button class="bim-deleg-close" type="button" aria-label="إغلاق">×</button><h2>توزيع المهمة على موظف</h2>
      <div id="bimDelegTitle" style="font-weight:800"></div>
      <label for="bimDelegEmp">الموظف</label><select id="bimDelegEmp"></select>
      <label for="bimDelegNote">ملاحظة للموظف (اختياري)</label><textarea id="bimDelegNote" rows="2" placeholder="مثال: بدي ياها قبل الساعة 2"></textarea>
      <div class="bim-deleg-preview" id="bimDelegPreview"></div>
      <div class="bim-deleg-actions"><button type="button" class="tg" id="bimDelegTg">✈️ إرسال Telegram</button><button type="button" class="wa" id="bimDelegWa">💬 إرسال واتساب</button></div>
      <div class="bim-deleg-note" id="bimDelegHint"></div></div>`;
    document.body.appendChild(modal);
    const close=()=>modal.classList.remove('show');
    modal.querySelector('.bim-deleg-close').onclick=close;modal.addEventListener('click',e=>{if(e.target===modal)close()});
    const emp=modal.querySelector('#bimDelegEmp'),note=modal.querySelector('#bimDelegNote');
    emp.onchange=refresh;note.oninput=refresh;
    modal.querySelector('#bimDelegTg').onclick=()=>send('telegram');
    modal.querySelector('#bimDelegWa').onclick=()=>send('whatsapp');
  }
  function selected(){const id=+modal.querySelector('#bimDelegEmp').value;return (opts&&opts.employees||[]).find(e=>e.id===id)}
  function refresh(){
    const e=selected(),n=modal.querySelector('#bimDelegNote').value.trim();
    modal.querySelector('#bimDelegTg').disabled=!e||!e.telegram;
    modal.querySelector('#bimDelegWa').disabled=!e;
    modal.querySelector('#bimDelegHint').textContent=!e?'اختر الموظف أولاً':(!e.telegram?'هذا الموظف غير مربوط على Telegram. ':'')+(e.whatsapp?'واتساب يفتح محادثة الموظف مباشرة.':'لا يوجد رقم واتساب لهذا الموظف؛ واتساب سيفتح لتختار المحادثة بنفسك.');
    modal.querySelector('#bimDelegPreview').textContent=(opts?opts.message:'')+(n?'\nملاحظة: '+n:'');
  }
  async function send(channel){
    const e=selected();if(!e)return;
    const body=JSON.stringify({employee_id:e.id,channel,note:modal.querySelector('#bimDelegNote').value.trim()});
    try{
      const r=await fetch('/api/tasks/'+current+'/delegate',{method:'POST',headers:{'Content-Type':'application/json'},body});
      const x=await r.json().catch(()=>({}));
      if(!r.ok)throw new Error(x.detail||'تعذر توزيع المهمة');
      modal.classList.remove('show');
      if(channel==='whatsapp'&&x.url){location.href=x.url;return}
      (window.bimToast||alert)('تم إرسال المهمة إلى '+x.employee+' على Telegram');
      document.dispatchEvent(new CustomEvent('bim:delegated',{detail:{task:current}}));
    }catch(err){(window.bimToast?bimToast(err.message,'err'):alert(err.message))}
  }
  window.bimDelegate=async tid=>{
    if(!modal)build();current=tid;
    try{
      const r=await fetch('/api/tasks/'+tid+'/delegate-options');const x=await r.json();
      if(!r.ok)throw new Error(x.detail||'تعذر تحميل الموظفين');opts=x;
    }catch(err){return window.bimToast?bimToast(err.message,'err'):alert(err.message)}
    modal.querySelector('#bimDelegTitle').textContent=(opts.message.split('\n')[0]||'');
    modal.querySelector('#bimDelegEmp').innerHTML='<option value="">— اختر —</option>'+opts.employees.map(e=>`<option value="${e.id}">${esc(e.name)}${e.department?' · '+esc(e.department):''}${e.telegram?' ✈️':''}${e.whatsapp?' 💬':''}</option>`).join('');
    modal.querySelector('#bimDelegNote').value='';refresh();modal.classList.add('show');
  };
})();
