//! `sd:/ultimate/switch-remote/config.txt`: the opt-in flag file. Without it, or
//! without a usable token, the plugin hooks nothing and opens no socket.
//!
//! ```text
//! token=<16..64 of [A-Za-z0-9_-]>   required
//! port=47130                         1024..65535
//! bind=0.0.0.0                       listen address (Eden: 127.0.0.1)
//! npad=auto                          auto | no1 | handheld
//! peers=private                      private | loopback | <a.b.c.d/len> inside a private range
//! inject=on                          the sysmodule's switch for the virtual controller
//! ```

pub const TOKEN_MIN: usize = 16;
pub const TOKEN_MAX: usize = 64;
pub const DEFAULT_PORT: u16 = 47130;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Npad {
    /// Both No1 (id 0) and Handheld (id 0x20).
    Auto,
    No1,
    Handheld,
}

impl Npad {
    /// Whether the hook injects into the state read for `id`.
    pub fn matches(self, id: u32) -> bool {
        match self {
            Npad::Auto => id == NPAD_NO1 || id == NPAD_HANDHELD,
            Npad::No1 => id == NPAD_NO1,
            Npad::Handheld => id == NPAD_HANDHELD,
        }
    }
}

/// `nn::hid::NpadId::No1` and `NpadId::Handheld`.
pub const NPAD_NO1: u32 = 0;
pub const NPAD_HANDHELD: u32 = 0x20;

/// Which client addresses may connect.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Peers {
    /// 10/8, 172.16/12, 192.168/16, 169.254/16 and 127/8.
    Private,
    Loopback,
    /// One IPv4 network (address, prefix length); always also loopback.
    Net(u32, u8),
}

