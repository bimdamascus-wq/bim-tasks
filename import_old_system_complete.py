from __future__ import annotations
import sys, re, json, shutil, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from openpyxl import load_workbook

ROOT = Path(r"C:\BIM_Tasks_Server")
sys.path.insert(0, str(ROOT))
from app.db import connect, init_db
from app.seed import seed
from app.security import hash_password

XLSX = Path(sys.argv[1])
REPORT = Path(sys.argv[2])

def norm(s):
    s = str(s or "").strip().lower()
    for a,b in [("أ","ا"),("إ","ا"),("آ","ا"),("ى","ي"),("ة","ه")]:
        s = s.replace(a,b)
    return re.sub(r"\s+"," ",s)

def yes(v):
    return norm(v) in ("نعم","yes","true","1","فعال","نشط")

def dt_iso(v):
    if v in (None,""):
        return None
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds")
    if isinstance(v,(int,float)):
        try:
            return (datetime(1899,12,30)+timedelta(days=float(v))).isoformat(timespec="seconds")
        except Exception:
            pass
    s = str(v).strip().replace("٫",".")
    for fmt in ("%d/%m/%Y %H:%M:%S","%d/%m/%Y %H:%M","%d/%m/%Y","%Y-%m-%d %H:%M:%S","%Y-%m-%dT%H:%M:%S","%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s,fmt).isoformat(timespec="seconds")
        except Exception:
            pass
    return s or None

def sval(v):
    if v is None:
        return None
    if isinstance(v,float) and v.is_integer():
        return str(int(v))
    return str(v).strip()

def ws_rows(wb,name):
    if name not in wb.sheetnames:
        return [],{}
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [],{}
    best_i,best = 0,[]
    for i,row in enumerate(rows[:5]):
        vals = [str(x).strip() if x is not None else "" for x in row]
        if sum(bool(x) for x in vals) > sum(bool(x) for x in best):
            best_i,best = i,vals
    return rows[best_i+1:], {norm(x):j for j,x in enumerate(best) if x}

def get(row,h,*keys):
    for k in keys:
        nk=norm(k)
        if nk in h and h[nk] < len(row):
            return row[h[nk]]
    for hk,j in h.items():
        if any(norm(k) in hk for k in keys) and j < len(row):
            return row[j]
    return None

def map_role(v):
    n=norm(v)
    if "مدير" in n or n=="admin":
        return "admin"
    if "مشرف" in n or n=="supervisor":
        return "supervisor"
    if "مشاه" in n or "عرض" in n or n=="viewer":
        return "viewer"
    return "employee"

def map_priority(v):
    n=norm(v)
    if n in ("عاجل","عاجله","عاجلة"):
        return "عاجلة"
    if n in ("عالي","عاليه","عالية","مرتفع","مرتفعة"):
        return "عالية"
    if n in ("منخفض","منخفضه","منخفضة"):
        return "منخفضة"
    return "عادي"

def map_status(v):
    n=norm(v)
    if n in ("تم","منجز","منجزه","منجزة","مكتمل","مكتمله"):
        return "تم"
    if n in ("ملغي","ملغاه","ملغاة","الغاء","إلغاء"):
        return "ملغاة"
    if n in ("بدات","بدأت","قيد التنفيذ","قيدتنفيذ"):
        return "قيد التنفيذ"
    if n in ("بانتظار","انتظار","موقوف","ايقاف مؤقت","إيقاف مؤقت"):
        return "بانتظار"
    return "جديدة"

def map_reminder_type(v):
    n=norm(v)
    if n=="بدون":
        return "بدون"
    if "ساعتين" in n:
        return "كل ساعتين"
    if "ساعه" in n or "ساعة" in n:
        return "كل ساعة"
    if "اسبوع" in n:
        return "أسبوعياً"
    if "شهر" in n:
        return "شهرياً"
    if "تاريخ" in n:
        return "تاريخ محدد"
    if "مره" in n or "مرة" in n:
        return "مرة واحدة"
    return "بدون"

wb = load_workbook(XLSX, data_only=True, read_only=False)
seed()
init_db()
c = connect()
stats = {k:0 for k in ["employees_new","employees_updated","tasks","recurring","participants","subtasks","reminders","templates","views","users","audit","notifications","settings"]}
notes=[]
temp_users=[]

backup_dir = ROOT/"backups"
backup_dir.mkdir(exist_ok=True)
db = ROOT/"data"/"bim_tasks.db"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = backup_dir/f"before_old_system_import_{stamp}.db"
if db.exists():
    shutil.copy2(db, backup_path)

try:
    c.execute("BEGIN IMMEDIATE")

    rows,h = ws_rows(wb,"الموظفون")
    emp_ids={}
    for row in rows:
        name=sval(get(row,h,"اسم الموظف","الموظف","الاسم"))
        if not name:
            continue
        dep=sval(get(row,h,"القسم"))
        wa=sval(get(row,h,"رقم WhatsApp","واتساب","whatsapp"))
        tg=sval(get(row,h,"Telegram Chat ID","chat id"))
        tge=1 if yes(get(row,h,"Telegram")) else 0
        wae=1 if yes(get(row,h,"WhatsApp")) else 0
        old=c.execute("SELECT id FROM employees WHERE lower(name)=lower(?)",(name,)).fetchone()
        if old:
            eid=old["id"]
            c.execute("UPDATE employees SET department=?,whatsapp=?,telegram_chat_id=?,telegram_enabled=?,whatsapp_enabled=?,is_active=1,updated_at=CURRENT_TIMESTAMP WHERE id=?",(dep,wa,tg,tge,wae,eid))
            stats["employees_updated"]+=1
        else:
            c.execute("INSERT INTO employees(name,department,whatsapp,telegram_chat_id,telegram_enabled,whatsapp_enabled,is_active) VALUES(?,?,?,?,?,?,1)",(name,dep,wa,tg,tge,wae))
            eid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            stats["employees_new"]+=1
        emp_ids[norm(name)]=eid

    meta_by_row={}
    meta_by_code={}
    rows,h=ws_rows(wb,"BIM_حالة_المهام")
    for row in rows:
        rr=get(row,h,"رقم الصف","row")
        code=sval(get(row,h,"Task ID","task id"))
        try:
            rr=int(float(rr))
        except Exception:
            rr=None
        md={
            "code":code,
            "telegram_status":sval(get(row,h,"Telegram الحالة")),
            "telegram_last":dt_iso(get(row,h,"Telegram آخر إرسال")),
            "telegram_error":sval(get(row,h,"Telegram الخطأ")),
            "completed_at":dt_iso(get(row,h,"تمت في")),
            "closing_note":sval(get(row,h,"الملاحظة الختامية")),
            "updated_at":dt_iso(get(row,h,"آخر تحديث"))
        }
        if rr:
            meta_by_row[rr]=md
        if code:
            meta_by_code[code]=md

    ws=wb["المهام"]
    header_row=2
    headers={norm(ws.cell(header_row,col).value):col for col in range(1,ws.max_column+1) if ws.cell(header_row,col).value}
    def cell(r,*keys):
        for k in keys:
            nk=norm(k)
            if nk in headers:
                return ws.cell(r,headers[nk]).value
        return None

    task_by_old_code={}
    task_by_row={}
    for r in range(header_row+1,ws.max_row+1):
        title=sval(cell(r,"المهمة","اسم المهمة"))
        if not title:
            continue
        en=sval(cell(r,"اسم الموظف","الموظف"))
        eid=emp_ids.get(norm(en))
        if en and not eid:
            notes.append(f'الصف {r}: الموظف "{en}" غير موجود في الموظفين، فتم ترك المهمة بدون موظف.')
        md=meta_by_row.get(r,{})
        code=md.get("code") or f"BIM-T-{r:05d}"
        existing=c.execute("SELECT id FROM tasks WHERE task_code=?",(code,)).fetchone()
        if existing:
            tid=existing["id"]
            task_by_old_code[code]=tid
            task_by_row[r]=tid
            continue
        created=dt_iso(cell(r,"التاريخ")) or datetime.now().isoformat(timespec="seconds")
        status=map_status(cell(r,"الحالة"))
        completed=md.get("completed_at")
        notes_text=sval(cell(r,"ملاحظات"))
        if md.get("closing_note"):
            notes_text=(notes_text+"\n" if notes_text else "")+"[ملاحظة ختامية قديمة] "+md["closing_note"]
        sql=("INSERT INTO tasks(task_code,employee_id,title,notes,priority,status,due_at,source,completed_at,created_at,updated_at) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?)")
        c.execute(sql,(code,eid,title,notes_text,map_priority(cell(r,"الأولوية")),status,dt_iso(cell(r,"الموعد")),"legacy_google_sheet",completed,created,md.get("updated_at") or created))
        tid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
        task_by_old_code[code]=tid
        task_by_row[r]=tid
        stats["tasks"]+=1
        if md.get("telegram_status"):
            c.execute("INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at) VALUES(?,?,?,?,?,?,?)",
                      (tid,eid,"telegram","legacy_state",md["telegram_status"],md.get("telegram_error"),md.get("telegram_last")))
            stats["notifications"]+=1

    rows,h=ws_rows(wb,"BIM_تذكيرات_المهام")
    for row in rows:
        code=sval(get(row,h,"Task ID"))
        rr=get(row,h,"رقم الصف")
        try:
            rr=int(float(rr))
        except Exception:
            rr=None
        tid=task_by_old_code.get(code) or task_by_row.get(rr)
        if not tid:
            continue
        typ=map_reminder_type(get(row,h,"النوع"))
        active=1 if yes(get(row,h,"مفعّل")) and typ!="بدون" else 0
        wday=get(row,h,"يوم الأسبوع")
        mday=get(row,h,"يوم الشهر")
        try:
            wday=int(float(wday)) if wday not in (None,"") else None
        except Exception:
            wday=None
        try:
            mday=int(float(mday)) if mday not in (None,"") else None
        except Exception:
            mday=None
        sql=("INSERT INTO task_reminders(task_id,type,start_at,specific_at,weekly_day,weekly_time,monthly_day,monthly_time,last_sent_at,is_active,updated_at) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
             "ON CONFLICT(task_id) DO UPDATE SET type=excluded.type,start_at=excluded.start_at,specific_at=excluded.specific_at,"
             "weekly_day=excluded.weekly_day,weekly_time=excluded.weekly_time,monthly_day=excluded.monthly_day,monthly_time=excluded.monthly_time,"
             "last_sent_at=excluded.last_sent_at,is_active=excluded.is_active,updated_at=excluded.updated_at")
        c.execute(sql,(tid,typ,dt_iso(get(row,h,"بدء/مرة واحدة")),dt_iso(get(row,h,"تاريخ محدد")),wday,sval(get(row,h,"وقت الأسبوع")),mday,sval(get(row,h,"وقت الشهر")),dt_iso(get(row,h,"آخر إرسال")),active,dt_iso(get(row,h,"آخر تحديث")) or datetime.now().isoformat()))
        stats["reminders"]+=1
    for tid in task_by_row.values():
        c.execute("INSERT OR IGNORE INTO task_reminders(task_id,type,is_active) VALUES(?,'بدون',0)",(tid,))

    rows,h=ws_rows(wb,"BIM_مشاركو_المهام")
    for row in rows:
        code=sval(get(row,h,"Task ID"))
        rr=get(row,h,"رقم الصف")
        try:
            rr=int(float(rr))
        except Exception:
            rr=None
        tid=task_by_old_code.get(code) or task_by_row.get(rr)
        name=sval(get(row,h,"الموظف"))
        eid=emp_ids.get(norm(name))
        if not tid or not eid:
            continue
        c.execute("INSERT OR REPLACE INTO task_participants(task_id,employee_id,status,started_at,completed_at) VALUES(?,?,?,?,?)",
                  (tid,eid,map_status(get(row,h,"الحالة")),dt_iso(get(row,h,"بدأت في")),dt_iso(get(row,h,"تمت في"))))
        c.execute("UPDATE tasks SET is_group=1 WHERE id=?",(tid,))
        stats["participants"]+=1

    rows,h=ws_rows(wb,"المهام الفرعية")
    for row in rows:
        code=sval(get(row,h,"Task ID"))
        rr=get(row,h,"رقم الصف")
        try:
            rr=int(float(rr))
        except Exception:
            rr=None
        tid=task_by_old_code.get(code) or task_by_row.get(rr)
        title=sval(get(row,h,"النص"))
        if not tid or not title:
            continue
        if c.execute("SELECT 1 FROM task_subtasks WHERE task_id=? AND title=?",(tid,title)).fetchone():
            continue
        done=1 if yes(get(row,h,"منفذة")) else 0
        c.execute("INSERT INTO task_subtasks(task_id,title,is_done,created_at,completed_at) VALUES(?,?,?,?,?)",
                  (tid,title,done,dt_iso(get(row,h,"تاريخ الإضافة")) or datetime.now().isoformat(),dt_iso(get(row,h,"تاريخ التنفيذ"))))
        stats["subtasks"]+=1

    rows,h=ws_rows(wb,"المهام المتكررة")
    for row in rows:
        title=sval(get(row,h,"المهمة"))
        if not title:
            continue
        name=sval(get(row,h,"الموظف"))
        eid=emp_ids.get(norm(name))
        nt=norm(get(row,h,"التكرار"))
        typ="أسبوعي" if "اسبوع" in nt else "شهري" if "شهر" in nt else "يومي"
        run=sval(get(row,h,"ساعة التنفيذ","وقت التنفيذ")) or "09:00"
        active=1 if get(row,h,"نشطة") in (None,"") or yes(get(row,h,"نشطة")) else 0
        if c.execute("SELECT 1 FROM recurring_tasks WHERE COALESCE(employee_id,0)=COALESCE(?,0) AND title=? AND recurrence_type=? AND run_time=?",(eid,title,typ,run)).fetchone():
            continue
        c.execute("INSERT INTO recurring_tasks(employee_id,title,priority,recurrence_type,run_time,is_active,last_run_key) VALUES(?,?,?,?,?,?,?)",
                  (eid,title,map_priority(get(row,h,"الأولوية")),typ,run,active,sval(get(row,h,"آخر إنشاء"))))
        stats["recurring"]+=1

    rows,h=ws_rows(wb,"قوالب المهام")
    for row in rows:
        name=sval(get(row,h,"اسم القالب"))
        title=sval(get(row,h,"عنوان المهمة"))
        if not name or not title:
            continue
        if c.execute("SELECT 1 FROM task_templates WHERE name=? AND title=?",(name,title)).fetchone():
            continue
        c.execute("INSERT INTO task_templates(name,title,notes,priority,is_active) VALUES(?,?,?,?,?)",
                  (name,title,sval(get(row,h,"الملاحظات")),map_priority(get(row,h,"الأولوية")),1 if get(row,h,"نشط") in (None,"") or yes(get(row,h,"نشط")) else 0))
        stats["templates"]+=1

    rows,h=ws_rows(wb,"العروض المحفوظة")
    for row in rows:
        name=sval(get(row,h,"اسم العرض"))
        if not name:
            continue
        filters={}
        for k in ["بحث","الموظف","القسم","الحالة","الأولوية","نوع العرض"]:
            val=sval(get(row,h,k))
            if val:
                filters[k]=val
        if c.execute("SELECT 1 FROM saved_views WHERE user_id IS NULL AND name=?",(name,)).fetchone():
            continue
        c.execute("INSERT INTO saved_views(user_id,name,filters_json,created_at) VALUES(NULL,?,?,?)",
                  (name,json.dumps(filters,ensure_ascii=False),dt_iso(get(row,h,"تاريخ الحفظ")) or datetime.now().isoformat()))
        stats["views"]+=1

    TEMP="BIM@2026!"
    rows,h=ws_rows(wb,"BIM_المستخدمون")
    for row in rows:
        username=sval(get(row,h,"Username"))
        if not username:
            continue
        if c.execute("SELECT 1 FROM users WHERE lower(username)=lower(?)",(username,)).fetchone():
            continue
        role_code=map_role(get(row,h,"Role"))
        role=c.execute("SELECT id FROM roles WHERE code=?",(role_code,)).fetchone()
        if not role:
            continue
        empname=sval(get(row,h,"Employee"))
        eid=emp_ids.get(norm(empname))
        active=1 if get(row,h,"Active") in (None,"") or yes(get(row,h,"Active")) else 0
        c.execute("INSERT INTO users(username,full_name,password_hash,role_id,employee_id,is_active,created_at,last_login_at) VALUES(?,?,?,?,?,?,?,?)",
                  (username,sval(get(row,h,"Name")) or username,hash_password(TEMP),role["id"],eid,active,dt_iso(get(row,h,"Created At")) or datetime.now().isoformat(),dt_iso(get(row,h,"Last Login"))))
        stats["users"]+=1
        temp_users.append(username)

    for sheet,time_col,user_col,action_col,task_col,details_col,prefix in [
        ("BIM_سجل_التدقيق","Time","Username","Action","Task ID","Details","legacy_audit_"),
        ("نشاط المهام","الوقت","الموظف","الحدث","Task ID","التفاصيل","legacy_task_"),
        ("BIM_الأخطاء","الوقت","المكان","المكان","Task ID","التفاصيل","legacy_error_")
    ]:
        rows,h=ws_rows(wb,sheet)
        for row in rows:
            action=sval(get(row,h,action_col))
            details=sval(get(row,h,details_col))
            if not action and not details:
                continue
            tm=dt_iso(get(row,h,time_col))
            who=sval(get(row,h,user_col)) or "النظام"
            code=sval(get(row,h,task_col))
            sig=f"{sheet}|{tm}|{who}|{action}|{code}|{details}"
            hsh=hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]
            if c.execute("SELECT 1 FROM audit_logs WHERE reason LIKE ?",(f"legacy:{hsh}%",)).fetchone():
                continue
            c.execute("INSERT INTO audit_logs(username_snapshot,action,entity_type,entity_id,reason,gateway,created_at) VALUES(?,?,?,?,?,?,?)",
                      (who,prefix+(action or "event"),"task" if code else "legacy",code,f"legacy:{hsh} | {details or ''}","lan",tm or datetime.now().isoformat()))
            stats["audit"]+=1

    rows,h=ws_rows(wb,"إشعارات المهام")
    for row in rows:
        text=sval(get(row,h,"النص"))
        code=sval(get(row,h,"Task ID"))
        if not text:
            continue
        rr=get(row,h,"رقم الصف")
        try:
            rr=int(float(rr))
        except Exception:
            rr=None
        tid=task_by_old_code.get(code) or task_by_row.get(rr)
        if not tid:
            continue
        trow=c.execute("SELECT employee_id FROM tasks WHERE id=?",(tid,)).fetchone()
        c.execute("INSERT INTO notifications(task_id,employee_id,channel,kind,status,error,sent_at) VALUES(?,?,?,?,?,?,?)",
                  (tid,trow["employee_id"] if trow else None,"legacy",sval(get(row,h,"النوع")) or "legacy","read" if yes(get(row,h,"مقروءة")) else "unread",text,dt_iso(get(row,h,"الوقت"))))
        stats["notifications"]+=1

    rows,h=ws_rows(wb,"BIM_الإعدادات")
    for row in rows:
        key=sval(get(row,h,"المفتاح"))
        value=sval(get(row,h,"القيمة"))
        if not key:
            continue
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",("legacy."+key,value or ""))
        stats["settings"]+=1

    c.execute("COMMIT")
