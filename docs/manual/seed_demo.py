import os, sys
os.environ.update({'BIM_ADMIN_USERNAME':'boss','BIM_ADMIN_PASSWORD':'Boss-Strong-Pass-2026','BIM_GATEWAY':'lan','TELEGRAM_BOT_TOKEN':''})
sys.path.insert(0, sys.argv[1])
from app.db import init_db; init_db()
import app.main as m
from fastapi.testclient import TestClient
with TestClient(m.app): pass
c = m.connect()
emps = [('مدير النظام','111'),('رنا الحلبي','222'),('سامر الخطيب','333'),('أحمد العلي','444'),('لين ديب','555'),('محمد سليمان','666'),('هبة النجار','')]
ids={}
for n,ch in emps:
    c.execute('INSERT INTO employees(name,telegram_chat_id,is_active) VALUES(?,?,1)',(n,ch or None)); ids[n]=c.execute('select last_insert_rowid()').fetchone()[0]
role=lambda r:c.execute('SELECT id FROM roles WHERE code=?',(r,)).fetchone()[0]
c.execute("UPDATE users SET employee_id=?, full_name='مدير النظام' WHERE username='boss'",(ids['مدير النظام'],))
for u,r,e in [('rana','deputy','رنا الحلبي'),('samer','supervisor','سامر الخطيب'),('ahmad','employee','أحمد العلي')]:
    c.execute('INSERT INTO users(username,full_name,password_hash,role_id,employee_id) VALUES(?,?,?,?,?)',(u,e,m.hash_password('Team-Pass-2026x'),role(r),ids[e]))
sid=c.execute("select id from users where username='samer'").fetchone()[0]
for e in ('أحمد العلي','لين ديب'): c.execute('INSERT INTO supervisor_team(user_id,employee_id) VALUES(?,?)',(sid,ids[e]))
c.close(); print(ids)
