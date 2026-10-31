/* BIM Tasks V8.1: visible-display normalization only, not database/API values. */
(()=>{'use strict';
const DIGITS='٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹';
const western=s=>String(s).replace(/[٠-٩۰-۹]/g,c=>String(DIGITS.indexOf(c)%10));
const months=['كانون الثاني','شباط','آذار','نيسان','أيار','حزيران','تموز','آب','أيلول','تشرين الأول','تشرين الثاني','كانون الأول'];
const days=['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'];
function niceDate(s){
 const x=western(s).trim();let m=x.match(/^(\d{4})[-/](\d\d?)[-/](\d\d?)(?:[T\s]+(\d\d?):(\d\d)(?::\d\d)?(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?)?$/);
 if(!m){const y=x.match(/^(\d\d?)[-/](\d\d?)[-/](\d{4})(?:\s+(\d\d?):(\d\d)(?::\d\d)?)?$/); if(y)m=[null,y[3],y[2],y[1],y[4],y[5]];}
 if(!m)return null;
 const year=+m[1],month=+m[2],day=+m[3];if(year<2000||year>2100||month<1||month>12)return null;
 const dt=new Date(year,month-1,day);if(dt.getFullYear()!==year||dt.getMonth()!==month-1||dt.getDate()!==day)return null;
 let out=days[dt.getDay()]+'، '+day+' '+months[month-1]+' '+year;
 if(m[4]!=null){let h=+m[4],min=+m[5];if(h>23||min>59)return null;const period=h<12?'صباحاً':'مساءً';h=h%12||12;out+='، الساعة '+h+':'+String(min).padStart(2,'0')+' '+period;}
 return out;
}
function visibleText(root){
 const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{acceptNode(n){
   const p=n.parentElement;if(!p||!n.nodeValue.trim())return NodeFilter.FILTER_REJECT;
   if(p.closest('script,style,noscript,textarea,input,select,option,pre,code,[contenteditable],.flatpickr-calendar,.datepicker,[data-no-date-format]'))return NodeFilter.FILTER_REJECT;
   if(!p.closest('table,.stat-card,.kpi,.metric,.dashboard-stat,.v510-task-row,.bim-section,.card,time,.badge'))return NodeFilter.FILTER_REJECT;
   return NodeFilter.FILTER_ACCEPT;
 }});let n,count=0;
 while((n=walker.nextNode()) && count++<7000){const raw=n.nodeValue;
   // Do not change user-entered free text, only isolated date strings or purely numerical indicators.
   const target=raw.trim();const parsed=niceDate(target);
   if(parsed){n.nodeValue=raw.replace(target,parsed);continue;}
   if(/^[\s٠-٩۰-۹\d.,%:+\-]+$/.test(raw)&&/[٠-٩۰-۹]/.test(raw))n.nodeValue=western(raw);
 }
}
function apply(){visibleText(document.body)}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply);else apply();
let queued=false;const obs=new MutationObserver(()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;obs.disconnect();apply();obs.observe(document.body,{childList:true,subtree:true});});});
window.addEventListener('load',()=>obs.observe(document.body,{childList:true,subtree:true}));
})();
