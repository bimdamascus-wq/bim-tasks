const { chromium } = require('playwright');
const B='http://localhost:8766', OUT=process.argv[2];
const sets={
 boss:['Boss-Strong-Pass-2026',['/v2','/v2/tasks','/v2/alerts','/v2/team','/v2/recurring','/v2/reports','/v2/settings',
  '/follow','/','/my-team','/tasks?view=cards','/tasks?view=table','/tasks?view=quick','/tasks?view=board','/tasks?new=1','/task/1','/employee/4',
  '/alerts','/recurring','/archive','/employees','/users','/access-control','/job-profiles','/handover','/shift-center',
  '/team-performance','/operations-reports','/workload','/activity-log','/audit','/record-management','/template-library','/automation-center',
  '/notification-center','/telegram-center','/telegram-groups','/iphone-setup','/integrations','/security','/permissions','/company',
  '/system-health','/sync','/backup','/settings','/account']],
 samer:['Team-Pass-2026x',['/v2','/my-team']],
 ahmad:['Team-Pass-2026x',['/my-work']],
};
(async()=>{
 const b=await chromium.launch();
 { const p=await b.newPage({viewport:{width:1440,height:900}}); await p.goto(B+'/login'); await p.waitForTimeout(800); await p.screenshot({path:OUT+'/login.png'}); await p.close(); }
 for (const [u,[pw,pages]] of Object.entries(sets)){
  const ctx=await b.newContext({viewport:{width:1440,height:900}}); const p=await ctx.newPage();
  await p.goto(B+'/login'); await p.fill('input[name=username]',u); await p.fill('input[name=password]',pw);
  await Promise.all([p.waitForNavigation(),p.click('button[type=submit], form button')]);
  for (const pg of pages){
   try{ const r=await p.goto(B+pg,{waitUntil:'networkidle',timeout:20000}); await p.waitForTimeout(1200);
    const f=u+'_'+(pg.replace(/[\/?=&]+/g,'_').replace(/^_|_$/g,'')||'home')+'.png';
    await p.screenshot({path:OUT+'/'+f}); console.log(r.status(), f, p.url()); }catch(e){console.log('ERR',pg,e.message)}
  }
  await ctx.close();
 }
 await b.close();
})();
