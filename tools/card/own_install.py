#!/usr/bin/python3
"""Install or reinstall switch-remote's OWN files on the Switch SD card, with receipts. Nothing else is written.

  own_install.py preflight <job-dir> --transport ums --root "/run/media/intpa/SWITCH SD"
  own_install.py install   <job-dir> --transport ums --root ...
  own_install.py preflight <job-dir> --transport mtp --root "mtp://<device>/1%3A%20SD%20Card"   (DBI MTP, via gio)

<job-dir> holds `sd/` (a tree of new files under the card's layout) and `order.txt` (the operator/primary order
this run follows). Only these destinations are allowed:

  atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_switch_remote.nro
  atmosphere/contents/4200000000005352/exefs.nsp | toolbox.json | flags/boot2.flag
  ultimate/switch-remote/config.txt
  atmosphere/contents/4200000000004150/exefs.nsp | toolbox.json | flags/boot2.flag   (sys-autopilot, option d)
  config/sys-autopilot/config.ini

An optional `remove.txt` (one path per line, # comments) deletes allowed files (option d: our sysmodule's
flags/boot2.flag). Removed files are kept locally under before/,
and a non-empty one also on the card (ultimate/switch-remote/backups/removed-<sha16>.<name>); the receipt row says
`removed` (or `already_absent`) and the readback confirms the file is gone.

preflight: refuses any other path; records the card's current sha256 of each destination and of every file in the
  plugins folder and our sysmodule folder (the "before" table); keeps local copies of the files to be replaced.
install: needs a fresh preflight receipt whose before-hashes still match; for each replaced file writes a rollback copy
  on the card (ultimate/switch-remote/backups/pre-<new16>.<name>) and reads it back; writes the new file (UMS: temp
  file, fsync, rename; MTP: gio copy), reads it back from the device (UMS: fsync + posix_fadvise DONTNEED, then read;
  MTP: gio copy back to a local temp), and re-hashes every other file of the before table (the "after" table). Any
  readback mismatch restores the backup and fails. A phase whose receipt exists refuses to run again.
The user connects and disconnects the Switch: this tool never mounts, unmounts or ejects anything.
"""
import argparse
import ftplib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ALLOWED = (
    'atmosphere/contents/01006A800016E000/romfs/skyline/plugins/libssbu_switch_remote.nro',
    'atmosphere/contents/4200000000005352/exefs.nsp',
    'atmosphere/contents/4200000000005352/toolbox.json',
    'atmosphere/contents/4200000000005352/flags/boot2.flag',
    'ultimate/switch-remote/config.txt',
    # sys-autopilot, the console driver (option d, primary ruling 2026-11-16: its folder counts as ours). Its boot2.flag
    # is NOT part of the first install (its __appInit diagAborts, a fatal at boot, on any init failure on unverified
    # firmware): the first start is by hand in ovlSysmodules; a later receipted session adds the flag once it served.
    'atmosphere/contents/4200000000004150/exefs.nsp',
    'atmosphere/contents/4200000000004150/toolbox.json',
    'atmosphere/contents/4200000000004150/flags/boot2.flag',
    'config/sys-autopilot/config.ini',
)
# remove.txt may delete the same files (e.g. our sysmodule's boot2.flag when sys-autopilot takes over, option d).
REMOVABLE = ALLOWED
WATCH_DIRS = ('atmosphere/contents/01006A800016E000/romfs/skyline/plugins', 'atmosphere/contents/4200000000005352',
              'atmosphere/contents/4200000000004150')
