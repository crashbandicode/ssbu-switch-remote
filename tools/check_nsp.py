#!/usr/bin/python3
"""Static checks of a sysmodule exefs.nsp against what Atmosphere's loader validates (AMS 1.12.2
stratosphere/loader/source/ldr_meta.cpp: Npdm/Acid/Aci magic, the ACID production flag, version fields when the
SDK version is old, sub-region bounds, matching program ids). Prints JSON; exit 1 when a check fails.

  check_nsp.py <exefs.nsp> [--program-id 0x4200000000005352]
"""
import json
import struct
import sys


def pfs0(data):
    if data[:4] != b'PFS0':
        raise ValueError('not a PFS0')
    n, strsz = struct.unpack_from('<II', data, 4)
    entries, base = [], 0x10 + n * 0x18
    for i in range(n):
        off, size, name_off = struct.unpack_from('<QQI', data, 0x10 + i * 0x18)
        name = data[base + name_off:data.index(b'\0', base + name_off)].decode()
        entries.append((name, off, size))
    data_start = base + strsz
    return {name: data[data_start + off:data_start + off + size] for name, off, size in entries}


def check(path, program_id=None):
    files = pfs0(open(path, 'rb').read())
    out = {'files': sorted(files), 'checks': {}}
    c = out['checks']
    c['has_main_and_npdm'] = 'main' in files and 'main.npdm' in files
    c['main_is_nso'] = files.get('main', b'')[:4] == b'NSO0'
    npdm = files.get('main.npdm', b'')
    c['npdm_magic'] = npdm[:4] == b'META'
    flags0 = npdm[0xC]
    out['is_64bit'] = bool(flags0 & 1)
    out['address_space_type'] = (flags0 >> 1) & 7
    out['main_thread_priority'] = npdm[0xE]
    out['default_cpu'] = npdm[0xF]
    out['main_thread_stack'] = hex(struct.unpack_from('<I', npdm, 0x1C)[0])
    aci_off, aci_size, acid_off, acid_size = struct.unpack_from('<IIII', npdm, 0x70)
    c['regions_in_bounds'] = aci_off + aci_size <= len(npdm) and acid_off + acid_size <= len(npdm)
    acid = npdm[acid_off:acid_off + acid_size]
    aci = npdm[aci_off:aci_off + aci_size]
    c['acid_magic'] = acid[0x200:0x204] == b'ACID'
    acid_flags = struct.unpack_from('<I', acid, 0x20C)[0]
    c['acid_production_flag'] = bool(acid_flags & 1)
    acid_version, acid_209 = acid[0x208], acid[0x209]
    out['acid_version'] = [acid_version, acid_209]
    pid_min, pid_max = struct.unpack_from('<QQ', acid, 0x210)
    c['aci_magic'] = aci[:4] == b'ACI0'
    aci_pid = struct.unpack_from('<Q', aci, 0x10)[0]
    out['program_id'] = hex(aci_pid)
    c['aci_pid_in_acid_range'] = pid_min <= aci_pid <= pid_max
    if program_id is not None:
        c['program_id_matches_folder'] = aci_pid == program_id
    # ACI0 sub-regions
    fac_off, fac_size, sac_off, sac_size, kac_off, kac_size = struct.unpack_from('<IIIIII', aci, 0x20)
    c['aci_subregions_in_bounds'] = all(o + s <= len(aci) for o, s in ((fac_off, fac_size), (sac_off, sac_size), (kac_off, kac_size)))
    sac, services, i = aci[sac_off:sac_off + sac_size], [], 0
    while i < len(sac):
        ctrl = sac[i]
        n = (ctrl & 7) + 1
        services.append(sac[i + 1:i + 1 + n].decode())
        i += 1 + n
    out['services'] = services
    kac = struct.unpack_from(f'<{kac_size // 4}I', aci, kac_off)
    syscalls = []
    for word in kac:
        # Kernel capability type = number of trailing one bits (KernelFlags 3, SyscallMask 4, MinKernelVersion 13,
        # HandleTableSize 15).
        t = (~word & (word + 1)).bit_length() - 1
        if t == 4:
            index = (word >> 29) & 7
            mask = (word >> 5) & 0xFFFFFF
            syscalls += [index * 24 + b for b in range(24) if mask >> b & 1]
        elif t == 3:
            out['kernel_flags'] = {'lowest_prio': (word >> 4) & 63, 'highest_prio': (word >> 10) & 63,
                                   'min_core': (word >> 16) & 255, 'max_core': (word >> 24) & 255}
        elif t == 15:
            out['handle_table_size'] = (word >> 16) & 0x3FF
    out['syscalls'] = [hex(s) for s in sorted(syscalls)]
    kf = out.get('kernel_flags', {})
    c['main_prio_in_range'] = kf.get('highest_prio', 99) <= out['main_thread_priority'] <= kf.get('lowest_prio', -1) \
        if kf.get('highest_prio', 0) <= kf.get('lowest_prio', 0) else \
        kf.get('lowest_prio', 99) <= out['main_thread_priority'] <= kf.get('highest_prio', -1)
    c['default_cpu_in_range'] = kf.get('min_core', 99) <= out['default_cpu'] <= kf.get('max_core', -1)
    out['ok'] = all(c.values())
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    pid = int(argv[argv.index('--program-id') + 1], 16) if '--program-id' in argv else None
    r = check(argv[0], pid)
    print(json.dumps(r, indent=1))
    return 0 if r['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
