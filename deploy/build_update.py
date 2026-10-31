"""Build a self-installing update zip from the git difference between what the server runs and HEAD.

  python deploy/build_update.py NAME BASE_REF "Arabic title" [--check /api/route-only-in-new-code]
         [--pip cryptography] [--wheels DIR] [--after "line shown after install" ...]

BASE_REF is the commit currently installed on the server (normally `main`).
Output: dist/BIM_Update_<NAME>.zip containing deploy/installer/* + update.json + payload/ (+ wheels/).
The payload always carries app/release.json with the commit; the installer reads it back from
/api/release-health on every port, so it proves the new code runs with the right gateway even without --check.
INSTALL_UPDATE.cmd installs on the real system, INSTALL_TO_TEST.cmd on the staging copy (C:\\BIM_Tasks_Test).
"""
import argparse, json, shutil, subprocess, sys, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOT_SHIPPED = ('tests/', 'deploy/', 'docs/', 'dist/', '.github/', 'CLAUDE.md', 'README.md', '.gitignore')


def git(*args):
    return subprocess.run(['git', *args], cwd=REPO, check=True, capture_output=True, text=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('name'); ap.add_argument('base'); ap.add_argument('title')
    ap.add_argument('--check', default='', help='optional route that exists only in the new code')
    ap.add_argument('--pip', action='append', default=[])
    ap.add_argument('--wheels')
    ap.add_argument('--after', action='append', default=[])
    a = ap.parse_args()
    if git('status', '--porcelain').strip():
        sys.exit('Commit your changes first: the package must match a commit.')
    changed, deleted = [], []
    for line in git('diff', '--name-status', '--no-renames', a.base, 'HEAD').splitlines():
        status, path = line.split('\t', 1)
        if path.startswith(NOT_SHIPPED):
            continue
        (deleted if status == 'D' else changed).append(path)
    if not changed and not deleted:
        sys.exit('Nothing to ship.')
    out = REPO / 'dist' / f'BIM_Update_{a.name}'
    shutil.rmtree(out, ignore_errors=True)
    (out / 'payload').mkdir(parents=True)
    for f in (REPO / 'deploy' / 'installer').iterdir():
        shutil.copy2(f, out / f.name)
    for path in changed:
        dst = out / 'payload' / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(subprocess.run(['git', 'show', f'HEAD:{path}'], cwd=REPO, check=True, capture_output=True).stdout)
    commit = git('rev-parse', '--short', 'HEAD').strip()
    (out / 'payload' / 'app').mkdir(parents=True, exist_ok=True)
    (out / 'payload' / 'app' / 'release.json').write_text(
        json.dumps({'commit': commit, 'name': a.name}), encoding='utf-8')
    if a.wheels:
        shutil.copytree(a.wheels, out / 'wheels')
    update = {'name': a.name, 'title': a.title, 'check_path': a.check, 'pip': a.pip,
              'delete': [p.replace('/', '\\') for p in deleted], 'after': a.after,
              'commit': commit, 'base': git('rev-parse', '--short', a.base).strip()}
    (out / 'update.json').write_text(json.dumps(update, ensure_ascii=False, indent=1), encoding='utf-8')
    (out / 'اقرأني.txt').write_text(
        f'BIM Tasks — {a.title}\r\n\r\n'
        '1) انسخ هالمجلد على السيرفر.\r\n'
        '2) كبسة يمين على INSTALL_UPDATE.cmd ثم "Run as administrator".\r\n'
        '   (لتجربته أول على النسخة التجريبية: INSTALL_TO_TEST.cmd بنفس الطريقة)\r\n'
        '3) لا تكبس بالماوس جوّا النافذة السودا وقت التثبيت. انتظر "تم التحديث بنجاح".\r\n\r\n'
        'بياخد نسخة احتياطية لحاله، وإذا فشل شي بيرجّع النظام متل ما كان.\r\n'
        'إذا النظام واقف لأي سبب: START_BIM_NOW.cmd كمسؤول.\r\n', encoding='utf-8')
    zpath = out.with_suffix('.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(out.parent).as_posix())
    print(zpath)
    print(f'{len(changed)} files, {len(deleted)} deleted, base {update["base"]} -> {update["commit"]}')


if __name__ == '__main__':
    main()