BACKUP_DIR = 'ultimate/switch-remote/backups'
SECRET = ('ultimate/switch-remote/config.txt', 'config/sys-autopilot/config.ini')  # its hash goes in the receipt; its content never in any output


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class Ums:
    """A card mounted by the user (hekate UMS or a reader). Reads skip the page cache where possible."""

    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_dir():
            raise SystemExit(f'card not visible at {root}: ask the user to expose it (UMS or DBI)')

    def path(self, rel):
        return self.root / rel

    def exists(self, rel):
        return self.path(rel).exists()

    def read(self, rel):
        p = self.path(rel)
        fd = os.open(p, os.O_RDONLY)
        try:
            try:
                os.fsync(fd)
            except OSError:
                pass
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            chunks = []
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    break
                chunks.append(b)
            return b''.join(chunks)
        finally:
            os.close(fd)

    def write(self, rel, data):
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + '.sr-part')
        with open(tmp, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        dfd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def remove(self, rel):
        p = self.path(rel)
        os.remove(p)
        dfd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def listdir(self, rel):
        p = self.path(rel)
        if not p.is_dir():
            return []
        out = []
        for q in sorted(p.rglob('*')):
            if q.is_file():
                out.append(str(q.relative_to(self.root)))
        return out


class Mtp:
    """DBI's MTP responder through gio (info/list/copy only). `root` is the storage URI, e.g.
    mtp://-_DBI_<serial>/1%3A%20SD%20Card ; a file:// URI works too (tests)."""

    def __init__(self, root):
        self.root = root.rstrip('/')
        if self._gio('info', self.root).returncode != 0:
            raise SystemExit(f'card not visible at {root}: ask the user to start DBI\'s MTP responder')

    @staticmethod
    def _gio(*args, check=False):
        return subprocess.run(['gio', *args], capture_output=True, text=True, timeout=120, check=check)

    def uri(self, rel):
        from urllib.parse import quote
        return self.root + '/' + quote(rel)

    def exists(self, rel):
        return self._gio('info', self.uri(rel)).returncode == 0

    def read(self, rel):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / 'f'
            self._gio('copy', self.uri(rel), str(local), check=True)
            return local.read_bytes()

    def write(self, rel, data):
        parent = rel.rsplit('/', 1)[0]
        parts = parent.split('/')
        for i in range(1, len(parts) + 1):
            sub = '/'.join(parts[:i])
            if not self.exists(sub):
                self._gio('mkdir', self.uri(sub), check=True)
        with tempfile.TemporaryDirectory() as d:
            local = Path(d) / rel.rsplit('/', 1)[-1]
            local.write_bytes(data)
            # copy, never move: gio move over MTP was unreliable (DBI install note, 2026-10-13). Some MTP servers
            # refuse an overwrite; then the old file is removed first (its rollback copy is already on the card).
            if self._gio('copy', '-T', str(local), self.uri(rel)).returncode != 0:
                if self.exists(rel):
                    self._gio('remove', self.uri(rel), check=True)
                self._gio('copy', '-T', str(local), self.uri(rel), check=True)

    def remove(self, rel):
        self._gio('remove', self.uri(rel), check=True)

    def listdir(self, rel, _prefix=None):
        r = self._gio('list', '-a', 'standard::type', self.uri(rel))
        if r.returncode != 0:
            return []
        out = []
        for line in r.stdout.splitlines():
            name = line.split('\t')[0].strip()
            if not name:
                continue
            child = f'{rel}/{name}'
            if '(directory)' in line or 'standard::type=2' in line:
                out += self.listdir(child)
            else:
                out.append(child)
        return sorted(out)


def ftp_credentials(bookmarks=None):
    """(user, password) for DBI's FTP server, held in memory only, NEVER printed or logged. The user comes from the
    GTK bookmark labelled "Switch SD" (~/.config/gtk-3.0/bookmarks); the password from that URL, else from the GNOME
    keyring entry gvfs stored for it (secret-tool), else empty."""
    from urllib.parse import unquote, urlsplit
    path = Path(bookmarks or Path.home() / '.config' / 'gtk-3.0' / 'bookmarks')
    for line in path.read_text().splitlines():
        url, _, label = line.partition(' ')
        if url.startswith('ftp://') and label.startswith('Switch SD'):
            u = urlsplit(url)
            user, password = unquote(u.username or ''), unquote(u.password or '')
            if not password and shutil.which('secret-tool'):
                r = subprocess.run(['secret-tool', 'lookup', 'protocol', 'ftp', 'server', u.hostname or '', 'user', user],
                                   capture_output=True, text=True, timeout=10)
                password = r.stdout.rstrip('\n') if r.returncode == 0 else ''
            return user, password
    raise SystemExit('no "Switch SD" FTP bookmark in ~/.config/gtk-3.0/bookmarks')


class Ftp:
    """DBI's FTP server (operator note 2026-11-16 ~04:16Z; DBI launched from the Album, FTP on). `root` is
    ftp://HOST[:PORT] (default port <ftp-port>). One fresh connection per operation, TYPE I. This server's NLST ignores a
    path argument, so listings cwd first. Writes: STOR to <name>.sr-part, RETR it back and compare sha256, then rename
    into place (if the server refuses to rename over an existing file, that allowlisted file is deleted first: its
    rollback copy is already on the card and verified); the caller reads the final file back again."""

    def __init__(self, root, credentials=None):
        from urllib.parse import urlsplit
        u = urlsplit(root if '://' in root else 'ftp://' + root)
        self.host, self.port = u.hostname, u.port or 5000
        self.user, self.password = credentials or ftp_credentials()
        try:
            f = self._conn()
            f.quit()
        except (OSError, EOFError, ftplib.Error) as e:
            raise SystemExit(f'card not visible at ftp://{self.host}:{self.port}: ask the user to start DBI\'s FTP '
                             f'server ({type(e).__name__})') from None

    def _conn(self):
        f = ftplib.FTP()
        f.connect(self.host, self.port, timeout=30)
        f.login(self.user, self.password)
        f.voidcmd('TYPE I')
        return f

    @staticmethod
    def _abs(rel):
        return '/' + rel.strip('/')

    def _is_dir(self, f, rel):
        try:
            f.cwd(self._abs(rel))
            return True
        except ftplib.error_perm:
            return False

    def exists(self, rel):
        f = self._conn()
        try:
            parent, name = rel.rsplit('/', 1) if '/' in rel else ('', rel)
            if not self._is_dir(f, parent):
                return False
            return name in f.nlst()
        finally:
            f.close()

    def read(self, rel):
        f = self._conn()
        try:
            buf = io.BytesIO()
            f.retrbinary('RETR ' + self._abs(rel), buf.write)
            return buf.getvalue()
        finally:
            f.close()

    def _mkdirs(self, f, parent):
        cur = ''
        for part in [p for p in parent.split('/') if p]:
            cur += '/' + part
            try:
                f.cwd(cur)
            except ftplib.error_perm:
                f.mkd(cur)

    def write(self, rel, data):
        parent = rel.rsplit('/', 1)[0] if '/' in rel else ''
        part = rel + '.sr-part'
        f = self._conn()
        try:
            self._mkdirs(f, parent)
            f.storbinary('STOR ' + self._abs(part), io.BytesIO(data))
        finally:
            f.close()
        if hashlib.sha256(self.read(part)).hexdigest() != hashlib.sha256(data).hexdigest():
            raise SystemExit(f'refused: {part} did not read back; left in place for inspection')
        f = self._conn()
        try:
            try:
                f.rename(self._abs(part), self._abs(rel))
            except ftplib.error_perm:
                f.delete(self._abs(rel))  # an allowlisted file whose verified rollback copy is already on the card
                f.rename(self._abs(part), self._abs(rel))
        finally:
            f.close()

    def remove(self, rel):
        f = self._conn()
        try:
            f.delete(self._abs(rel))
        finally:
            f.close()

    def listdir(self, rel):
        f = self._conn()
        out = []
        try:
            def walk(d):
                if not self._is_dir(f, d):
                    return
                names = [n.rsplit('/', 1)[-1] for n in f.nlst()]
                for name in sorted(n for n in names if n not in ('.', '..')):
                    child = f'{d}/{name}' if d else name
                    if self._is_dir(f, child):
                        walk(child)
                    else:
                        out.append(child)
            walk(rel.strip('/'))
        finally:
            f.close()
        return sorted(out)


def transport(kind, root):
    return {'ums': Ums, 'mtp': Mtp, 'ftp': Ftp}[kind](root)


def job_removals(job):
    path = Path(job) / 'remove.txt'
    if not path.exists():
        return []
    rels = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith('#')]
    bad = [r for r in rels if r not in REMOVABLE]
    if bad:
        raise SystemExit(f'refused: not removable switch-remote files: {bad}')
    sd = Path(job) / 'sd'
    both = [r for r in rels if (sd / r).exists()]
    if both:
        raise SystemExit(f'refused: both written and removed by one job: {both}')
    return rels


