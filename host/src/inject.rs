//! Puts the map library (library/slop.lua) into a copy of a Lua map: at the end of its script,
//! after a config line saying where the host's seat is, or just before a final top-level
//! `return` (TypeScript-to-Lua bundles end with one, and Lua allows nothing after it). The
//! library then starts itself right after the map's main. The original map is never touched,
//! so no build of it has to carry test code.

use crate::map::Result;
use std::ffi::CString;
use std::path::Path;
use stormlib::{Archive, OpenArchiveFlags};
use stormlib_sys::*;

/// The library, built into the binary so an injection needs nothing next to it
pub const LIBRARY: &str = include_str!("../../library/slop.lua");
/// An injected block sits between these, and is replaced on re-injection
const MARKER: &str = "--[[wc3-slop-lan]]";
const END_MARKER: &str = "--[[/wc3-slop-lan]]";
const SCRIPTS: &[&str] = &["war3map.lua", "scripts\\war3map.lua"];

/// What the library is told when it starts.
pub struct LibraryConfig {
    pub seat: Option<usize>,
    pub prefix: String,
}

impl LibraryConfig {
    fn lua(&self) -> String {
        let seat = self.seat.map(|s| s.to_string()).unwrap_or_else(|| "nil".into());
        format!("SLOP_CONFIG = {{seat = {}, prefix = {:?}}}\n", seat, self.prefix)
    }
}

/// Copies `map` to `out` with the library in its script. Refuses JASS maps: the library is Lua.
pub fn inject(map: &Path, out: &Path, config: &LibraryConfig) -> Result<()> {
    let (name, script) = {
        let mut archive = Archive::open(map, OpenArchiveFlags::STREAM_FLAG_READ_ONLY)?;
        let found = SCRIPTS.iter().find_map(|name| {
            archive.open_file(name).ok().and_then(|mut f| f.read_all().ok()).map(|bytes| (*name, bytes))
        });
        match found {
            Some(found) => found,
            None if archive.has_file("war3map.j").unwrap_or(false) || archive.has_file("scripts\\war3map.j").unwrap_or(false) => {
                return Err("this is a JASS map; the library needs a Lua map (the host still works without it)".into())
            }
            None => return Err("the map has no script".into()),
        }
    };
    let script = String::from_utf8(script).map_err(|_| "the map's script is not UTF-8")?;
    let script = with_library(&without_library(&script), config);

    if map != out {
        std::fs::copy(map, out)?;
    }
    replace_file(out, name, script.as_bytes())
}

/// The script with any injected block taken out.
fn without_library(script: &str) -> String {
    match (script.find(MARKER), script.find(END_MARKER)) {
        (Some(start), Some(end)) if end > start => {
            let after = &script[end + END_MARKER.len()..];
            format!("{}{}", &script[..start], after.strip_prefix('\n').unwrap_or(after))
        }
        _ => script.to_string(),
    }
}

/// The script with the block in: before a final top-level `return` line, else at the end.
fn with_library(script: &str, config: &LibraryConfig) -> String {
    let block = format!("{}\n{}{}\n{}\n", MARKER, config.lua(), LIBRARY.trim_end(), END_MARKER);
    let body = script.trim_end();
    let last_line = body.rfind('\n').map(|at| at + 1).unwrap_or(0);
    if body[last_line..].starts_with("return") {
        format!("{}{}{}\n", &body[..last_line], block, &body[last_line..])
    } else {
        format!("{}\n{}", body, block)
    }
}

#[cfg(not(windows))]
fn replace_file(archive: &Path, name: &str, data: &[u8]) -> Result<()> {
    let path = CString::new(archive.to_str().ok_or("the map path is not UTF-8")?)?;
    let name = CString::new(name)?;
    unsafe {
        let mut mpq: HANDLE = std::ptr::null_mut();
        if !SFileOpenArchive(path.as_ptr(), 0, 0, &mut mpq) {
            return Err(format!("cannot open {} for writing", archive.display()).into());
        }
        let mut file: HANDLE = std::ptr::null_mut();
        let written = SFileCreateFile(mpq, name.as_ptr(), 0, data.len() as u32, 0,
                                      MPQ_FILE_COMPRESS | MPQ_FILE_REPLACEEXISTING, &mut file)
            && SFileWriteFile(file, data.as_ptr() as *const _, data.len() as u32, MPQ_COMPRESSION_ZLIB)
            && SFileFinishFile(file);
        let closed = SFileCloseArchive(mpq);
        if !(written && closed) {
            return Err(format!("writing the script into {} failed", archive.display()).into());
        }
    }
    Ok(())
}

#[cfg(windows)]
fn replace_file(_archive: &Path, _name: &str, _data: &[u8]) -> Result<()> {
    Err("injecting is not supported on Windows yet".into())
}

/// Whether a map already carries the library.
pub fn is_injected(map: &Path) -> bool {
    let Ok(mut archive) = Archive::open(map, OpenArchiveFlags::STREAM_FLAG_READ_ONLY) else { return false };
    SCRIPTS.iter().any(|name| {
        archive.open_file(name).ok().and_then(|mut f| f.read_all().ok())
            .map(|bytes| String::from_utf8_lossy(&bytes).contains(MARKER)).unwrap_or(false)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs a Lua map: SLOP_TEST_MAP=<file.w3x> cargo test. Skipped without one.
    #[test]
    fn injects_and_reinjects() {
        let Ok(map) = std::env::var("SLOP_TEST_MAP") else { return };
        let out = std::env::temp_dir().join("slop-inject-test.w3x");
        let config = LibraryConfig { seat: Some(17), prefix: "slop".into() };
        inject(Path::new(&map), &out, &config).unwrap();
        inject(&out.clone(), &out, &config).unwrap();
        let mut archive = Archive::open(&out, OpenArchiveFlags::STREAM_FLAG_READ_ONLY).unwrap();
        let name = SCRIPTS.iter().find(|n| archive.has_file(n).unwrap_or(false)).unwrap();
        let script = String::from_utf8(archive.open_file(name).unwrap().read_all().unwrap()).unwrap();
        assert_eq!(script.matches(MARKER).count(), 1, "re-injecting replaces the block");
        assert!(script.contains(LIBRARY.trim_end()));
        assert!(script.contains("SLOP_CONFIG = {seat = 17, prefix = \"slop\"}"));
        std::fs::write(std::env::temp_dir().join("slop-inject-test.lua"), &script).unwrap();
        std::fs::remove_file(&out).ok();
    }

    #[test]
    fn goes_before_a_final_return() {
        let config = LibraryConfig { seat: None, prefix: "slop".into() };
        let bundled = with_library("local x = 1\nreturn require(\"main\", ...)\n\n", &config);
        assert!(bundled.trim_end().ends_with("return require(\"main\", ...)"));
        assert!(bundled.find(MARKER).unwrap() < bundled.find("return require").unwrap());
        let plain = with_library("function main() end\n", &config);
        assert!(plain.trim_end().ends_with(END_MARKER));
        assert_eq!(without_library(&bundled), "local x = 1\nreturn require(\"main\", ...)\n");
        assert_eq!(without_library(&plain), "function main() end\n");
    }
}
