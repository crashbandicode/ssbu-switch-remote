//! switch-remote, in-game half: reads Smash Ultimate 13.0.5's mode word and Online Deluxe's
//! ONLINE bit and reports the scene to the switch-remote sysmodule over loopback, ten times a
//! second. The sysmodule refuses remote input unless a fresh report says offline (DESIGN.md §2).
//! No hooks: one reader thread on a static stack.
pub mod model;
#[cfg(target_os = "switch")]
mod runtime;
