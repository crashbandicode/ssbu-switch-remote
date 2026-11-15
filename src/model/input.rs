//! Controller input as the host describes it, and the state the hid hook applies.
//!
//! Button bits: an Eden macro's `buttons` is a 22-bit mask, bit i = `ButtonNames()[i]`
//! (Eden `src/yuzu/macro_file.cpp:323-331`: a b x y lstick rstick l r zl zr plus minus
//! left up right down sl_left sr_left home capture sl_right sr_right). Bits 0..15 are the
//! same order as the console's `nn::hid::NpadButton` bits, so they pass through
//! unchanged. Bits 16..21 differ from the console layout and are refused (`home` and
//! `capture` are system buttons a game-side hook cannot press; SL/SR are Joy-Con rail
//! buttons that Smash does not read in menus).
//!
//! Everything here is pure: callers pass the current tick, so the lease logic is
//! host-tested and the hook only does loads and compares.

/// Eden's button names, in macro-bit order.
pub const BUTTON_NAMES: [&str; 22] = [
    "a", "b", "x", "y", "lstick", "rstick", "l", "r", "zl", "zr", "plus", "minus", "left", "up",
    "right", "down", "sl_left", "sr_left", "home", "capture", "sl_right", "sr_right",
];

/// Bits the plugin injects: the 16 that map 1:1 onto `nn::hid::NpadButton`.
pub const INJECTABLE_MASK: u32 = 0xffff;
/// Every bit an Eden macro may carry.
pub const MACRO_MASK: u32 = (1 << 22) - 1;
/// Number of per-button leases kept.
pub const BUTTONS: usize = 16;

/// The macro bit for a button name (Eden's spelling; `dleft`/`dup`/… accepted too).
pub fn button_bit(name: &str) -> Option<u32> {
    let name = match name {
        "dleft" => "left",
        "dup" => "up",
        "dright" => "right",
        "ddown" => "down",
        "start" => "plus",
        "select" => "minus",
        other => other,
    };
    BUTTON_NAMES.iter().position(|n| *n == name).map(|i| i as u32)
}

/// Stick value on the wire: -32767..=32767 (the console's `AnalogStickState` range).
pub const STICK_MAX: i32 = 32767;

/// Eden's float stick value (-1..1) as a console stick value.
pub fn stick_from_unit(value: f64) -> i32 {
    if !value.is_finite() {
        return 0;
    }
    (value.clamp(-1.0, 1.0) * STICK_MAX as f64).round() as i32
}

/// Two stick axes packed into one word, so a hook reads x and y together.
pub fn pack_stick(x: i32, y: i32) -> u32 {
    let x = x.clamp(-STICK_MAX, STICK_MAX) as i16 as u16 as u32;
    let y = y.clamp(-STICK_MAX, STICK_MAX) as i16 as u16 as u32;
    x | (y << 16)
}

pub fn unpack_stick(word: u32) -> (i32, i32) {
    ((word & 0xffff) as u16 as i16 as i32, (word >> 16) as u16 as i16 as i32)
}

/// Longest lease the host may ask for, as in Eden (`CLIControl.md`: 1–10000 ms).
pub const LEASE_MAX_MS: u32 = 10_000;

/// Deadline tick for a lease of `ms` starting at `now`. `freq` is ticks per second.
pub fn lease_deadline(now: u64, ms: u32, freq: u64) -> u64 {
    let ms = ms.clamp(1, LEASE_MAX_MS) as u64;
    now.saturating_add(ms.saturating_mul(freq) / 1000).max(1)
}

/// Held-button mask from per-button deadlines (0 = released).
pub fn held_mask(deadlines: &[u64; BUTTONS], now: u64) -> u32 {
    let mut mask = 0;
    for (bit, deadline) in deadlines.iter().enumerate() {
        if *deadline > now {
            mask |= 1 << bit;
        }
    }
    mask
}

/// One sample of a macro: the state from `frame` on, until the next sample.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Sample {
    pub frame: u32,
    pub buttons: u32,
    /// lx, ly, rx, ry in console units.
    pub sticks: [i16; 4],
}

/// Most samples a macro may hold. The library's longest segment has 33 samples; Eden
/// auto-saves a recording at 60,000. 4,096 samples is 64 KiB of static storage.
pub const MACRO_SAMPLES: usize = 4096;

