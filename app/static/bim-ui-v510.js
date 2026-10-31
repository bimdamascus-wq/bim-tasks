(()=>{
  const DBN='bim-tasks-offline-v1',STORE='actions';
  function dbOpen(){return new Promise((res,rej)=>{const r=indexedDB.open(DBN,1);r.onupgradeneeded=()=>{if(!r.result.objectStoreNames.contains(STORE))r.result.createObjectStore(STORE,{keyPath:'id'})};r.onsuccess=()=>res(r.result);r.onerror=()=>rej(r.error)})}
  async function qAll(){const d=await dbOpen();return new Promise((res,rej)=>{const r=d.transaction(STORE,'readonly').objectStore(STORE).getAll();r.onsuccess=()=>res(r.result||[]);r.onerror=()=>rej(r.error)})}
  async function qPut(x){const d=await dbOpen();return new Promise((res,rej)=>{const r=d.transaction(STORE,'readwrite').objectStore(STORE).put(x);r.onsuccess=()=>res();r.onerror=()=>rej(r.error)})}
  async function qDel(id){const d=await dbOpen();return new Promise((res,rej)=>{const r=d.transaction(STORE,'readwrite').objectStore(STORE).delete(id);r.onsuccess=()=>res();r.onerror=()=>rej(r.error)})}
  function uid(){return crypto.randomUUID?crypto.randomUUID():Date.now()+'-'+Math.random().toString(16).slice(2)}

  window.bimRequest=async(url,opt={},cfg={})=>{try{const r=await fetch(url,opt);let x={};try{x=await r.clone().json()}catch{}if(r.status===401){location.href='/login';throw new Error('انتهت الجلسة')}if(!r.ok)throw Object.assign(new Error(x.detail||'تعذر تنفيذ العملية'),{http:true,status:r.status,payload:x});return x}catch(e){if(cfg.queueable&&!e.http){const id=uid();await qPut({id,url,method:opt.method||'POST',headers:opt.headers||{},body:opt.body||null,label:cfg.label||'عملية معلقة',status:'pending',createdAt:new Date().toISOString()});bimToast('تم حفظ العملية محليًا وستُرسل عند عودة السيرفر','warn');window.dispatchEvent(new Event('bim:queue-change'));return{offline_queued:true}}throw e}};
  async function flushQueue(){const actions=await qAll();for(const a of actions){if(a.status==='conflict')continue;try{const r=await fetch(a.url,{method:a.method,headers:a.headers||{},body:a.body});if(r.ok){await qDel(a.id)}else if([400,403,404,409].includes(r.status)){a.status='conflict';await qPut(a)}else break}catch{break}}updateConnection()}


  window.addEventListener('online',()=>{flushQueue().catch(()=>{});});

  window.bimEsc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  window.bimArDigits=v=>String(v??'').replace(/\d/g,d=>'٠١٢٣٤٥٦٧٨٩'[Number(d)]);
  window.bimDate=v=>{if(!v)return 'بدون موعد';let d=new Date(String(v).replace(' ','T'));if(Number.isNaN(d.getTime()))return bimArDigits(v);return new Intl.DateTimeFormat('ar-SY',{weekday:'long',day:'numeric',month:'long',year:'numeric',hour:'numeric',minute:'2-digit',hour12:true}).format(d).replace(/AM|a\.m\./ig,'صباحاً').replace(/PM|p\.m\./ig,'مساءً')};
  window.bimToast=(message,type='ok')=>{let st=document.querySelector('.bim-toast-stack');if(!st){st=document.createElement('div');st.className='bim-toast-stack';st.style.cssText='position:fixed;left:20px;bottom:20px;z-index:15000;display:grid;gap:8px;max-width:min(420px,90vw)';document.body.appendChild(st)}const t=document.createElement('div');t.textContent=message;t.style.cssText='background:'+(type==='err'?'#414b58':type==='warn'?'#414b58':'#087a55')+';color:#fff;border-radius:12px;padding:12px 14px;font-size:12px;font-weight:900;box-shadow:0 14px 34px rgba(0,0,0,.18)';st.appendChild(t);setTimeout(()=>t.remove(),3800)};

  if(location.pathname==='/login'){document.body.classList.add('bim-login-page');return;}

  const path=location.pathname;
  const defs={
    '/company':['إعداد الشركة','الهوية والإعدادات العامة'],
    '/template-library':['قوالب العمل','قوالب المهام والتعليمات المستقلة'],
    '/workload':['عبء العمل','المهام المفتوحة والجهد التقديري'],
    '/handover':['تسليم الأعمال','نقل الأعمال المفتوحة'],
    '/access-control':['الصلاحيات والقنوات','وصول المستخدمين ووسائل العمل'],
    '/':['الرئيسية','متابعة الأعمال والقرارات'],
    '/tasks':['كل المهام','عرض المهام ومتابعتها'],
    '/tasks-table':['المهام كجدول','جدول كامل للبحث والفلترة'],
    '/manage':['إدارة المهام','إدارة المهام والتعديلات'],
    '/employees':['الموظفون','الفريق والأقسام والربط'],
    '/recurring':['المهام المتكررة','القواعد الدورية'],
    '/integrations':['الربط والتكامل','الخدمات والتكاملات'],
    '/telegram-center':['Telegram','البوت وربط الموظفين'],
    '/shift-center':['الشيفتات','الدوام والرسائل'],
    '/telegram-groups':['مجموعات Telegram','المجموعات والتذكيرات'],
    '/telegram-health':['Telegram Health','الصحة والطابور'],
    '/telegram-dashboard':['Telegram Dashboard','إحصائيات Telegram'],
    '/automation-center':['مركز الأتمتة','الأعمال التلقائية'],
    '/operations-reports':['التقارير التشغيلية','الأداء والمتابعة'],
    '/audit':['سجل النشاط','كل الحركات والتعديلات'],
    '/users':['المستخدمون','الحسابات والصلاحيات'],
    '/system-health':['صحة النظام','الخدمات والاتصال'],
    '/archive':['الأرشيف','المهام المؤرشفة'],
    '/sync':['المزامنة','Google Sheet'],
    '/backup':['النسخ الاحتياطي','الحماية والاستعادة']
  };
  const icon={'/':'⌂','/tasks':'◫','/tasks-table':'▤','/manage':'⚙','/employees':'◉','/recurring':'↻','/integrations':'⌁','/telegram-center':'✈','/shift-center':'◷','/telegram-groups':'◎','/automation-center':'✦','/operations-reports':'▧','/audit':'◴','/users':'♙','/system-health':'●','/archive':'□','/sync':'↔','/backup':'⬡','/telegram-health':'◇','/telegram-dashboard':'▥'};
  const groups=[
    ['العمل اليومي',[
      ['/','الرئيسية','dashboard.view'],['/tasks','كل المهام','tasks.view'],['/tasks-table','المهام كجدول','tasks.view'],['/manage','إدارة المهام','tasks.edit']]],
    ['الفريق والتنظيم',[
      ['/record-management','إدارة السجلات والحذف','users.manage'],['/company','إعداد الشركة','users.manage'],['/template-library','قوالب العمل','users.manage'],['/workload','عبء العمل','dashboard.view'],['/handover','تسليم الأعمال','users.manage'],['/access-control','الصلاحيات والقنوات','users.manage'],['/employees','الموظفون','employees.view'],['/recurring','المهام المتكررة','recurring.view'],['/shift-center','الشيفتات','employees.edit']]],
    ['الربط والتشغيل',[
      ['/integrations','الربط والتكامل','dashboard.view'],['/telegram-center','Telegram','employees.edit'],['/telegram-groups','مجموعات Telegram','employees.edit'],['/automation-center','مركز الأتمتة','employees.edit']]],
    ['المراقبة والإدارة',[
      ['/operations-reports','التقارير التشغيلية','tasks.view'],['/audit','سجل النشاط','audit.view'],['/users','المستخدمون','users.manage'],['/system-health','صحة النظام','dashboard.view'],['/archive','الأرشيف','tasks.view'],['/sync','المزامنة','dashboard.view'],['/backup','النسخ الاحتياطي','dashboard.view'],['/page-visibility','الصفحات الظاهرة','users.manage']]]
  ];

  const exact=a=>a==='/'?path==='/':path===a||path.startsWith(a+'/');

  async function init(){
    let me={permissions:[],full_name:'المستخدم',role:''};
    try{
      const r=await fetch('/api/me',{cache:'no-store'});
      if(r.status===401){location.href='/login';return}
      if(r.ok)me=await r.json();
    }catch{}
    const perms=new Set(me.permissions||[]);
    const sidebar=document.createElement('aside');
    sidebar.className='v510-sidebar';
    const navHtml=groups.map(([label,items])=>{
      const hiddenPages=new Set(me.hidden_pages||[]);
      const links=items.filter(x=>perms.has(x[2]) && !hiddenPages.has(x[0]) && (x[0]!=='/' || ['admin','deputy'].includes(me.role_code))).map(x=>`<a href="${x[0]}" class="${exact(x[0])?'active':''}"><span class="v510-nav-ico">${icon[x[0]]||'•'}</span><span>${x[1]}</span></a>`).join('');
      return links?`<div class="v510-nav-group"><div class="v510-nav-label">${label}</div><div class="v510-nav">${links}</div></div>`:'';
    }).join('');
    sidebar.innerHTML=`
      <div class="v510-brand">
        <span class="v510-brand-mark"><img src="${window.companyBrand?.logo?'/company-logo':'/static/bim-logo-trim.png'}" alt="${bimEsc(window.companyBrand?.name||'الشركة')}"></span>
        <span class="v510-brand-copy"><b>${bimEsc(window.companyBrand?.name||'إدارة العمل')}</b><span>إدارة العمل</span></span>
      </div>
      <div class="v510-sidebar-scroll">${navHtml}</div>
      <div class="v510-side-user">
        <div class="v510-user-card">
          <span class="v510-avatar">${bimEsc((me.full_name||me.username||'ب').trim().slice(0,1))}</span>
          <span><b>${bimEsc(me.full_name||me.username||'المستخدم')}</b><span>${bimEsc(me.role||'')}</span></span>
        </div>
        <a class="v510-logout" href="/logout">تسجيل الخروج</a>
      </div>`;
    document.body.prepend(sidebar);
    const scrollArea=sidebar.querySelector('.v510-sidebar-scroll');
    const identity=String(me.id||me.user_id||me.username||me.full_name||'user');
    const storeKey='bim-sidebar-y:'+identity;
    try{
      const old=Number(sessionStorage.getItem(storeKey)||0);
      if(Number.isFinite(old)&&old>=0){
        const restore=()=>{if(scrollArea)scrollArea.scrollTop=old};
        requestAnimationFrame(()=>requestAnimationFrame(restore));
        if(document.fonts&&document.fonts.ready)document.fonts.ready.then(restore).catch(()=>{});
      }
      let pending=false;
      scrollArea?.addEventListener('scroll',()=>{
        if(pending)return;pending=true;
        requestAnimationFrame(()=>{pending=false;sessionStorage.setItem(storeKey,String(scrollArea.scrollTop))});
      },{passive:true});
      sidebar.addEventListener('click',e=>{if(e.target.closest('a'))sessionStorage.setItem(storeKey,String(scrollArea.scrollTop))});
    }catch(_){}

    const info=defs[path]||['BIM Tasks','نظام إدارة المهام'];
    const top=document.createElement('header');
    top.className='v510-topbar';
    top.innerHTML=`
      <div class="v510-page-name"><b>${bimEsc(info[0])}</b><span>${bimEsc(info[1])}</span></div>
      <div class="v510-tools">
        <input id="v510Search" class="v510-search" placeholder="ابحث عن مهمة أو موظف...">
        <span class="v6-today" id="v6Today"></span><span id="v510State" class="v510-state"><i></i> جاري التحقق</span>
      </div>`;
    sidebar.insertAdjacentElement('afterend',top);const td=document.getElementById('v6Today');if(td)td.textContent=new Intl.DateTimeFormat('ar-SY',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).format(new Date());

    const search=document.getElementById('v510Search');
    search.addEventListener('keydown',e=>{if(e.key==='Enter'&&search.value.trim())location.href='/tasks-table?q='+encodeURIComponent(search.value.trim())});

    async function health(){
      const el=document.getElementById('v510State');
      try{
        const c=new AbortController();const t=setTimeout(()=>c.abort(),7000);
        let r=await fetch('/api/client-status',{cache:'no-store',signal:c.signal});if(r.status===403)r=await fetch('/api/me',{cache:'no-store',signal:c.signal});clearTimeout(t);
        if(r.ok){el.className='v510-state online';el.innerHTML='<i></i> متصل بالسيرفر';return}
      }catch{}
      el.className='v510-state offline';el.innerHTML='<i></i> غير متصل';
    }
    health();setInterval(health,12000);
  }
  document.addEventListener('DOMContentLoaded',init);
})();