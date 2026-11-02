const {chromium}=require('playwright');const fs=require('fs');
const css=process.argv[2]?fs.readFileSync(process.argv[2],'utf8'):'';
const pages=['/','/tasks','/tasks-table','/manage','/my-team','/archive','/recurring','/employees','/users','/access-control','/job-profiles','/workload','/handover','/shift-center','/team-performance','/operations-reports','/activity-log','/audit','/record-management','/template-library','/automation-center','/notification-center','/telegram-center','/telegram-groups','/telegram-health','/telegram-dashboard','/iphone-setup','/integrations','/security','/permissions','/company','/page-visibility','/system-health','/sync','/backup','/account','/task/1'];
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:900}});
await p.goto('http://127.0.0.1:8766/login');await p.fill('input[name=username]','boss');await p.fill('input[name=password]','Boss-Strong-Pass-2026');
await Promise.all([p.waitForNavigation(),p.click('form button')]);
for(const pg of pages){await p.goto('http://127.0.0.1:8766'+pg);if(css)await p.addStyleTag({content:css});await p.waitForTimeout(600);
 // open first modal / details to test them too
 await p.evaluate(()=>{document.querySelectorAll('details').forEach(d=>d.open=true);const m=document.querySelector('.modal-backdrop');if(m)m.classList.add('show')});
 const bad=await p.evaluate(()=>{const parse=c=>{const m=c.match(/[\d.]+/g);if(!m)return [0,0,0,0];const v=m.map(Number);if(c.startsWith('color(srgb')){v[0]*=255;v[1]*=255;v[2]*=255}return v};
  const lum=([r,g,b])=>{const f=v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};return .2126*f(r)+.7152*f(g)+.0722*f(b)};
  const bgOf=e=>{let col=[11,18,32];const stack=[];for(let x=e;x;x=x.parentElement){const cs=getComputedStyle(x);const c=parse(cs.backgroundColor);const a=c.length>3?c[3]:1;if(cs.backgroundImage&&cs.backgroundImage!=='none'&&!cs.backgroundImage.includes('url'))return null;if(a>0.5){col=c.slice(0,3);break}}return col};
  const out=[];for(const e of document.querySelectorAll('body *')){if(!(e.offsetWidth||e.offsetHeight))continue;if(e.classList.contains('ds-skip'))continue;const own=[...e.childNodes].some(n=>n.nodeType==3&&n.textContent.trim());if(!own&&!['INPUT','SELECT','TEXTAREA','BUTTON'].includes(e.tagName))continue;
   const cs=getComputedStyle(e);if(cs.visibility==='hidden'||parseFloat(cs.opacity)<.5)continue;const fg=parse(cs.color);if(fg.length>3&&fg[3]<.5)continue;
   if(e.closest('.ds-date')&&e.tagName==='INPUT')continue;if(e.type==='checkbox'||e.type==='radio')continue;
   const bg=bgOf(e);if(!bg)continue;const L1=lum(fg.slice(0,3)),L2=lum(bg);const r=(Math.max(L1,L2)+.05)/(Math.min(L1,L2)+.05);
   if(r<3)out.push(r.toFixed(1)+' '+e.tagName.toLowerCase()+'.'+(e.className||'').toString().split(' ')[0]+' "'+(e.textContent||e.value||'').trim().slice(0,25)+'"')}
  return [...new Set(out)].slice(0,8)});
 if(bad.length)console.log(pg,'\n  '+bad.join('\n  '));}
await b.close()})();
