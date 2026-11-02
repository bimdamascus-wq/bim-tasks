const {chromium}=require('playwright');
const pages=['/','/tasks','/tasks-table','/manage','/tasks?view=board','/employee/3','/my-work','/my-team','/archive','/recurring','/employees','/users','/access-control','/job-profiles','/workload','/handover','/shift-center','/team-performance','/operations-reports','/activity-log','/audit','/record-management','/template-library','/automation-center','/notification-center','/telegram-center','/telegram-groups','/telegram-health','/telegram-dashboard','/iphone-setup','/integrations','/security','/permissions','/company','/page-visibility','/system-health','/sync','/backup','/account','/task/1'];
async function collect(port,user,pw){const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:900}});const errs=[];p.on('pageerror',e=>errs.push(e.message));
 await p.goto(`http://127.0.0.1:${port}/login`);await p.fill('input[name=username]',user);await p.fill('input[name=password]',pw);await Promise.all([p.waitForNavigation(),p.click('form button')]);
 const out={nav:null,pages:{},errs};
 for(const pg of pages){const r=await p.goto(`http://127.0.0.1:${port}${pg}`);await p.waitForTimeout(700);
  const d=await p.evaluate(()=>{const shell='.v510-sidebar,.v510-topbar,.ds-side,.ds-top,body>nav.toolbar,#activityNav,.app>nav.side';
   const inShell=e=>!!e.closest(shell);
   const vis=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
   const q=s=>[...document.querySelectorAll(s)].filter(e=>!inShell(e));
   const txt=e=>(e.getAttribute('aria-label')||e.textContent||e.value||'').replace(/\s+/g,' ').trim().slice(0,40);
   const navs=[...document.querySelectorAll(shell)].flatMap(n=>[...n.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))).filter(h=>h&&h!=='#');
   return {status:document.title,buttons:q('button').map(txt).sort(),fields:q('input,select,textarea').map(e=>e.name||e.id||e.type).sort(),links:q('a[href]').map(a=>a.getAttribute('href')).sort(),ids:q('[id]').map(e=>e.id).sort(),hiddenBtns:q('button').filter(e=>!vis(e)).length,nav:[...new Set(navs)].sort()}});
  d.code=r.status();out.pages[pg]=d;}
 await b.close();return out}
(async()=>{const [,,user,pw]=process.argv;const a=await collect(8765,user,pw),b=await collect(8766,user,pw);
 const navA=new Set(Object.values(a.pages).flatMap(x=>x.nav)),navB=new Set(Object.values(b.pages).flatMap(x=>x.nav));
 console.log('NAV missing in new:',[...navA].filter(x=>!navB.has(x)),' new only:',[...navB].filter(x=>!navA.has(x)));
 const diff=(x,y)=>{const m={};for(const v of x)m[v]=(m[v]||0)+1;for(const v of y)m[v]=(m[v]||0)-1;return Object.entries(m).filter(([k,v])=>v>0).map(([k,v])=>k+(v>1?'×'+v:''))};
 for(const pg of Object.keys(a.pages)){const A=a.pages[pg],B=b.pages[pg];const r=[];
  if(A.code!==B.code)r.push(`code ${A.code}->${B.code}`);
  for(const k of ['buttons','fields','links','ids']){const m=diff(A[k],B[k]);if(m.length)r.push(k+' missing: '+m.join(' | '))}
  if(r.length)console.log(pg,'\n   '+r.join('\n   '))}
 console.log('errors old',a.errs.length,'new',b.errs,);})();