def job_files(job):
    sd = Path(job) / 'sd'
    files = sorted(str(p.relative_to(sd)) for p in sd.rglob('*') if p.is_file()) if sd.exists() else []
    bad = [f for f in files if f not in ALLOWED]
    if bad:
        raise SystemExit(f'refused: not switch-remote files: {bad}')
    if not files and not job_removals(job):
        raise SystemExit('refused: no files under sd/ and no remove.txt')
    return {f: (sd / f).read_bytes() for f in files}


def table(card, rels):
    return {rel: (sha(card.read(rel)) if card.exists(rel) else None) for rel in rels}


def show(rel, h):
    return '(secret)' if rel in SECRET and h else h


def write_receipt(job, name, receipt):
    path = Path(job) / name
    tmp = path.with_suffix('.part')
    tmp.write_text(json.dumps(receipt, indent=1) + '\n')
    os.replace(tmp, path)
    return path


def preflight(job, card, kind):
    if (Path(job) / 'preflight-receipt.json').exists():
        raise SystemExit('refused: preflight-receipt.json exists; inspect it, or use a new job folder')
    order = (Path(job) / 'order.txt').read_text().strip() if (Path(job) / 'order.txt').exists() else ''
    if not order:
        raise SystemExit('refused: order.txt (the order this install follows) is missing or empty')
    new = job_files(job)
    removals = job_removals(job)
    watched = sorted(set(sum((card.listdir(d) for d in WATCH_DIRS), [])) | set(new) | set(removals))
    before = table(card, watched)
    keep = Path(job) / 'before'
    for rel, h in before.items():
        if (rel in new or rel in removals) and h:
            (keep / rel).parent.mkdir(parents=True, exist_ok=True)
            (keep / rel).write_bytes(card.read(rel))
    receipt = {'phase': 'preflight', 'time': now(), 'transport': kind, 'order': order,
               'files': {rel: {'new_sha256': show(rel, sha(data)), 'bytes': len(data),
                               'card_sha256_before': show(rel, before[rel])} for rel, data in new.items()},
               'removals': {rel: show(rel, before[rel]) for rel in removals},
               'watched_before': {rel: show(rel, h) for rel, h in before.items()},
               '_before_raw': before}
    write_receipt(job, 'preflight-receipt.json', receipt)
    return receipt


