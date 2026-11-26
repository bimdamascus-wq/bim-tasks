import requests, datetime as dt, sys, os
B='http://localhost:8766'; s=requests.Session(); s.headers['origin']=B
r=s.post(B+'/login',data={'username':'boss','password':'Boss-Strong-Pass-2026'},allow_redirects=False); assert r.status_code==303,r.text[:200]
today=dt.date.today()
def due(days,h=14): return f'{today+dt.timedelta(days=days)}T{h:02d}:00'
T=[('تجهيز عرض سعر لمشروع المزة',4,'عالية',due(0)),('متابعة دفعة المورد الشهرية',5,'عادي',due(1)),('تحديث قائمة الأسعار على الموقع',6,'منخفضة',due(3)),
   ('جرد مستودع الفرع الثاني',4,'عالية',due(-2)),('الرد على شكوى زبون الطلبية 512',5,'عالية',due(-1)),('تصوير المنتجات الجديدة',7,'عادي',due(2)),
   ('إرسال تقرير المبيعات الأسبوعي',2,'عادي',due(0,17)),('ترتيب ملفات العقود',6,'عادي',None),('تجديد اشتراك الإنترنت',3,'منخفضة',due(5)),
   ('تدريب الموظف الجديد على النظام',3,'عادي',due(1))]
ids=[]
for t,e,p,d in T:
    r=s.post(B+'/api/tasks',json={'title':t,'employee_id':e,'priority':p,'due_at':d,'notes':'ملاحظة توضيحية للمهمة.'}); assert r.status_code==200,r.text; ids.append(r.json()['id'])
for i,st in [(0,'بدأت'),(1,'قيد التنفيذ'),(2,'تم'),(5,'بانتظار'),(7,'تم'),(8,'قيد التنفيذ')]:
    r=s.patch(B+f'/api/tasks/{ids[i]}/status',json={'status':st}); print(st, r.status_code, r.text[:80])
s.post(B+f'/api/tasks/{ids[0]}/comments',json={'body':'الزبون طلب تعديل الكمية، عم نشتغل عليها.'})
for t,e,ty,extra in [('تقرير الصندوق اليومي',5,'يومي',{}),('اجتماع الفريق الأسبوعي',3,'أسبوعي',{'week_day':0}),('دفع إيجار المحل',2,'شهري',{'month_day':1})]:
    r=s.post(B+'/api/recurring',json={'title':t,'employee_id':e,'recurrence_type':ty,'run_time':'09:00',**extra}); print('rec',r.status_code,r.text[:80])
