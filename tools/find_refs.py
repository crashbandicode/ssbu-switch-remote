#!/usr/bin/python3
"""Find instructions in a main text image that load a given data address (ADRP + LDR/ADD lo12 within 6 words).

Usage: find_refs.py <text.bin> <hex offset> [--bytes N]
Prints each hit's ADRP offset, the using instruction and, with --bytes, N bytes at the ADRP as hex.
The text image is the 13.0.5 main .text loaded at offset 0 (stock exefs text.bin)."""
import struct
import sys


def adrp_target(word, pc):
    if word & 0x9f000000 != 0x90000000:
        return None
    immlo = (word >> 29) & 3
    immhi = (word >> 5) & 0x7ffff
    imm = (immhi << 2) | immlo
    if imm & (1 << 20):
        imm -= 1 << 21
    return (pc & ~0xfff) + (imm << 12), word & 31


def lo12_use(word, reg):
    """(kind, byte offset) when `word` uses `reg` as base with an unsigned 12-bit offset."""
    rn = (word >> 5) & 31
    if rn != reg:
        return None
    if word & 0xff800000 == 0x91000000:  # ADD (immediate), 64-bit, no shift
        return 'add', (word >> 10) & 0xfff
    if word & 0x3b000000 == 0x39000000 and (word >> 26) & 1 == 0:  # LDR/STR (unsigned immediate), integer
        size = word >> 30
        opc = (word >> 22) & 3
        return ('ldr' if opc else 'str') + str(8 << size), ((word >> 10) & 0xfff) << size
    return None


def main():
    path, target = sys.argv[1], int(sys.argv[2], 16)
    nbytes = int(sys.argv[sys.argv.index('--bytes') + 1]) if '--bytes' in sys.argv else 0
    data = open(path, 'rb').read()
    words = struct.unpack_from(f'<{len(data) // 4}I', data)
    for i, w in enumerate(words):
        hit = adrp_target(w, i * 4)
        if not hit or hit[0] != target & ~0xfff:
            continue
        page, reg = hit
        for j in range(i + 1, min(i + 7, len(words))):
            use = lo12_use(words[j], reg)
            if use and use[1] == target & 0xfff:
                line = f'adrp=0x{i * 4:x} use=0x{j * 4:x} {use[0]}'
                if nbytes:
                    line += ' bytes=' + data[i * 4:i * 4 + nbytes].hex()
                print(line)
                break


if __name__ == '__main__':
    main()
