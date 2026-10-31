"""Encrypted daily backup to a location outside the server, plus the matching decryption for restore.

The archive (database snapshot + attachments + .env + company logo) is encrypted with a
passphrase that lives only in .env (BIM_BACKUP_PASSPHRASE) and on paper with the manager.
Without the passphrase an off-site copy is useless to whoever finds it - and to us.

File format (.bimbak):
  MAGIC(8) | salt(16) | nonce_prefix(8) | chunks...
  chunk = length(4, big endian) | AES-256-GCM ciphertext
  nonce = nonce_prefix + counter(4); AAD = MAGIC + counter + final flag,
  so reordered, removed or truncated chunks fail to decrypt.
"""
import hashlib, json, os, secrets, shutil, sqlite3, struct, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
MAGIC = b'BIMBAK01'
CHUNK = 4 * 1024 * 1024
SCRYPT = dict(n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
PREFIX = 'BIM_Offsite_'
SUFFIX = '.bimbak'


class BackupError(Exception):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase or '') < 16:
        raise BackupError('BIM_BACKUP_PASSPHRASE يجب ألا تقل عن 16 حرفاً')
    return hashlib.scrypt(passphrase.encode('utf-8'), salt=salt, **SCRYPT)


def _aead():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - dependency listed in requirements.txt
        raise BackupError('مكتبة cryptography غير مثبتة؛ شغّل pip install -r requirements.txt') from e
    return AESGCM


def encrypt_file(src: Path, dst: Path, passphrase: str):
    AESGCM = _aead()
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(8)
    box = AESGCM(_key(passphrase, salt))
    with open(src, 'rb') as fin, open(dst, 'wb') as fout:
        fout.write(MAGIC + salt + prefix)
        counter, block = 0, fin.read(CHUNK)
        while True:
            nxt = fin.read(CHUNK)
            final = not nxt
            aad = MAGIC + struct.pack('>IB', counter, int(final))
            ct = box.encrypt(prefix + struct.pack('>I', counter), block, aad)
            fout.write(struct.pack('>I', len(ct)) + ct)
            if final:
                break
            counter, block = counter + 1, nxt


def decrypt_file(src: Path, dst: Path, passphrase: str):
    AESGCM = _aead()
    from cryptography.exceptions import InvalidTag
    with open(src, 'rb') as fin, open(dst, 'wb') as fout:
        head = fin.read(32)
        if len(head) != 32 or head[:8] != MAGIC:
            raise BackupError('الملف ليس نسخة BIM مشفرة')
        box = AESGCM(_key(passphrase, head[8:24]))
        prefix, counter, done = head[24:32], 0, False
        while not done:
            raw = fin.read(4)
            if len(raw) != 4:
                raise BackupError('النسخة ناقصة أو مقطوعة')
            ct = fin.read(struct.unpack('>I', raw)[0])
            nonce = prefix + struct.pack('>I', counter)
            for final in (0, 1):
                try:
                    pt = box.decrypt(nonce, ct, MAGIC + struct.pack('>IB', counter, final))
                    done = bool(final)
                    break
                except InvalidTag:
                    pt = None
            if pt is None:
                raise BackupError('كلمة سر النسخ غير صحيحة أو الملف معدّل')
            fout.write(pt)
            counter += 1
        if fin.read(1):
            raise BackupError('بيانات زائدة بعد نهاية النسخة؛ الملف معدّل')


def db_snapshot(db: Path, dest: Path) -> dict:
    """Consistent copy of the live WAL database, then prove it is readable."""
    src = sqlite3.connect(str(db), timeout=30)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()
    chk = sqlite3.connect(str(dest))
    try:
        ok = chk.execute('PRAGMA integrity_check').fetchone()[0]
        if ok != 'ok':
            raise BackupError('فحص سلامة قاعدة البيانات فشل: ' + str(ok))
        counts = {t: chk.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('tasks', 'employees', 'users')}
    finally:
        chk.close()
    return counts