/// Checks a macro's samples as the plugin receives them: frames strictly ascending,
/// only injectable buttons, the last sample before `duration`.
pub fn check_sample(previous: Option<&Sample>, sample: &Sample, duration: u32) -> Result<(), &'static str> {
    if sample.buttons & !MACRO_MASK != 0 {
        return Err("bits_outside_mask");
    }
    if sample.buttons & !INJECTABLE_MASK != 0 {
        return Err("system_button");
    }
    if let Some(p) = previous {
        if sample.frame <= p.frame {
            return Err("frames_not_ascending");
        }
    }
    if sample.frame >= duration {
        return Err("frame_past_duration");
    }
    Ok(())
}

/// Index of the sample in force at `frame` (the last one whose frame is <= it), starting
/// the search at `from` (the index used last frame), so playback is O(1) per frame.
pub fn sample_at(samples: &[Sample], from: usize, frame: u32) -> Option<usize> {
    if samples.is_empty() || samples[0].frame > frame {
        return None;
    }
    let mut i = from.min(samples.len() - 1);
    if samples[i].frame > frame {
        i = 0;
    }
    while i + 1 < samples.len() && samples[i + 1].frame <= frame {
        i += 1;
    }
    Some(i)
}

/// What the hook writes into one npad state.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Injection {
    /// Buttons ORed into the real state (live input), or the whole state (macro).
    pub buttons: u32,
    /// Stick overrides: `Some((x, y))` replaces that stick.
    pub left: Option<(i32, i32)>,
    pub right: Option<(i32, i32)>,
    /// A macro owns the controller: the real buttons and sticks are replaced, as Eden's
    /// playback "takes ownership of controller input" (`InputMacros.md`).
    pub exclusive: bool,
}

impl Injection {
    pub fn is_empty(&self) -> bool {
        !self.exclusive && self.buttons == 0 && self.left.is_none() && self.right.is_none()
    }

    pub fn from_sample(s: &Sample) -> Self {
        Self {
            buttons: s.buttons & INJECTABLE_MASK,
            left: Some((s.sticks[0] as i32, s.sticks[1] as i32)),
            right: Some((s.sticks[2] as i32, s.sticks[3] as i32)),
            exclusive: true,
        }
    }

    /// Applies this injection to a console state's fields.
    pub fn apply(&self, buttons: &mut u64, left: &mut (i32, i32), right: &mut (i32, i32)) {
        if self.exclusive {
            *buttons &= !(INJECTABLE_MASK as u64);
        }
        *buttons |= (self.buttons & INJECTABLE_MASK) as u64;
        if let Some(l) = self.left {
            *left = l;
        }
        if let Some(r) = self.right {
            *right = r;
        }
        // The console derives the stick-direction bits (16..23: StickL/R Left/Up/Right/Down)
        // from the stick values; keep them consistent with what was injected.
        if self.exclusive || self.left.is_some() || self.right.is_some() {
            *buttons &= !STICK_DIRECTION_BITS;
            *buttons |= stick_direction_bits(*left, *right);
        }
    }
}

/// `nn::hid::NpadButton` bits 16..23: StickLLeft, StickLUp, StickLRight, StickLDown,
/// StickRLeft, StickRUp, StickRRight, StickRDown.
pub const STICK_DIRECTION_BITS: u64 = 0xff << 16;
/// Threshold the direction bits use. ASSUMPTION: half deflection, as homebrew references
/// describe the system's emulated stick buttons; only menus that read these bits care.
pub const STICK_DIRECTION_THRESHOLD: i32 = STICK_MAX / 2;

