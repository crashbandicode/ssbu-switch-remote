//! Switch half: version and site checks, the install line, and the reporter thread that sends
//! the scene to the sysmodule over loopback. No hooks; the thread allocates nothing.

use crate::model::config;
use crate::model::protocol::LineBuf;
use crate::model::scene::{self, MODE_READER, MODE_WORD, SUPPORTED_VERSION};
use core::cell::UnsafeCell;
use core::fmt::Write as _;
use skyline::hooks::{getRegionAddress, Region};
use skyline::nn;
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicU64, Ordering};

const DATA_DIR: &str = "sd:/ultimate/switch-remote";
const OPEN_READ: i32 = 1;
const OPEN_WRITE_APPEND: i32 = 2 | 4;
const REPORT_MS: u64 = 100;
const RETRY_MS: u64 = 1000;
const DELUXE_RETRY_REPORTS: u64 = 50;

/// Counts calls into the heap; nothing after the install line is meant to make one.
struct CountingAllocator;
static HEAP_ALLOCATIONS: AtomicU64 = AtomicU64::new(0);

unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        HEAP_ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        System.alloc(layout)
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        HEAP_ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        System.alloc_zeroed(layout)
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        HEAP_ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        System.realloc(ptr, layout, size)
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }
}

#[global_allocator]
static ALLOCATOR: CountingAllocator = CountingAllocator;

/// Static storage handed to one owner (the reporter thread, or `main` before it starts).
struct Owned<T>(UnsafeCell<T>);
// SAFETY: `main` writes these once before the thread starts; then only the thread touches them.
unsafe impl<T> Sync for Owned<T> {}

const STACK: usize = 0x4000;
#[repr(C, align(0x1000))]
struct Stack([u8; STACK]);
static THREAD: Owned<[u8; 0x200]> = Owned(UnsafeCell::new([0; 0x200]));
static THREAD_STACK: Owned<Stack> = Owned(UnsafeCell::new(Stack([0; STACK])));
static CONFIG: Owned<Option<config::Config>> = Owned(UnsafeCell::new(None));
static GUARDS_OK: core::sync::atomic::AtomicBool = core::sync::atomic::AtomicBool::new(false);

// nn::socket, the calls Skyline's own logger links (DESIGN.md §6); Skyline initializes the library.
mod sock {
    #[repr(C)]
    pub struct SockAddrIn {
        pub len: u8,
        pub family: u8,
        pub port_be: u16,
        pub addr_be: u32,
        pub zero: [u8; 8],
    }
    extern "C" {
        #[link_name = "\u{1}_ZN2nn6socket6SocketEiii"]
        pub fn socket(domain: i32, ty: i32, proto: i32) -> i32;
        #[link_name = "\u{1}_ZN2nn6socket7ConnectEiPKNS0_8SockAddrEj"]
        pub fn connect(fd: i32, addr: *const SockAddrIn, len: u32) -> i32;
        #[link_name = "\u{1}_ZN2nn6socket4SendEiPKvmi"]
        pub fn send(fd: i32, buf: *const u8, len: usize, flags: i32) -> isize;
        #[link_name = "\u{1}_ZN2nn6socket5CloseEi"]
        pub fn close(fd: i32) -> i32;
    }
    pub const AF_INET: i32 = 2;
    pub const SOCK_STREAM: i32 = 1;
}

fn sleep_ms(ms: u64) {
    unsafe { nn::os::SleepThread(nn::TimeSpan { nanoseconds: ms * 1_000_000 }) };
}

fn version_is_supported() -> bool {
    let mut version = nn::oe::DisplayVersion { name: [0; 16] };
    unsafe { nn::oe::GetDisplayVersion(&mut version) };
    let end = version.name.iter().position(|v| *v == 0).unwrap_or(16);
    version.name[..end] == *SUPPORTED_VERSION.as_bytes()
}

fn text_base() -> usize {
    unsafe { getRegionAddress(Region::Text) as usize }
}

