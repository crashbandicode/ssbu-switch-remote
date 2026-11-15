//! Pure, host-tested logic. `scene` is what the NRO ships; `input` and `protocol` are the
//! in-game input path kept as the documented fallback (DESIGN.md §7), not compiled into the
//! runtime.
pub mod config;
pub mod input;
pub mod protocol;
pub mod scene;