except Exception:
    c.execute("ROLLBACK")
    c.close()
    raise
c.close()

lines=[
    "BIM Tasks - Old System Import Report",
    "====================================",
    "Date: "+datetime.now().isoformat(timespec="seconds"),
    "Source: "+XLSX.name,
    "",
    "Imported / updated:",
    f"- Employees new: {stats['employees_new']}",
    f"- Employees updated: {stats['employees_updated']}",
    f"- Tasks: {stats['tasks']}",
    f"- Recurring tasks: {stats['recurring']}",
    f"- Group participants: {stats['participants']}",
    f"- Subtasks: {stats['subtasks']}",
    f"- Reminders: {stats['reminders']}",
    f"- Templates: {stats['templates']}",
    f"- Saved views: {stats['views']}",
    f"- Users created: {stats['users']}",
    f"- Audit/activity rows: {stats['audit']}",
    f"- Historical notifications: {stats['notifications']}",
    f"- Legacy settings: {stats['settings']}",
    "",
    "Backup:",
    "- "+str(backup_path),
]
if temp_users:
    lines += ["","Imported users with temporary password BIM@2026!:"] + ["- "+u for u in temp_users]
lines += ["","Unmatched employee notes:"]
lines += notes if notes else ["- None"]
lines += [
    "",
    "Important:",
    "- Existing user passwords were NOT changed.",
    "- Old Google permission-email rows were not converted because the new system uses role-based permissions, not Google sharing permissions."
]
REPORT.write_text("\n".join(lines),encoding="utf-8-sig")
print("\n".join(lines))