/// Whether the 64 bytes at the site are the 13.0.5 ones, read inside `.text`.
fn site_matches(site: &scene::Site) -> bool {
    let base = text_base();
    let end = unsafe { getRegionAddress(Region::Rodata) as usize };
    let address = base + site.offset;
    if address + 64 > end {
        return false;
    }
    let bytes = unsafe { core::slice::from_raw_parts(address as *const u8, 64) };
    bytes == site.guard
}

fn path_bytes(name: &str) -> LineBuf<96> {
    let mut p = LineBuf::<96>::default();
    let _ = write!(p, "{DATA_DIR}/{name}\0");
    p
}

fn append_log(bytes: &[u8]) {
    let path = path_bytes("events.log");
    unsafe {
        let dir = b"sd:/ultimate/switch-remote\0";
        nn::fs::CreateDirectory(dir.as_ptr());
        nn::fs::CreateFile(path.as_bytes().as_ptr(), 0);
        let mut handle = nn::fs::FileHandle { handle: 0 };
        if nn::fs::OpenFile(&mut handle, path.as_bytes().as_ptr(), OPEN_WRITE_APPEND) != 0 {
            return;
        }
        let mut size = 0i64;
        if nn::fs::GetFileSize(&mut size, handle) == 0 {
            let option = nn::fs::WriteOption { flags: 0 };
            nn::fs::WriteFile(handle, size, bytes.as_ptr(), bytes.len() as u64, &option);
            nn::fs::FlushFile(handle);
        }
        nn::fs::CloseFile(handle);
    }
}

/// Reads `config.txt` into a stack buffer and parses it; None when absent or invalid.
fn read_config(report: &mut LineBuf<384>) -> Option<config::Config> {
    let path = path_bytes("config.txt");
    let mut buf = [0u8; 1024];
    let mut read = 0u64;
    unsafe {
        let mut handle = nn::fs::FileHandle { handle: 0 };
        if nn::fs::OpenFile(&mut handle, path.as_bytes().as_ptr(), OPEN_READ) != 0 {
            let _ = write!(report, " config=absent");
            return None;
        }
        let rc = nn::fs::ReadFile1(&mut read, handle, 0, buf.as_mut_ptr(), buf.len() as u64);
        nn::fs::CloseFile(handle);
        if rc != 0 {
            let _ = write!(report, " config=unreadable");
            return None;
        }
    }
    match config::parse(&buf[..read as usize]) {
        Ok(c) => {
            let _ = write!(report, " config=ok port={}", c.port);
            Some(c)
        }
        Err(e) => {
            let _ = write!(report, " config=invalid error={}", e.code());
            None
        }
    }
}

fn mode_word() -> u32 {
    unsafe { ((text_base() + MODE_WORD) as *const u32).read_volatile() }
}

type DeluxeContext = extern "C" fn() -> u32;

fn lookup_deluxe() -> usize {
    let mut address = 0usize;
    let rc = unsafe { nn::ro::LookupSymbol(&mut address, scene::DELUXE_EXPORT.as_ptr()) };
    if rc == 0 {
        address
    } else {
        0
    }
}

fn send_all(fd: i32, bytes: &[u8]) -> bool {
    let mut sent = 0;
    while sent < bytes.len() {
        let n = unsafe { sock::send(fd, bytes[sent..].as_ptr(), bytes.len() - sent, 0) };
        if n <= 0 {
            return false;
        }
        sent += n as usize;
    }
    true
}

fn connect_scene(cfg: &config::Config) -> Option<i32> {
    unsafe {
        let fd = sock::socket(sock::AF_INET, sock::SOCK_STREAM, 0);
        if fd < 0 {
            return None;
        }
        let addr = sock::SockAddrIn {
            len: 16,
            family: sock::AF_INET as u8,
            port_be: scene::scene_port(cfg.port).to_be(),
            addr_be: 0x7f00_0001u32.to_be(),
            zero: [0; 8],
        };
        if sock::connect(fd, &addr, 16) != 0 {
            sock::close(fd);
            return None;
        }
        let mut line = LineBuf::<96>::default();
        line.push(b"AUTH ");
        line.push(cfg.token());
        line.push(b"\n");
        if !send_all(fd, line.as_bytes()) {
            sock::close(fd);
            return None;
        }
        Some(fd)
    }
}

