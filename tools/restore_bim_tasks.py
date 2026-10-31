"""Restore BIM Tasks from an encrypted off-site backup (.bimbak).

  python tools/restore_bim_tasks.py                 -> list backups in the configured folder and choose
  python tools/restore_bim_tasks.py FILE.bimbak     -> restore that file
  options: --with-env (also replace .env)  --yes (no confirmation, for scripted drills)  --check (verify only)

The server must be stopped first. Current data is moved to data/_before_restore_<time>/, never deleted.
"""
import argparse, getpass, json, os, socket, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import backup_offsite  # noqa: E402


def load_env():
    env = ROOT / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def configured_folder():
    db = ROOT / 'data' / 'bim_tasks.db'
    if db.exists():
        try:
            c = sqlite3.connect(str(db))
            r = c.execute("SELECT value FROM settings WHERE key='backup.offsite_dir'").fetchone()
            c.close()
            if r and r[0]:
                return Path(r[0])
        except sqlite3.Error:
            pass
    return Path(os.environ['BIM_OFFSITE_DIR']) if os.environ.get('BIM_OFFSITE_DIR') else None


def server_running():
    for port in (8797, 8798):
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return port
    return None


def record_restore(name, manifest):
    """Write the restore into the restored database's sensitive-operations log."""
    from app.security_center import append_audit
    c = sqlite3.connect(str(ROOT / 'data' / 'bim_tasks.db'))
    try:
        append_audit(c, {'user_id': None, 'username_snapshot': 'أداة الاسترجاع على السيرفر', 'action': 'backup_restore',
                         'entity_type': 'system', 'entity_id': None, 'before_json': None,
                         'after_json': json.dumps({'file': name, 'backup_created_at': manifest.get('created_at')}, ensure_ascii=False),
                         'reason': None, 'gateway': 'lan', 'ip_address': socket.gethostname()})
        c.commit()
    finally:
        c.close()


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument('file', nargs='?')
    ap.add_argument('--with-env', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()

    path = Path(a.file) if a.file else None
    if not path:
        folder = configured_folder()
        rows = backup_offsite.list_backups(folder) if folder else []
        if not rows:
            folder_text = folder or 'غير محدد'
            sys.exit(f'لا توجد نسخ في مجلد النسخ الخارجي ({folder_text}). مرّر مسار الملف مباشرة.')
        print('النسخ المتوفرة:')
        for i, r in enumerate(rows[:20], 1):
            print(f'  {i:>2}) {r["name"]}   {r["size"] / 1048576:.1f} MB')
        pick = input('رقم النسخة (Enter = الأحدث): ').strip() or '1'
        path = Path(rows[int(pick) - 1]['path'])
    if not path.is_file():
        sys.exit('الملف غير موجود: ' + str(path))
    if '.part' in path.name:  # downloaded from Telegram in several parts
        import tempfile
        joined = Path(tempfile.mkdtemp(prefix='bim_join_')) / path.name.split('.part')[0]
        try:
            path = backup_offsite.join_parts(path, joined)
        except backup_offsite.BackupError as e:
            sys.exit('❌ ' + str(e))

    passphrase = os.environ.get('BIM_BACKUP_PASSPHRASE') or getpass.getpass('كلمة سر النسخ (BIM_BACKUP_PASSPHRASE): ')
    print('جاري فك التشفير والتحقق…')
    try:
        manifest = backup_offsite.verify(path, passphrase)
    except backup_offsite.BackupError as e:
        sys.exit('❌ ' + str(e))
    print('✅ النسخة سليمة. تاريخها:', manifest['created_at'], '| المهام:', manifest['counts']['tasks'],
          '| الموظفون:', manifest['counts']['employees'], '| الحسابات:', manifest['counts']['users'])
    if a.check:
        return
    port = server_running()
    if port:
        sys.exit(f'❌ السيرفر شغّال (المنفذ {port}). أوقفه أولاً بـ STOP_BIM_TASKS ثم أعد التشغيل.')
    if not a.yes and input('سيتم استبدال البيانات الحالية بهذه النسخة. اكتب نعم للمتابعة: ').strip() not in ('نعم', 'yes'):
        sys.exit('تم الإلغاء.')
    result = backup_offsite.restore_into(path, passphrase, ROOT, with_env=a.with_env)
    record_restore(path.name, manifest)
    print('✅ تم الاسترجاع.')
    print('   البيانات السابقة محفوظة في:', result['previous_data'])
    if result['env'] == 'saved_as_.env.restored':
        print('   ملف .env من النسخة حُفظ باسم .env.restored (لم يُستبدل .env الحالي). استخدم --with-env لاستبداله.')
    print('   شغّل النظام الآن بـ START_BIM_TASKS.')


if __name__ == '__main__':
    main()