def install(job, card, kind):
    if (Path(job) / 'install-receipt.json').exists():
        raise SystemExit('refused: install-receipt.json exists; inspect it, or use a new job folder')
    pre_path = Path(job) / 'preflight-receipt.json'
    if not pre_path.exists():
        raise SystemExit('refused: run preflight first')
    pre = json.loads(pre_path.read_text())
    new = job_files(job)
    before = pre['_before_raw']
    drift = {rel: h for rel, h in table(card, list(before)).items() if h != before[rel]}
    if drift:
        raise SystemExit(f'refused: the card changed since preflight: {sorted(drift)}')
    rows, failed = [], None
    for rel, data in new.items():
        row = {'dest': rel, 'before': show(rel, before.get(rel)), 'new': show(rel, sha(data)), 'bytes': len(data)}
        old = card.read(rel) if before.get(rel) else None
        if old is not None and sha(old) == sha(data):
            row.update(after=row['new'], action='unchanged', readback_ok=True)
            rows.append(row)
            continue
        if old is not None:
            backup = f'{BACKUP_DIR}/pre-{sha(data)[:16]}.{rel.rsplit("/", 1)[-1]}'
            card.write(backup, old)
            row['rollback_copy'] = backup
            row['rollback_readback_ok'] = sha(card.read(backup)) == sha(old)
            if not row['rollback_readback_ok']:
                failed = f'rollback copy of {rel} did not read back'
                rows.append(row)
                break
        card.write(rel, data)
        got = sha(card.read(rel))
        row.update(after=show(rel, got), action='replaced' if old is not None else 'added', readback_ok=got == sha(data))
        rows.append(row)
        if got != sha(data):
            failed = f'{rel} read back {got[:16]}, expected {sha(data)[:16]}'
            if old is not None:
                card.write(rel, old)
                row['restored'] = sha(card.read(rel)) == sha(old)
            break
    removals = job_removals(job) if failed is None else []
    for rel in removals:
        row = {'dest': rel, 'before': show(rel, before.get(rel)), 'new': None, 'bytes': 0}
        if before.get(rel) is None:
            row.update(after=None, action='already_absent', readback_ok=True)
        else:
            old = card.read(rel)
            if old:  # a non-empty file keeps a rollback copy on the card too (an empty flag needs none)
                backup = f'{BACKUP_DIR}/removed-{sha(old)[:16]}.{rel.rsplit("/", 1)[-1]}'
                card.write(backup, old)
                row['rollback_copy'] = backup
                row['rollback_readback_ok'] = sha(card.read(backup)) == sha(old)
                if not row['rollback_readback_ok']:
                    failed = f'rollback copy of {rel} did not read back'
                    rows.append(row)
                    break
            card.remove(rel)
            gone = not card.exists(rel)
            row.update(after=None, action='removed', readback_ok=gone,
                       local_backup=str(Path(job) / 'before' / rel))
            rows.append(row)
            if not gone:
                failed = f'{rel} still present after remove'
                break
            continue
        rows.append(row)
    others = [rel for rel in before if rel not in new and rel not in removals]
    after_others = table(card, others)
    changed = sorted(rel for rel in others if after_others[rel] != before[rel])
    receipt = {'phase': 'install', 'time': now(), 'transport': kind, 'order': pre['order'], 'files': rows,
               'other_files_checked': len(others), 'other_files_changed': changed,
               'ok': failed is None and not changed, 'error': failed}
    write_receipt(job, 'install-receipt.json', receipt)
    if receipt['ok']:
        print('Writes complete and read back. The Switch can be disconnected when the user is ready.')
    return receipt