#[cfg(feature = "diag")]
fn diag(args: core::fmt::Arguments) {
    let mut line = LineBuf::<160>::default();
    let tick = unsafe { nn::os::GetSystemTick() as u64 };
    let _ = write!(line, "{tick} ");
    let _ = line.write_fmt(args);
    line.push(b"\n");
    append_log(line.as_bytes());
}

#[cfg(not(feature = "diag"))]
fn diag(_: core::fmt::Arguments) {}

extern "C" fn reporter_main(_: *mut skyline::libc::c_void) {
    // SAFETY: written by `main` before this thread started; read-only from here on.
    let Some(cfg) = (unsafe { (*CONFIG.0.get()).as_ref() }) else { return };
    let guards_ok = GUARDS_OK.load(Ordering::Acquire);
    let mut fd: Option<i32> = None;
    let mut seq = 0u64;
    let mut deluxe = 0usize;
    let mut last_mode = u32::MAX;
    loop {
        if deluxe == 0 && seq % DELUXE_RETRY_REPORTS == 0 {
            deluxe = lookup_deluxe();
        }
        if fd.is_none() {
            fd = connect_scene(cfg);
            diag(format_args!("scene_channel connected={}", fd.is_some()));
            if fd.is_none() {
                sleep_ms(RETRY_MS);
                continue;
            }
        }
        let mode = if guards_ok { mode_word() } else { 0 };
        let bits = (deluxe != 0).then(|| {
            let f: DeluxeContext = unsafe { core::mem::transmute(deluxe) };
            f()
        });
        if mode != last_mode {
            diag(format_args!("mode=0x{mode:x} online={} deluxe={}", scene::mode_is_online(mode), scene::deluxe_field(bits)));
            last_mode = mode;
        }
        seq += 1;
        let mut line = LineBuf::<96>::default();
        scene::scene_line(&mut line, seq, mode, bits, guards_ok);
        if !send_all(fd.unwrap(), line.as_bytes()) {
            unsafe { sock::close(fd.take().unwrap()) };
            diag(format_args!("scene_channel dropped"));
            continue;
        }
        sleep_ms(REPORT_MS);
    }
}

fn start_reporter() -> bool {
    unsafe {
        let thread = THREAD.0.get().cast::<nn::os::ThreadType>();
        let priority = nn::os::GetThreadPriority(nn::os::GetCurrentThread());
        let created = nn::os::CreateThread(
            thread,
            reporter_main,
            core::ptr::null_mut(),
            THREAD_STACK.0.get().cast::<u8>(),
            STACK,
            priority,
        );
        if created != 0 {
            return false;
        }
        nn::os::SetThreadNamePointer(thread, c"switch-remote".as_ptr().cast());
        nn::os::StartThread(thread);
        true
    }
}

fn report_install(report: &mut LineBuf<384>) {
    let _ = write!(report, " heap_allocations={}", HEAP_ALLOCATIONS.load(Ordering::Relaxed));
    skyline::logging::log(core::str::from_utf8(report.as_bytes()).unwrap_or(""));
    report.push(b"\n");
    append_log(report.as_bytes());
}

#[skyline::main(name = "switch-remote")]
pub fn main() {
    let mut report = LineBuf::<384>::default();
    let tick = unsafe { nn::os::GetSystemTick() as u64 };
    let _ = write!(report, "{tick} plugin=switch-remote/{}", env!("CARGO_PKG_VERSION"));
    if !version_is_supported() {
        let _ = write!(report, " result=unsupported_version");
        report_install(&mut report);
        return;
    }
    let guards = site_matches(&MODE_READER);
    GUARDS_OK.store(guards, Ordering::Release);
    let _ = write!(report, " {}={}", MODE_READER.name, if guards { "ok" } else { "mismatch" });
    let Some(cfg) = read_config(&mut report) else {
        let _ = write!(report, " result=disabled");
        report_install(&mut report);
        return;
    };
    unsafe { *CONFIG.0.get() = Some(cfg) };
    let started = start_reporter();
    let _ = write!(report, " result={}", if !started { "thread_failed" } else if guards { "installed" } else { "installed_guards_failed" });
    report_install(&mut report);
}
