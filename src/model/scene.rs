//! The scene report: what the NRO reads and the line it sends to the sysmodule.

use core::fmt::Write as _;

/// The game version every address here belongs to.
pub const SUPPORTED_VERSION: &str = "13.0.5";

/// The game's mode word, `main+0x53050f0` (TrainingRooms rc15 `runtime/lobby/game_mode.rs:44-55`).
pub const MODE_WORD: usize = 0x53050f0;

/// A guarded site: `offset` in main's `.text`, and its 64 bytes in 13.0.5.
#[derive(Clone, Copy, Debug)]
pub struct Site {
    pub name: &'static str,
    pub offset: usize,
    pub guard: [u8; 64],
}

/// The game's own reader of the mode word: `adrp x8, …; ldr w8, [x8, #0xf0]; and w8, w8,
/// #0x7fff0000; cmp …` (stock 13.0.5 `text.bin` at 0x1360928, found with `tools/find_refs.py`).
/// If these bytes differ, the address or the meaning of the word may differ, so the report says
/// `guards=0` and the sysmodule keeps input off.
pub const MODE_READER: Site = Site {
    name: "mode_reader",
    offset: 0x1360928,
    guard: [
        0x28, 0xfd, 0x01, 0xb0, 0x08, 0xf1, 0x40, 0xb9, 0x08, 0x39, 0x10, 0x12, 0xa9, 0xbf, 0xbf, 0x12,
        0x1f, 0x01, 0x09, 0x6b, 0x6c, 0x01, 0x00, 0x54, 0xe9, 0x03, 0x07, 0x32, 0x1f, 0x01, 0x09, 0x6b,
        0x20, 0x02, 0x00, 0x54, 0x29, 0x40, 0xa0, 0x52, 0x1f, 0x01, 0x09, 0x6b, 0xc0, 0x01, 0x00, 0x54,
        0x49, 0x40, 0xa0, 0x52, 0x1f, 0x01, 0x09, 0x6b, 0x60, 0x01, 0x00, 0x54, 0x60, 0x00, 0x00, 0x14,
    ],
};

/// The ADRP at the reader's start resolves to the mode word's page, and the LDR after it uses the
/// word's low 12 bits: proof the guard is about this word, checked on the host.
pub fn reader_targets(site_offset: usize, words: [u32; 2]) -> Option<usize> {
    let adrp = words[0];
    if adrp & 0x9f00_0000 != 0x9000_0000 {
        return None;
    }
    let imm = (((adrp >> 5) & 0x7ffff) << 2) | ((adrp >> 29) & 3);
    let imm = ((imm as i64) << 43) >> 43; // sign-extend 21 bits
    let page = ((site_offset as i64 & !0xfff) + (imm << 12)) as usize;
    let ldr = words[1];
    // LDR (unsigned immediate), 32-bit: size=10, opc=01.
    if ldr & 0xffc0_0000 != 0xb940_0000 {
        return None;
    }
    let lo12 = ((ldr >> 10) & 0xfff) as usize * 4;
    Some(page + lo12)
}

/// Online Deluxe's state export and its ONLINE bit
/// (`third_party/deluxe-plugin-sdk/abi/src/lib.rs:105-121` in TrainingRooms rc15).
pub const DELUXE_EXPORT: &[u8] = b"ssbu_online_deluxe_online_context_v1\0";
pub const DELUXE_ONLINE: u32 = 2;

/// The same family rule as the sysmodule's gate (`sysmodule/core/core.cpp` `mode_is_online`).
pub fn mode_is_online(mode: u32) -> bool {
    mode & 0x7f00_0000 == 0x0700_0000
}

/// `deluxe`: None when Online Deluxe is not loaded.
pub fn deluxe_field(deluxe: Option<u32>) -> i32 {
    match deluxe {
        None => -1,
        Some(bits) => (bits & DELUXE_ONLINE != 0) as i32,
    }
}

/// `SCENE <seq> 0x<mode> <deluxe -1|0|1> <guards 0|1>\n`, the line the sysmodule parses
/// (`sysmodule/core/core.cpp` `parse_request`).
pub fn scene_line<W: core::fmt::Write>(out: &mut W, seq: u64, mode: u32, deluxe: Option<u32>, guards_ok: bool) {
    let _ = writeln!(out, "SCENE {} 0x{:x} {} {}", seq, mode, deluxe_field(deluxe), guards_ok as u8);
}

/// Sysmodule's scene port: the host port + 1 (`sysmodule/core/core.hpp` `kDefaultPort`).
pub fn scene_port(host_port: u16) -> u16 {
    host_port.wrapping_add(1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::protocol::LineBuf;

    #[test]
    fn guard_bytes_are_64() {
        assert_eq!(MODE_READER.guard.len(), 64);
        let w = |i: usize| u32::from_le_bytes(MODE_READER.guard[i * 4..i * 4 + 4].try_into().unwrap());
        // The reader loads exactly the mode word.
        assert_eq!(reader_targets(MODE_READER.offset, [w(0), w(1)]), Some(MODE_WORD));
        // and w8, w8, #0x7fff0000: the game's own family mask.
        assert_eq!(w(2), 0x1210_3908);
    }

    #[test]
    fn reader_target_rejects_other_instructions() {
        assert_eq!(reader_targets(0x1360928, [0xd503_201f, 0xb940_f108]), None); // nop, not adrp
        assert_eq!(reader_targets(0x1360928, [0xb001_fd28, 0xf940_f108]), None); // 64-bit ldr
    }

    #[test]
    fn scene_line_format() {
        let mut b = LineBuf::<64>::default();
        scene_line(&mut b, 7, 0x0102_0000, None, true);
        assert_eq!(b.as_bytes(), b"SCENE 7 0x1020000 -1 1\n");
        b.clear();
        scene_line(&mut b, 8, 0x0701_0000, Some(DELUXE_ONLINE | 1), false);
        assert_eq!(b.as_bytes(), b"SCENE 8 0x7010000 1 0\n");
        b.clear();
        scene_line(&mut b, 9, 0, Some(1), true);
        assert_eq!(b.as_bytes(), b"SCENE 9 0x0 0 1\n");
    }

    #[test]
    fn online_family_matches_the_sysmodule() {
        assert!(mode_is_online(0x0701_0000));
        assert!(mode_is_online(0x8702_0400));
        assert!(!mode_is_online(0x0201_0000));
        assert!(!mode_is_online(0));
        assert_eq!(scene_port(47130), 47131);
    }
}