impl Peers {
    pub fn allows(self, ip: u32) -> bool {
        let in_net = |net: u32, len: u8| {
            let mask = if len == 0 { 0 } else { u32::MAX << (32 - len as u32) };
            ip & mask == net & mask
        };
        let loopback = in_net(0x7f00_0000, 8);
        match self {
            Peers::Loopback => loopback,
            Peers::Net(net, len) => loopback || in_net(net, len),
            Peers::Private => {
                loopback
                    || in_net(0x0a00_0000, 8)
                    || in_net(0xac10_0000, 12)
                    || in_net(0xc0a8_0000, 16)
                    || in_net(0xa9fe_0000, 16)
            }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Config {
    pub token: [u8; TOKEN_MAX],
    pub token_len: usize,
    pub port: u16,
    /// Host-order IPv4.
    pub bind: u32,
    pub npad: Npad,
    pub peers: Peers,
}

impl Config {
    pub fn token(&self) -> &[u8] {
        &self.token[..self.token_len]
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConfigError {
    NoToken,
    BadToken,
    BadPort,
    BadBind,
    BadNpad,
    BadPeers,
    UnknownKey,
}

impl ConfigError {
    pub fn code(self) -> &'static str {
        match self {
            ConfigError::NoToken => "no_token",
            ConfigError::BadToken => "bad_token",
            ConfigError::BadPort => "bad_port",
            ConfigError::BadBind => "bad_bind",
            ConfigError::BadNpad => "bad_npad",
            ConfigError::BadPeers => "bad_peers",
            ConfigError::UnknownKey => "unknown_key",
        }
    }
}

/// `a.b.c.d` as a host-order u32.
pub fn parse_ipv4(s: &[u8]) -> Option<u32> {
    let mut out = 0u32;
    let mut parts = 0;
    for part in s.split(|c| *c == b'.') {
        if part.is_empty() || part.len() > 3 || !part.iter().all(u8::is_ascii_digit) {
            return None;
        }
        let v = part.iter().fold(0u32, |a, c| a * 10 + (c - b'0') as u32);
        if v > 255 {
            return None;
        }
        out = (out << 8) | v;
        parts += 1;
    }
    (parts == 4).then_some(out)
}

fn trim(s: &[u8]) -> &[u8] {
    let start = s.iter().position(|c| !c.is_ascii_whitespace()).unwrap_or(s.len());
    let end = s.iter().rposition(|c| !c.is_ascii_whitespace()).map_or(start, |e| e + 1);
    &s[start..end.max(start)]
}

/// Parses the file's bytes. `#` starts a comment line; blank lines are skipped.
pub fn parse(text: &[u8]) -> Result<Config, ConfigError> {
    let mut cfg = Config {
        token: [0; TOKEN_MAX],
        token_len: 0,
        port: DEFAULT_PORT,
        bind: 0,
        npad: Npad::Auto,
        peers: Peers::Private,
    };
    for raw in text.split(|c| *c == b'\n') {
        let line = trim(raw);
        if line.is_empty() || line[0] == b'#' {
            continue;
        }
        let eq = line.iter().position(|c| *c == b'=').ok_or(ConfigError::UnknownKey)?;
        let (key, value) = (trim(&line[..eq]), trim(&line[eq + 1..]));
        match key {
            b"token" => {
                let ok_char = |c: &u8| c.is_ascii_alphanumeric() || *c == b'_' || *c == b'-';
                if value.len() < TOKEN_MIN || value.len() > TOKEN_MAX || !value.iter().all(ok_char) {
                    return Err(ConfigError::BadToken);
                }
                cfg.token[..value.len()].copy_from_slice(value);
                cfg.token_len = value.len();
            }
            b"port" => {
                let p = super::protocol::parse_u32(value).ok_or(ConfigError::BadPort)?;
                // port+1 is the sysmodule's scene port.
                if !(1024..=65534).contains(&p) {
                    return Err(ConfigError::BadPort);
                }
                cfg.port = p as u16;
            }
            b"bind" => cfg.bind = parse_ipv4(value).ok_or(ConfigError::BadBind)?,
            b"npad" => {
                cfg.npad = match value {
                    b"auto" => Npad::Auto,
                    b"no1" => Npad::No1,
                    b"handheld" => Npad::Handheld,
                    _ => return Err(ConfigError::BadNpad),
                }
            }
            b"peers" => {
                cfg.peers = match value {
                    b"private" => Peers::Private,
                    b"loopback" => Peers::Loopback,
                    _ => {
                        let slash = value.iter().position(|c| *c == b'/').ok_or(ConfigError::BadPeers)?;
                        let net = parse_ipv4(&value[..slash]).ok_or(ConfigError::BadPeers)?;
                        let len = super::protocol::parse_u32(&value[slash + 1..]).ok_or(ConfigError::BadPeers)?;
                        if len > 32 {
                            return Err(ConfigError::BadPeers);
                        }
                        // A network wider than a private range is refused: LAN only.
                        let probe = Peers::Private;
                        if len < 8 || !probe.allows(net) {
                            return Err(ConfigError::BadPeers);
                        }
                        Peers::Net(net, len as u8)
                    }
                }
            }
            // The sysmodule's key (sysmodule/core/core.cpp parse_config); nothing to do in the game.
            b"inject" if value == b"on" || value == b"off" => {}
            _ => return Err(ConfigError::UnknownKey),
        }
    }
    if cfg.token_len == 0 {
        return Err(ConfigError::NoToken);
    }
    Ok(cfg)
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &[u8] = b"abcdefghijklmnop";

    #[test]
    fn config_requires_token() {
        assert_eq!(parse(b""), Err(ConfigError::NoToken));
        assert_eq!(parse(b"# only a comment\nport=5000\n"), Err(ConfigError::NoToken));
        assert_eq!(parse(b"token=short"), Err(ConfigError::BadToken));
        assert_eq!(parse(b"token=abcdefghijklmnop!"), Err(ConfigError::BadToken));
        let c = parse(b"token=abcdefghijklmnop\n").unwrap();
        assert_eq!(c.token(), TOKEN);
        assert_eq!(c.port, DEFAULT_PORT);
        assert_eq!(c.bind, 0);
        assert_eq!(c.npad, Npad::Auto);
        assert_eq!(c.peers, Peers::Private);
    }

    #[test]
    fn config_keys() {
        let c = parse(b"token = abcdefghijklmnop\r\nport=47131\nbind=127.0.0.1\nnpad=handheld\npeers=172.30.254.0/24\n")
            .unwrap();
        assert_eq!(c.port, 47131);
        assert_eq!(c.bind, 0x7f00_0001);
        assert_eq!(c.npad, Npad::Handheld);
        assert_eq!(c.peers, Peers::Net(0xac1efe00, 24));
        assert_eq!(parse(b"token=abcdefghijklmnop\nport=80"), Err(ConfigError::BadPort));
        assert_eq!(parse(b"token=abcdefghijklmnop\nbind=1.2.3"), Err(ConfigError::BadBind));
        assert_eq!(parse(b"token=abcdefghijklmnop\nnpad=p2"), Err(ConfigError::BadNpad));
        assert_eq!(parse(b"token=abcdefghijklmnop\npeers=8.8.8.0/24"), Err(ConfigError::BadPeers));
        assert_eq!(parse(b"token=abcdefghijklmnop\npeers=10.0.0.0/4"), Err(ConfigError::BadPeers));
        assert_eq!(parse(b"token=abcdefghijklmnop\ncolour=red"), Err(ConfigError::UnknownKey));
        assert!(parse(b"token=abcdefghijklmnop\ninject=off").is_ok());
        assert_eq!(parse(b"token=abcdefghijklmnop\ninject=maybe"), Err(ConfigError::UnknownKey));
        assert_eq!(parse(b"token=abcdefghijklmnop\nport=65535"), Err(ConfigError::BadPort));
        assert_eq!(parse(b"token=abcdefghijklmnop\nnonsense"), Err(ConfigError::UnknownKey));
    }

    #[test]
    fn peers_are_private_only() {
        let p = Peers::Private;
        for ip in ["172.30.254.66", "10.1.2.3", "172.16.0.1", "172.31.255.255", "127.0.0.1", "169.254.3.4"] {
            assert!(p.allows(parse_ipv4(ip.as_bytes()).unwrap()), "{ip}");
        }
        for ip in ["8.8.8.8", "172.32.0.1", "192.169.0.1", "100.64.0.1", "0.0.0.0"] {
            assert!(!p.allows(parse_ipv4(ip.as_bytes()).unwrap()), "{ip}");
        }
        let l = Peers::Loopback;
        assert!(l.allows(0x7f00_0001));
        assert!(!l.allows(0xac1efebb));
        let n = Peers::Net(0xac1efe00, 24);
        assert!(n.allows(0xac1efe42));
        assert!(!n.allows(0xc0a8_01bf));
        assert!(n.allows(0x7f00_0001));
    }

    #[test]
    fn npad_targets() {
        assert!(Npad::Auto.matches(0) && Npad::Auto.matches(0x20) && !Npad::Auto.matches(1));
        assert!(Npad::No1.matches(0) && !Npad::No1.matches(0x20));
        assert!(Npad::Handheld.matches(0x20) && !Npad::Handheld.matches(0));
    }

    #[test]
    fn ipv4() {
        assert_eq!(parse_ipv4(b"172.30.254.92"), Some(0xac1efe5c));
        assert_eq!(parse_ipv4(b"256.0.0.1"), None);
        assert_eq!(parse_ipv4(b"1.2.3.4.5"), None);
        assert_eq!(parse_ipv4(b"1..3.4"), None);
    }
}
