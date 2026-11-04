"""Task reminders: three options (owner 2026-11-04: "few options that cover everything", e.g. every 10 days).

  مرة واحدة    once at start_at
  يتكرر        every every_n [ساعة|يوم|أسبوع|شهر] starting at start_at (covers hourly, daily, every 10 days, weekly,
               monthly). Hour-based repeats only fire inside the employee's working time (no 3 a.m. messages).
  قبل الموعد   before_minutes before the task's due_at (sent once per due time; again if the due time changes)

Every reminder stops when the task is done or cancelled (worker query). The old seven types are converted once by
`migrate()` (the table had a CHECK that only allowed the old names, so it is rebuilt without it). `normalize()` also
accepts the old names, so an old page or client still saves something sensible.
"""
import calendar
from datetime import datetime, timedelta

TYPES = ('بدون', 'مرة واحدة', 'يتكرر', 'قبل الموعد')
UNITS = ('ساعة', 'يوم', 'أسبوع', 'شهر')
BEFORE = (30, 60, 180, 1440, 2880)  # minutes offered in the form; any positive value is accepted
DAY_START, DAY_END = 8, 20          # working time for hour-based repeats when the employee has no shift set
_AR = str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩')
_PLURAL = {'ساعة': 'ساعات', 'يوم': 'أيام', 'أسبوع': 'أسابيع', 'شهر': 'أشهر'}
_EVERY1 = {'ساعة': 'كل ساعة', 'يوم': 'كل يوم', 'أسبوع': 'كل أسبوع', 'شهر': 'كل شهر'}
_EVERY2 = {'ساعة': 'كل ساعتين', 'يوم': 'كل يومين', 'أسبوع': 'كل أسبوعين', 'شهر': 'كل شهرين'}


def _parse(v):
    if not v: return None
    try: return datetime.fromisoformat(str(v).replace('Z', '').replace(' ', 'T')[:19])
    except ValueError: return None


def _int(v, default=None):
    try: return int(v)
    except (TypeError, ValueError): return default


def every_text(n, unit):
    n = max(1, _int(n, 1)); unit = unit if unit in UNITS else 'يوم'
    if n == 1: return _EVERY1[unit]
    if n == 2: return _EVERY2[unit]
    word = _PLURAL[unit] if 3 <= n <= 10 else unit
    return f"كل {str(n).translate(_AR)} {word}"


def before_text(minutes):
    m = max(1, _int(minutes, 60))
    if m % 1440 == 0:
        d = m // 1440
        return 'قبل الموعد بيوم' if d == 1 else 'قبل الموعد بيومين' if d == 2 else f"قبل الموعد بـ {str(d).translate(_AR)} أيام"
    if m % 60 == 0:
        h = m // 60
        return 'قبل الموعد بساعة' if h == 1 else 'قبل الموعد بساعتين' if h == 2 else f"قبل الموعد بـ {str(h).translate(_AR)} ساعات"
    return f"قبل الموعد بـ {str(m).translate(_AR)} دقيقة"


def label(r):
    """Short Arabic description of a reminder row/dict (for tables and the task page)."""
    if not r or not r.get('type') or r.get('type') == 'بدون' or not r.get('is_active', 1): return 'بدون'
    t = r['type']
    if t == 'مرة واحدة':
        from .main import ar_when
        return 'مرة واحدة' + (f" · {ar_when(r.get('start_at'))}" if r.get('start_at') else '')
    if t == 'يتكرر':
        return every_text(r.get('every_n'), r.get('every_unit'))
    if t == 'قبل الموعد':
        return before_text(r.get('before_minutes'))
    return t