pub fn stick_direction_bits(left: (i32, i32), right: (i32, i32)) -> u64 {
    let one = |(x, y): (i32, i32), shift: u32| -> u64 {
        let t = STICK_DIRECTION_THRESHOLD;
        let mut b = 0u64;
        if x < -t {
            b |= 1;
        }
        if y > t {
            b |= 2;
        }
        if x > t {
            b |= 4;
        }
        if y < -t {
            b |= 8;
        }
        b << shift
    };
    one(left, 16) | one(right, 20)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn button_bits_follow_eden_macro_order() {
        assert_eq!(button_bit("a"), Some(0));
        assert_eq!(button_bit("zr"), Some(9));
        assert_eq!(button_bit("lstick"), Some(4));
        assert_eq!(button_bit("down"), Some(15));
        assert_eq!(button_bit("ddown"), Some(15));
        assert_eq!(button_bit("home"), Some(18));
        assert_eq!(button_bit("nope"), None);
        // tap-a.edenmacro.json presses buttons=1; open-sidebar presses 512 (zr).
        assert_eq!(1u32 << button_bit("zr").unwrap(), 512);
    }

    #[test]
    fn sticks_pack_and_clamp() {
        assert_eq!(unpack_stick(pack_stick(-32767, 32767)), (-32767, 32767));
        assert_eq!(unpack_stick(pack_stick(-90000, 90000)), (-32767, 32767));
        assert_eq!(stick_from_unit(1.0), 32767);
        assert_eq!(stick_from_unit(-0.5), -16384);
        assert_eq!(stick_from_unit(f64::NAN), 0);
        assert_eq!(stick_from_unit(7.0), 32767);
    }

    #[test]
    fn leases_expire_and_never_zero() {
        let freq = 19_200_000;
        let d = lease_deadline(1000, 100, freq);
        assert_eq!(d, 1000 + 1_920_000);
        assert_eq!(lease_deadline(0, 0, freq), 19_200); // clamped to 1 ms
        assert_eq!(lease_deadline(0, 99_999, freq), 192_000_000); // clamped to 10 s
        let mut deadlines = [0u64; BUTTONS];
        deadlines[0] = d;
        deadlines[9] = 5_000_000;
        assert_eq!(held_mask(&deadlines, 1000), 1 | 512);
        assert_eq!(held_mask(&deadlines, 1000 + 1_920_000), 512);
        assert_eq!(held_mask(&deadlines, 5_000_000), 0);
    }

    #[test]
    fn samples_are_checked() {
        let a = Sample { frame: 1, buttons: 1, sticks: [0; 4] };
        let b = Sample { frame: 1, buttons: 0, sticks: [0; 4] };
        assert_eq!(check_sample(Some(&a), &b, 72), Err("frames_not_ascending"));
        let home = Sample { frame: 2, buttons: 1 << 18, sticks: [0; 4] };
        assert_eq!(check_sample(Some(&a), &home, 72), Err("system_button"));
        let late = Sample { frame: 72, buttons: 0, sticks: [0; 4] };
        assert_eq!(check_sample(Some(&a), &late, 72), Err("frame_past_duration"));
        let wide = Sample { frame: 3, buttons: 1 << 23, sticks: [0; 4] };
        assert_eq!(check_sample(None, &wide, 72), Err("bits_outside_mask"));
        assert_eq!(check_sample(None, &a, 72), Ok(()));
    }

    #[test]
    fn playback_finds_the_sample_in_force() {
        // tap-a: frame 0 nothing, frame 1 A, frame 5 release, 72 frames long.
        let s = [
            Sample { frame: 0, buttons: 0, sticks: [0; 4] },
            Sample { frame: 1, buttons: 1, sticks: [0; 4] },
            Sample { frame: 5, buttons: 0, sticks: [0; 4] },
        ];
        assert_eq!(sample_at(&s, 0, 0), Some(0));
        assert_eq!(sample_at(&s, 0, 1), Some(1));
        assert_eq!(sample_at(&s, 1, 4), Some(1));
        assert_eq!(sample_at(&s, 1, 5), Some(2));
        assert_eq!(sample_at(&s, 2, 71), Some(2));
        // A stale hint past the frame restarts from the front.
        assert_eq!(sample_at(&s, 2, 2), Some(1));
        let late = [Sample { frame: 3, buttons: 1, sticks: [0; 4] }];
        assert_eq!(sample_at(&late, 0, 2), None);
        assert_eq!(sample_at(&[], 0, 2), None);
    }

    #[test]
    fn injection_or_versus_exclusive() {
        let mut buttons = 0b10u64; // real B held
        let mut l = (100, 0);
        let mut r = (0, 0);
        let live = Injection { buttons: 1, left: None, right: None, exclusive: false };
        live.apply(&mut buttons, &mut l, &mut r);
        assert_eq!(buttons, 0b11);
        assert_eq!(l, (100, 0));

        let mut buttons = 0b10u64;
        let mac = Injection::from_sample(&Sample { frame: 0, buttons: 1, sticks: [0, 32767, 0, 0] });
        mac.apply(&mut buttons, &mut l, &mut r);
        assert_eq!(buttons & 0xffff, 1, "macro replaces the real buttons");
        assert_eq!(l, (0, 32767));
        assert_eq!(buttons & STICK_DIRECTION_BITS, 2 << 16, "StickLUp follows the stick");
        assert!(!mac.is_empty());
        assert!(Injection::default().is_empty());
    }

    #[test]
    fn stick_direction_bits_cover_both_sticks() {
        assert_eq!(stick_direction_bits((-32767, 0), (0, 0)), 1 << 16);
        assert_eq!(stick_direction_bits((0, 0), (32767, -32767)), (4 | 8) << 20);
        assert_eq!(stick_direction_bits((100, -100), (0, 0)), 0);
    }
}