def build_archive(work: Path, db: Path) -> tuple[Path, dict]:
    snap = work / 'bim_tasks.db'
    counts = db_snapshot(db, snap)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'counts': counts,
                'format': 1, 'includes': ['data/bim_tasks.db', 'data/attachments', '.env', 'data/company_logo']}
    out = work / 'archive.zip'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        z.writestr('MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=1))
        z.write(snap, 'data/bim_tasks.db')
        for extra in (ROOT / '.env', DATA / 'company_logo'):
            if extra.is_file():
                z.write(extra, extra.relative_to(ROOT).as_posix())
        att = DATA / 'attachments'
        if att.exists():
            for p in att.rglob('*'):
                if p.is_file():
                    z.write(p, p.relative_to(ROOT).as_posix())
    return out, manifest


def verify(path: Path, passphrase: str) -> dict:
    """Decrypt to a temp file and check every zip member CRC and the database."""
    with tempfile.TemporaryDirectory(prefix='bim_verify_') as tmp:
        plain = Path(tmp) / 'archive.zip'
        decrypt_file(path, plain, passphrase)
        with zipfile.ZipFile(plain) as z:
            bad = z.testzip()
            if bad:
                raise BackupError('ملف تالف داخل النسخة: ' + bad)
            manifest = json.loads(z.read('MANIFEST.json'))
            z.extract('data/bim_tasks.db', tmp)
        chk = sqlite3.connect(str(Path(tmp) / 'data' / 'bim_tasks.db'))
        try:
            if chk.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('قاعدة البيانات داخل النسخة غير سليمة')
        finally:
            chk.close()
    return manifest


def list_backups(folder: Path):
    if not folder or not folder.is_dir():
        return []
    rows = []
    for p in sorted(folder.glob(PREFIX + '*' + SUFFIX), reverse=True):
        st = p.stat()
        rows.append({'name': p.name, 'path': str(p), 'size': st.st_size,
                     'modified': datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')})
    return rows


def run(dest_dir: str, passphrase: str, keep: int = 30, db: Path | None = None) -> dict:
    """Create, verify and rotate one off-site backup. Raises BackupError with an Arabic reason."""
    if not dest_dir:
        raise BackupError('لم يُحدد مجلد النسخ خارج السيرفر')
    dest = Path(dest_dir)
    if not dest.is_dir():
        raise BackupError('مجلد النسخ خارج السيرفر غير موجود أو غير متصل: ' + str(dest))
    db = db or (DATA / 'bim_tasks.db')
    if not db.exists():
        raise BackupError('قاعدة البيانات غير موجودة')
    _key(passphrase, b'0' * 16)  # validate passphrase length before doing any work
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    final = dest / f'{PREFIX}{stamp}{SUFFIX}'
    partial = dest / f'.{PREFIX}{stamp}.partial'
    with tempfile.TemporaryDirectory(prefix='bim_backup_') as tmp:
        archive, manifest = build_archive(Path(tmp), db)
        try:
            encrypt_file(archive, partial, passphrase)
            verify(partial, passphrase)
            os.replace(partial, final)
        finally:
            if partial.exists():
                partial.unlink()
    removed = []
    for old in list_backups(dest)[max(int(keep), 1):]:
        try:
            Path(old['path']).unlink(); removed.append(old['name'])
        except OSError:
            pass
    return {'ok': True, 'file': final.name, 'folder': str(dest), 'size': final.stat().st_size,
            'counts': manifest['counts'], 'removed': removed}


def restore_into(backup: Path, passphrase: str, root: Path = ROOT, with_env: bool = False) -> dict:
    """Replace data/ with the backup content. The previous data is moved aside, never deleted."""
    data = root / 'data'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safety = data / f'_before_restore_{stamp}'
    with tempfile.TemporaryDirectory(prefix='bim_restore_') as tmp:
        plain = Path(tmp) / 'archive.zip'
        decrypt_file(backup, plain, passphrase)
        out = Path(tmp) / 'x'
        with zipfile.ZipFile(plain) as z:
            if z.testzip():
                raise BackupError('النسخة تالفة')
            for name in z.namelist():
                target = (out / name).resolve()
                if not str(target).startswith(str(out.resolve())):
                    raise BackupError('مسار غير آمن داخل النسخة: ' + name)
            z.extractall(out)
            manifest = json.loads(z.read('MANIFEST.json'))
        chk = sqlite3.connect(str(out / 'data' / 'bim_tasks.db'))
        try:
            if chk.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('قاعدة البيانات داخل النسخة غير سليمة')
        finally:
            chk.close()
        safety.mkdir(parents=True)
        for name in ('bim_tasks.db', 'bim_tasks.db-wal', 'bim_tasks.db-shm', 'attachments', 'company_logo'):
            if (data / name).exists():
                shutil.move(str(data / name), str(safety / name))
        shutil.copy2(out / 'data' / 'bim_tasks.db', data / 'bim_tasks.db')
        if (out / 'data' / 'attachments').exists():
            shutil.copytree(out / 'data' / 'attachments', data / 'attachments')
        if (out / 'data' / 'company_logo').exists():
            shutil.copy2(out / 'data' / 'company_logo', data / 'company_logo')
        env_note = None
        if (out / '.env').exists():
            if with_env:
                if (root / '.env').exists():
                    shutil.copy2(root / '.env', safety / '.env')
                shutil.copy2(out / '.env', root / '.env'); env_note = 'replaced'
            else:
                shutil.copy2(out / '.env', root / '.env.restored'); env_note = 'saved_as_.env.restored'
    return {'ok': True, 'manifest': manifest, 'previous_data': str(safety), 'env': env_note}
