const {chromium}=require('playwright');const fs=require('fs');
const [,,out,user,pw,w,h,tag,csslist,...pages]=process.argv;const style=csslist.split(',').filter(Boolean).map(f=>fs.readFileSync(f,'utf8')).join('\n');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:+w,height:+h}});
await p.goto('http://127.0.0.1:8766/login');await p.fill('input[name=username]',user);await p.fill('input[name=password]',pw);
await Promise.all([p.waitForNavigation(),p.click('form button')]);
for(const pg of pages){await p.goto('http://127.0.0.1:8766'+pg);if(style)await p.addStyleTag({content:style});await p.waitForTimeout(1200);
 await p.screenshot({path:out+'/'+tag+pg.replace(/\W+/g,'_')+'.png'});}
await b.close()})();
