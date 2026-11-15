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
import hashlib
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
)
WATCH_DIRS = ('atmosphere/contents/01006A800016E000/romfs/skyline/plugins', 'atmosphere/contents/4200000000005352')
BACKUP_DIR = 'ultimate/switch-remote/backups'
SECRET = ('ultimate/switch-remote/config.txt',)  # its hash goes in the receipt; its content never in any output


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


def transport(kind, root):
    return Ums(root) if kind == 'ums' else Mtp(root)


def job_files(job):
    sd = Path(job) / 'sd'
    files = sorted(str(p.relative_to(sd)) for p in sd.rglob('*') if p.is_file())
    bad = [f for f in files if f not in ALLOWED]
    if bad:
        raise SystemExit(f'refused: not switch-remote files: {bad}')
    if not files:
        raise SystemExit('refused: no files under sd/')
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
    watched = sorted(set(sum((card.listdir(d) for d in WATCH_DIRS), [])) | set(new))
    before = table(card, watched)
    keep = Path(job) / 'before'
    for rel, h in before.items():
        if rel in new and h:
            (keep / rel).parent.mkdir(parents=True, exist_ok=True)
            (keep / rel).write_bytes(card.read(rel))
    receipt = {'phase': 'preflight', 'time': now(), 'transport': kind, 'order': order,
               'files': {rel: {'new_sha256': show(rel, sha(data)), 'bytes': len(data),
                               'card_sha256_before': show(rel, before[rel])} for rel, data in new.items()},
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
    others = [rel for rel in before if rel not in new]
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('phase', choices=['preflight', 'install'])
    ap.add_argument('job')
    ap.add_argument('--transport', choices=['ums', 'mtp'], required=True)
    ap.add_argument('--root', required=True)
    a = ap.parse_args(argv)
    card = transport(a.transport, a.root)
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
