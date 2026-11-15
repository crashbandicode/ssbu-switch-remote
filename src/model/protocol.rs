//! The wire protocol between the host client and the plugin: one ASCII request line,
//! one reply line, and for `SHOT`/`FILE` a binary payload after the reply line.
//! Parsing and formatting work on borrowed bytes and fixed buffers (no heap), so the
//! network thread runs on static storage. `docs/PROTOCOL.md` is the contract.

use super::input::{self, Sample};

/// First line the client sends, and the plugin's greeting tag.
pub const VERSION: &str = "SR1";
/// Longest request line, LF included.
pub const LINE_MAX: usize = 256;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stick {
    Left,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ButtonState {
    Press,
    Release,
    /// Press, released after `ms` (Eden's tap; its default is 100 ms).
    Tap,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Request<'a> {
    /// `AUTH <token>`: must be the first request of a connection.
    Auth(&'a [u8]),
    Ping,
    Status,
    /// `BTN <bit> press|release|tap <ms>`: one button, its own lease (Eden's per-input lease).
    Button { bit: u32, state: ButtonState, ms: u32 },
    /// `STICK l|r <x> <y> <lease_ms>`: console units, -32767..32767.
    Stick { stick: Stick, x: i32, y: i32, ms: u32 },
    /// `RESET`: release every injected input and stop a macro.
    Reset,
    /// `MBEGIN <samples> <duration_frames>`: start loading a macro.
    MacroBegin { samples: u32, duration: u32 },
    /// `MEV <frame> <buttons> <lx> <ly> <rx> <ry>`: one macro sample.
    MacroEvent(Sample),
    /// `MPLAY`: play the loaded macro from the next presented frame.
    MacroPlay,
    MacroStatus,
    MacroStop,
    /// `SHOT`: the screen as the system captures it (JPEG, 1280x720).
    Shot,
    /// `FILE <offset> <max> <path>`: read part of a file under `sd:/ultimate/`.
    File { offset: u64, max: u32, path: &'a [u8] },
    Bye,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ParseError {
    Empty,
    TooLong,
    Unknown,
    Arguments,
    Range,
    Path,
}

impl ParseError {
    pub fn code(self) -> &'static str {
        match self {
            ParseError::Empty => "empty",
            ParseError::TooLong => "too_long",
            ParseError::Unknown => "unknown_method",
            ParseError::Arguments => "bad_arguments",
            ParseError::Range => "out_of_range",
            ParseError::Path => "bad_path",
        }
    }
}

/// Splits on single spaces; at most 8 fields.
fn fields(line: &[u8]) -> ([&[u8]; 8], usize) {
    let mut out: [&[u8]; 8] = [&[]; 8];
    let mut n = 0;
    for part in line.split(|b| *b == b' ').filter(|p| !p.is_empty()) {
        if n == out.len() {
            return (out, n + 1);
        }
        out[n] = part;
        n += 1;
    }
    (out, n)
}

pub fn parse_u64(s: &[u8]) -> Option<u64> {
    if s.is_empty() || s.len() > 20 {
        return None;
    }
    let mut v: u64 = 0;
    for &c in s {
        if !c.is_ascii_digit() {
            return None;
        }
        v = v.checked_mul(10)?.checked_add((c - b'0') as u64)?;
    }
    Some(v)
}

pub fn parse_u32(s: &[u8]) -> Option<u32> {
    parse_u64(s).and_then(|v| u32::try_from(v).ok())
}

pub fn parse_i32(s: &[u8]) -> Option<i32> {
    match s.split_first() {
        Some((b'-', rest)) => parse_u64(rest).and_then(|v| i32::try_from(-(v as i64)).ok()),
        _ => parse_u64(s).and_then(|v| i32::try_from(v).ok()),
    }
}

fn stick_value(s: &[u8]) -> Result<i32, ParseError> {
    let v = parse_i32(s).ok_or(ParseError::Arguments)?;
    if v.abs() > input::STICK_MAX {
        return Err(ParseError::Range);
    }
    Ok(v)
}

fn lease(s: &[u8]) -> Result<u32, ParseError> {
    let v = parse_u32(s).ok_or(ParseError::Arguments)?;
    if v == 0 || v > input::LEASE_MAX_MS {
        return Err(ParseError::Range);
    }
    Ok(v)
}

/// A path under `sd:/ultimate/`: printable ASCII, no `..`, no backslash, no leading `/`.
pub fn path_ok(path: &[u8]) -> bool {
    !path.is_empty()
        && path.len() <= 160
        && path[0] != b'/'
        && path.iter().all(|c| c.is_ascii_graphic() && *c != b'\\')
        && !path.split(|c| *c == b'/').any(|part| part == b".." || part == b"." || part.is_empty())
}

/// Parses one request line (without its LF).
pub fn parse(line: &[u8]) -> Result<Request<'_>, ParseError> {
    let line = line.strip_suffix(b"\r").unwrap_or(line);
    if line.len() >= LINE_MAX {
        return Err(ParseError::TooLong);
    }
    let (f, n) = fields(line);
    if n == 0 {
        return Err(ParseError::Empty);
    }
    let want = |count: usize| if n == count { Ok(()) } else { Err(ParseError::Arguments) };
    Ok(match f[0] {
        b"AUTH" => {
            want(2)?;
            Request::Auth(f[1])
        }
        b"PING" => {
            want(1)?;
            Request::Ping
        }
        b"STATUS" => {
            want(1)?;
            Request::Status
        }
        b"BTN" => {
            want(4)?;
            let bit = parse_u32(f[1]).ok_or(ParseError::Arguments)?;
            if bit >= input::BUTTONS as u32 {
                return Err(ParseError::Range);
            }
            let state = match f[2] {
                b"press" => ButtonState::Press,
                b"release" => ButtonState::Release,
                b"tap" => ButtonState::Tap,
                _ => return Err(ParseError::Arguments),
            };
            Request::Button { bit, state, ms: lease(f[3])? }
        }
        b"STICK" => {
            want(5)?;
            let stick = match f[1] {
                b"l" => Stick::Left,
                b"r" => Stick::Right,
                _ => return Err(ParseError::Arguments),
            };
            Request::Stick { stick, x: stick_value(f[2])?, y: stick_value(f[3])?, ms: lease(f[4])? }
        }
        b"RESET" => {
            want(1)?;
            Request::Reset
        }
        b"MBEGIN" => {
            want(3)?;
            let samples = parse_u32(f[1]).ok_or(ParseError::Arguments)?;
            let duration = parse_u32(f[2]).ok_or(ParseError::Arguments)?;
            if samples == 0 || samples as usize > input::MACRO_SAMPLES || duration == 0 || duration > 216_000 {
                return Err(ParseError::Range);
            }
            Request::MacroBegin { samples, duration }
        }
        b"MEV" => {
            want(7)?;
            let frame = parse_u32(f[1]).ok_or(ParseError::Arguments)?;
            let buttons = parse_u32(f[2]).ok_or(ParseError::Arguments)?;
            let mut sticks = [0i16; 4];
            for (i, s) in sticks.iter_mut().enumerate() {
                *s = stick_value(f[3 + i])? as i16;
            }
            Request::MacroEvent(Sample { frame, buttons, sticks })
        }
        b"MPLAY" => {
            want(1)?;
            Request::MacroPlay
        }
        b"MSTATUS" => {
            want(1)?;
            Request::MacroStatus
        }
        b"MSTOP" => {
            want(1)?;
            Request::MacroStop
        }
        b"SHOT" => {
            want(1)?;
            Request::Shot
        }
        b"FILE" => {
            want(4)?;
            let offset = parse_u64(f[1]).ok_or(ParseError::Arguments)?;
            let max = parse_u32(f[2]).ok_or(ParseError::Arguments)?;
            if max == 0 || max > FILE_CHUNK_MAX {
                return Err(ParseError::Range);
            }
            if !path_ok(f[3]) {
                return Err(ParseError::Path);
            }
            Request::File { offset, max, path: f[3] }
        }
        b"BYE" => {
            want(1)?;
            Request::Bye
        }
        _ => return Err(ParseError::Unknown),
    })
}

/// Largest `FILE` chunk.
pub const FILE_CHUNK_MAX: u32 = 64 * 1024;

/// Compares a presented token with the configured one without an early exit.
pub fn token_eq(given: &[u8], expected: &[u8]) -> bool {
    if expected.is_empty() {
        return false;
    }
    let mut diff = (given.len() != expected.len()) as u8;
    for (i, e) in expected.iter().enumerate() {
        diff |= given.get(i).copied().unwrap_or(0) ^ e;
    }
    diff == 0
}

/// A fixed-capacity line writer (`core::fmt::Write`), truncating instead of failing.
pub struct LineBuf<const N: usize> {
    pub buf: [u8; N],
    pub len: usize,
}

impl<const N: usize> Default for LineBuf<N> {
    fn default() -> Self {
        Self { buf: [0; N], len: 0 }
    }
}

impl<const N: usize> LineBuf<N> {
    pub fn clear(&mut self) {
        self.len = 0;
    }
    pub fn as_bytes(&self) -> &[u8] {
        &self.buf[..self.len]
    }
    pub fn push(&mut self, bytes: &[u8]) {
        let room = N - self.len;
        let n = bytes.len().min(room);
        self.buf[self.len..self.len + n].copy_from_slice(&bytes[..n]);
        self.len += n;
    }
}

impl<const N: usize> core::fmt::Write for LineBuf<N> {
    fn write_str(&mut self, s: &str) -> core::fmt::Result {
        self.push(s.as_bytes());
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use core::fmt::Write as _;

    #[test]
    fn parses_every_method() {
        assert_eq!(parse(b"AUTH abc"), Ok(Request::Auth(b"abc")));
        assert_eq!(parse(b"PING\r"), Ok(Request::Ping));
        assert_eq!(parse(b"STATUS"), Ok(Request::Status));
        assert_eq!(
            parse(b"BTN 0 tap 100"),
            Ok(Request::Button { bit: 0, state: ButtonState::Tap, ms: 100 })
        );
        assert_eq!(
            parse(b"STICK l -32767 32767 250"),
            Ok(Request::Stick { stick: Stick::Left, x: -32767, y: 32767, ms: 250 })
        );
        assert_eq!(parse(b"RESET"), Ok(Request::Reset));
        assert_eq!(parse(b"MBEGIN 3 72"), Ok(Request::MacroBegin { samples: 3, duration: 72 }));
        assert_eq!(
            parse(b"MEV 1 1 0 0 0 -5"),
            Ok(Request::MacroEvent(Sample { frame: 1, buttons: 1, sticks: [0, 0, 0, -5] }))
        );
        assert_eq!(parse(b"MPLAY"), Ok(Request::MacroPlay));
        assert_eq!(parse(b"MSTATUS"), Ok(Request::MacroStatus));
        assert_eq!(parse(b"MSTOP"), Ok(Request::MacroStop));
        assert_eq!(parse(b"SHOT"), Ok(Request::Shot));
        assert_eq!(
            parse(b"FILE 0 4096 training_rooms/events.log"),
            Ok(Request::File { offset: 0, max: 4096, path: b"training_rooms/events.log" })
        );
        assert_eq!(parse(b"BYE"), Ok(Request::Bye));
    }

    #[test]
    fn refuses_bad_requests() {
        assert_eq!(parse(b""), Err(ParseError::Empty));
        assert_eq!(parse(b"   "), Err(ParseError::Empty));
        assert_eq!(parse(b"FOO"), Err(ParseError::Unknown));
        assert_eq!(parse(b"PING x"), Err(ParseError::Arguments));
        assert_eq!(parse(b"BTN 16 tap 100"), Err(ParseError::Range));
        assert_eq!(parse(b"BTN 0 hold 100"), Err(ParseError::Arguments));
        assert_eq!(parse(b"BTN 0 tap 0"), Err(ParseError::Range));
        assert_eq!(parse(b"BTN 0 tap 10001"), Err(ParseError::Range));
        assert_eq!(parse(b"STICK l 40000 0 100"), Err(ParseError::Range));
        assert_eq!(parse(b"STICK x 0 0 100"), Err(ParseError::Arguments));
        assert_eq!(parse(b"MBEGIN 0 72"), Err(ParseError::Range));
        assert_eq!(parse(b"MBEGIN 4097 72"), Err(ParseError::Range));
        assert_eq!(parse(b"SHOT 640"), Err(ParseError::Arguments));
        assert_eq!(parse(b"FILE 0 4096 ../x"), Err(ParseError::Path));
        assert_eq!(parse(b"FILE 0 4096 /abs"), Err(ParseError::Path));
        assert_eq!(parse(b"FILE 0 4096 a//b"), Err(ParseError::Path));
        assert_eq!(parse(b"FILE 0 0 a"), Err(ParseError::Range));
        assert_eq!(parse(b"FILE 0 65537 a"), Err(ParseError::Range));
        assert_eq!(parse(b"BTN 1 2 3 4 5 6 7 8 9"), Err(ParseError::Arguments));
        let long = [b'A'; LINE_MAX];
        assert_eq!(parse(&long), Err(ParseError::TooLong));
        assert_eq!(parse(b"MEV 1 1 0 0 0"), Err(ParseError::Arguments));
    }

    #[test]
    fn numbers() {
        assert_eq!(parse_i32(b"-32767"), Some(-32767));
        assert_eq!(parse_i32(b"-"), None);
        assert_eq!(parse_i32(b"+1"), None);
        assert_eq!(parse_u32(b"4294967296"), None);
        assert_eq!(parse_u64(b"18446744073709551616"), None);
        assert_eq!(parse_u64(b"007"), Some(7));
    }

    #[test]
    fn tokens_compare_whole() {
        assert!(token_eq(b"abcdef", b"abcdef"));
        assert!(!token_eq(b"abcde", b"abcdef"));
        assert!(!token_eq(b"abcdefg", b"abcdef"));
        assert!(!token_eq(b"abcdeF", b"abcdef"));
        assert!(!token_eq(b"", b""), "an empty configured token never authenticates");
    }

    #[test]
    fn line_buffer_truncates() {
        let mut b = LineBuf::<8>::default();
        write!(b, "OK {}", 123456789).unwrap();
        assert_eq!(b.as_bytes(), b"OK 12345");
        b.clear();
        b.push(b"hi");
        assert_eq!(b.as_bytes(), b"hi");
    }
}