def normalize(rem):
    """Form/API dict -> columns to store. Accepts the old type names too. Raises ValueError with an Arabic message."""
    rem = dict(rem or {}); t = rem.get('type') or 'بدون'
    old = {'كل ساعة': (1, 'ساعة'), 'كل ساعتين': (2, 'ساعة')}
    if t in old:
        rem.update(type='يتكرر', every_n=old[t][0], every_unit=old[t][1])
    elif t == 'تاريخ محدد':
        rem.update(type='مرة واحدة', start_at=rem.get('specific_at') or rem.get('start_at'))
    elif t in ('أسبوعياً', 'شهرياً'):
        unit = 'أسبوع' if t == 'أسبوعياً' else 'شهر'
        rem.update(type='يتكرر', every_n=1, every_unit=unit, start_at=rem.get('start_at') or _legacy_start(rem, unit, datetime.now()))
    t = rem.get('type') or 'بدون'
    if t not in TYPES: raise ValueError('نوع التذكير غير معروف')
    out = {'type': t, 'start_at': None, 'every_n': None, 'every_unit': None, 'before_minutes': None, 'is_active': 0 if t == 'بدون' else 1}
    if t == 'مرة واحدة':
        if not _parse(rem.get('start_at')): raise ValueError('اختر يوم وساعة التذكير')
        out['start_at'] = str(rem['start_at'])[:16]
    elif t == 'يتكرر':
        n = _int(rem.get('every_n'))
        if not n or n < 1 or n > 365: raise ValueError('اكتب كل كم (رقم من ١ إلى ٣٦٥)')
        if rem.get('every_unit') not in UNITS: raise ValueError('اختر: ساعة أو يوم أو أسبوع أو شهر')
        start = _parse(rem.get('start_at')) or datetime.now().replace(second=0, microsecond=0)
        out.update(every_n=n, every_unit=rem['every_unit'], start_at=start.strftime('%Y-%m-%dT%H:%M'))
    elif t == 'قبل الموعد':
        m = _int(rem.get('before_minutes'))
        if not m or m < 1 or m > 43200: raise ValueError('اختر قبل الموعد بقديش')
        out['before_minutes'] = m
    return out


def _add_months(d, k):
    y, mo = divmod(d.month - 1 + k, 12); y += d.year; mo += 1
    return d.replace(year=y, month=mo, day=min(d.day, calendar.monthrange(y, mo)[1]))


def last_occurrence(start, n, unit, now):
    """Latest planned time <= now of a repeat (start + k*n units), or None before the first one."""
    if not start or now < start: return None
    n = max(1, n or 1)
    if unit == 'شهر':
        k = (now.year - start.year) * 12 + now.month - start.month
        k -= k % n
        while k > 0 and _add_months(start, k) > now: k -= n
        return _add_months(start, max(0, k))
    step = {'ساعة': timedelta(hours=1), 'يوم': timedelta(days=1), 'أسبوع': timedelta(weeks=1)}.get(unit, timedelta(days=1)) * n
    return start + step * int((now - start) / step)


def in_working_time(c, employee_id, now):
    """Hour-based repeats only fire in working time: the employee's shift and work days, else 08:00-20:00."""
    from .workers import _shift_context
    e = c.execute('SELECT * FROM employees WHERE id=?', (employee_id,)).fetchone() if employee_id else None
    ctx = _shift_context(e, now) if e else None
    if ctx: return ctx['in_shift'] and ctx['is_work_day']
    return DAY_START <= now.hour < DAY_END


def due(c, r, task, now=None):
    """Should reminder row `r` of `task` be sent now?"""
    now = now or datetime.now(); last = _parse(r['last_sent_at']); t = r['type']
    if t == 'مرة واحدة':
        at = _parse(r['start_at'])
        return bool(at and now >= at and not last)
    if t == 'يتكرر':
        occ = last_occurrence(_parse(r['start_at']), _int(r['every_n'], 1), r['every_unit'], now)
        if not occ or (last and last >= occ): return False
        if r['every_unit'] == 'ساعة' and not in_working_time(c, task['employee_id'], now): return False
        return True
    if t == 'قبل الموعد':
        d = _parse(task['due_at']); m = _int(r['before_minutes'])
        if not d or not m: return False
        start = d - timedelta(minutes=m)
        return start <= now < d and (not last or last < start)
    return False