def table_md(receipt):
    lines = ['| destination | before | after | action | readback |', '| --- | --- | --- | --- | --- |']
    for r in receipt['files']:
        b = (r.get('before') or 'absent')[:16]
        a = (r.get('after') or '-')[:16]
        lines.append(f"| {r['dest']} | {b} | {a} | {r.get('action', 'failed')} | {r.get('readback_ok')} |")
    lines.append(f"\nOther files checked: {receipt['other_files_checked']}, changed: {receipt['other_files_changed'] or 'none'}")
    return '\n'.join(lines)


FETCH_EXCLUDE = ('*/config.txt', '*/backups/*', '*.sr-part')  # our token file, our rollback binaries, part files
FETCH_MAX_BYTES = 4 << 20


def fetch(out, card, paths, lists=(), exclude=FETCH_EXCLUDE, max_bytes=FETCH_MAX_BYTES):
    """READ-ONLY. `paths`: files or folders copied into `out`; `lists`: folders only listed (names, recursively).
    Every file is read twice and both reads must have the same sha256 (a truncated or interrupted transfer cannot pass).
    Files matching `exclude` or larger than `max_bytes` are recorded, not kept. Nothing on the card is written."""
    import fnmatch
    out = Path(out)
    if (out / 'fetch-manifest.json').exists():
        raise SystemExit('refused: fetch-manifest.json exists; use a new output folder')
    manifest = {'time': now(), 'files': {}, 'listings': {}, 'skipped': {}, 'absent': [], 'read_twice': True}
    for rel in lists:
        rel = rel.strip('/')
        manifest['listings'][rel] = card.listdir(rel)
    files = []
    for rel in paths:
        rel = rel.strip('/')
        listed = card.listdir(rel)
        found = listed if listed else ([rel] if card.exists(rel) else [])
        if not found:
            manifest['absent'].append(rel)
        files += found
    for rel in sorted(set(files)):
        if any(fnmatch.fnmatch('/' + rel, pat) or fnmatch.fnmatch(rel, pat) for pat in exclude):
            manifest['skipped'][rel] = 'excluded'
            continue
        first = card.read(rel)
        if len(first) > max_bytes:
            manifest['skipped'][rel] = f'{len(first)} bytes > {max_bytes}'
            continue
        second = card.read(rel)
        if sha(first) != sha(second):
            raise SystemExit(f'refused: two reads of {rel} differ ({sha(first)[:16]} vs {sha(second)[:16]})')
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_bytes(first)
        manifest['files'][rel] = {'bytes': len(first), 'sha256': show(rel, sha(first))}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'fetch-manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('phase', choices=['preflight', 'install', 'fetch'])
    ap.add_argument('job', help='the job folder (fetch: the local output folder)')
    ap.add_argument('--transport', choices=['ums', 'mtp', 'ftp'], required=True)
    ap.add_argument('--root', required=True)
    ap.add_argument('--path', action='append', default=[], help='fetch: a card file or folder to copy (repeatable)')
    ap.add_argument('--list', action='append', default=[], help='fetch: a card folder to list only (repeatable)')
    a = ap.parse_args(argv)
    card = transport(a.transport, a.root)
    if a.phase == 'fetch':
        r = fetch(a.job, card, a.path, a.list)
        print(json.dumps(r, indent=1))
        return 0
    if a.phase == 'preflight':
        r = preflight(a.job, card, a.transport)
        print(json.dumps({k: v for k, v in r.items() if k != '_before_raw' and k != 'watched_before'}, indent=1))
        return 0
    r = install(a.job, card, a.transport)
    md = table_md(r)
    (Path(a.job) / 'INSTALL.md').write_text(md + '\n')
    print(md)
    return 0 if r['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