def _legacy_start(r, unit, now):
    """Start time for an old weekly/monthly reminder so it keeps its day and time."""
    if unit == 'أسبوع':
        h, mi = (map(int, (r.get('weekly_time') or '09:00').split(':')[:2]))
        target = _int(r.get('weekly_day'), 0)  # 0 = Sunday (old convention)
        d = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        d -= timedelta(days=((now.weekday() + 1) % 7 - target) % 7)
    else:
        h, mi = (map(int, (r.get('monthly_time') or '09:00').split(':')[:2]))
        day = min(max(1, _int(r.get('monthly_day'), 1)), calendar.monthrange(now.year, now.month)[1])
        d = now.replace(day=day, hour=h, minute=mi, second=0, microsecond=0)
    if d > now:  # latest past occurrence, so the cadence continues from there
        d = d - timedelta(weeks=1) if unit == 'أسبوع' else _add_months(d, -1)
    if not r.get('last_sent_at'):  # never sent: the old code waited for the next one, keep it that way
        d = d + timedelta(weeks=1) if unit == 'أسبوع' else _add_months(d, 1)
    return d.strftime('%Y-%m-%dT%H:%M')


def migrate(c):
    """Rebuild task_reminders without the old CHECK, add the new columns, convert old rows. Runs once; safe when two
    processes start together (BEGIN IMMEDIATE + re-check)."""
    sql = (c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='task_reminders'").fetchone() or [''])[0] or ''
    if 'every_unit' in sql: return False
    c.execute('BEGIN IMMEDIATE')
    try:
        sql = (c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='task_reminders'").fetchone() or [''])[0] or ''
        if 'every_unit' in sql:
            c.execute('COMMIT'); return False
        c.execute('''CREATE TABLE task_reminders_new(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
          type TEXT NOT NULL DEFAULT 'بدون',
          start_at TEXT, specific_at TEXT, weekly_day INTEGER, weekly_time TEXT, monthly_day INTEGER, monthly_time TEXT,
          last_sent_at TEXT, is_active INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          every_n INTEGER, every_unit TEXT, before_minutes INTEGER)''')
        # last_sent_at used to be written in UTC (CURRENT_TIMESTAMP); the new code compares local times.
        c.execute('''INSERT INTO task_reminders_new(id,task_id,type,start_at,specific_at,weekly_day,weekly_time,monthly_day,
          monthly_time,last_sent_at,is_active,updated_at)
          SELECT id,task_id,type,start_at,specific_at,weekly_day,weekly_time,monthly_day,monthly_time,
          CASE WHEN last_sent_at IS NULL THEN NULL ELSE datetime(last_sent_at,'localtime') END,is_active,updated_at FROM task_reminders''')
        c.execute('DROP TABLE task_reminders')
        c.execute('ALTER TABLE task_reminders_new RENAME TO task_reminders')
        now = datetime.now()
        for r in [dict(x) for x in c.execute("SELECT * FROM task_reminders WHERE type NOT IN ('بدون','مرة واحدة','يتكرر','قبل الموعد')")]:
            t = r['type']
            if t == 'تاريخ محدد':
                c.execute("UPDATE task_reminders SET type='مرة واحدة',start_at=COALESCE(specific_at,start_at) WHERE id=?", (r['id'],))
            elif t in ('كل ساعة', 'كل ساعتين'):
                start = r['start_at'] or (_parse(r['updated_at']) or now).strftime('%Y-%m-%dT%H:%M')
                c.execute("UPDATE task_reminders SET type='يتكرر',every_n=?,every_unit='ساعة',start_at=? WHERE id=?", (1 if t == 'كل ساعة' else 2, start, r['id']))
            elif t in ('أسبوعياً', 'شهرياً'):
                unit = 'أسبوع' if t == 'أسبوعياً' else 'شهر'
                c.execute("UPDATE task_reminders SET type='يتكرر',every_n=1,every_unit=?,start_at=? WHERE id=?", (unit, _legacy_start(r, unit, now), r['id']))
            else:
                c.execute("UPDATE task_reminders SET type='بدون',is_active=0 WHERE id=?", (r['id'],))
        c.execute('COMMIT')
        return True
    except Exception:
        c.execute('ROLLBACK'); raise
