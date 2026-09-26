//! The Windows calls: finding running games, reading and patching one, suspending its threads.
//! The same calls work under Wine, so one build serves both.

use crate::patterns::{best_address, find_all, menus_addresses, name, pattern, selectors, FACTORY, KNOWN_BUILDS};
use std::ffi::c_void;
use std::mem::{size_of, zeroed};
use std::ptr::null_mut;
use std::time::Duration;
use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE, INVALID_HANDLE_VALUE, STILL_ACTIVE};
use windows_sys::Win32::Storage::FileSystem::{GetFileVersionInfoSizeW, GetFileVersionInfoW, VerQueryValueW, VS_FIXEDFILEINFO};
use windows_sys::Win32::System::Diagnostics::Debug::{
    FlushInstructionCache, GetThreadContext, ReadProcessMemory, WriteProcessMemory, CONTEXT, CONTEXT_CONTROL_AMD64,
};
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Module32FirstW, Module32NextW, Process32FirstW, Process32NextW, Thread32First, Thread32Next,
    MODULEENTRY32W, PROCESSENTRY32W, TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32, TH32CS_SNAPPROCESS, TH32CS_SNAPTHREAD,
    THREADENTRY32,
};
use windows_sys::Win32::System::Memory::{
    VirtualProtectEx, VirtualQueryEx, MEMORY_BASIC_INFORMATION, MEM_COMMIT, MEM_PRIVATE, PAGE_EXECUTE_READ,
    PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY, PAGE_GUARD, PAGE_READONLY, PAGE_READWRITE,
};
use windows_sys::Win32::System::Threading::{
    GetExitCodeProcess, OpenProcess, OpenThread, QueryFullProcessImageNameW, ResumeThread, SuspendThread,
    PROCESS_QUERY_INFORMATION, PROCESS_SUSPEND_RESUME, PROCESS_VM_OPERATION, PROCESS_VM_READ, PROCESS_VM_WRITE,
    THREAD_GET_CONTEXT, THREAD_SUSPEND_RESUME,
};

const GAME: &str = "warcraft iii.exe";

pub type Result<T> = std::result::Result<T, String>;

fn last_error(what: &str) -> String {
    format!("{what} failed (error {})", unsafe { GetLastError() })
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(Some(0)).collect()
}

fn from_wide(text: &[u16]) -> String {
    String::from_utf16_lossy(&text[..text.iter().position(|&c| c == 0).unwrap_or(text.len())])
}

/// A handle that is closed when dropped.
struct Handle(HANDLE);

impl Drop for Handle {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.0) };
    }
}

/// The pids of every running Warcraft III.exe.
pub fn running_games() -> Vec<u32> {
    let snapshot = Handle(unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) });
    if snapshot.0 == INVALID_HANDLE_VALUE {
        return vec![];
    }
    let mut entry: PROCESSENTRY32W = unsafe { zeroed() };
    entry.dwSize = size_of::<PROCESSENTRY32W>() as u32;
    let mut found = vec![];
    let mut more = unsafe { Process32FirstW(snapshot.0, &mut entry) } != 0;
    while more {
        if from_wide(&entry.szExeFile).to_lowercase() == GAME {
            found.push(entry.th32ProcessID);
        }
        more = unsafe { Process32NextW(snapshot.0, &mut entry) } != 0;
    }
    found
}

/// A running game, opened for reading and patching.
pub struct Game {
    pub pid: u32,
    pub version: String,
    process: Handle,
    text: (usize, usize),
}

impl Game {
    pub fn open(pid: u32, any_build: bool) -> Result<Game> {
        let process = Handle(unsafe {
            OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_SUSPEND_RESUME,
                0,
                pid,
            )
        });
        if process.0.is_null() {
            return Err(last_error(&format!("opening game {pid}")));
        }
        let path = image_path(&process)?;
        let version = file_version(&path).unwrap_or_else(|| "unknown".into());
        if !any_build && !KNOWN_BUILDS.contains(&version.as_str()) {
            return Err(format!(
                "game {pid} is version {version}, not a build this was checked on ({}); --any-build to try anyway",
                KNOWN_BUILDS.join(", ")
            ));
        }
        let (base, size) = game_module(pid)?;
        let mut game = Game { pid, version, process, text: (0, 0) };
        game.text = game.code_section(base, size)?;
        Ok(game)
    }

    /// Whether the game still runs. A process only just started may not give an exit code yet
    /// (under Wine it can fail then): the process list decides.
    pub fn alive(&self) -> bool {
        let mut code = 0u32;
        if (unsafe { GetExitCodeProcess(self.process.0, &mut code) }) != 0 {
            return code == STILL_ACTIVE as u32;
        }
        running_games().contains(&self.pid)
    }

    /// The provider selector (its operand's address, and what it selects now), once the game has
    /// decrypted it. Refuses when the build's factory does not take TCPN, or the pattern is not
    /// unique.
    pub fn selector(&self) -> Result<Option<(usize, [u8; 4])>> {
        let runs = self.readable_runs(self.text.0, self.text.1, false);
        let factory = pattern(FACTORY);
        let mut found = vec![];
        for (start, bytes) in &runs {
            found.extend(selectors(bytes).into_iter().map(|(at, operand)| (start + at, operand)));
        }
        match found.as_slice() {
            [] => Ok(None),
            [site] if runs.iter().any(|(_, bytes)| !find_all(bytes, &factory).is_empty()) => Ok(Some(*site)),
            // The selector has run, so the factory it calls has too: if its pattern is not there,
            // this build's factory is not one that makes TCPN providers
            [_] => Err("this build's provider factory does not look like one that makes TCPN providers; refusing".into()),
            sites => Err(format!("the selector pattern matched {} places; refusing to guess", sites.len())),
        }
    }

    /// The menus' port and guid, as the game wrote them into its own memory.
    pub fn menus_address(&self) -> Option<(u16, String)> {
        let mut found = vec![];
        for (_, bytes) in self.readable_runs(0x10000, 0x7FFF_FFFF_0000, true) {
            found.extend(menus_addresses(&bytes));
        }
        best_address(found)
    }

    fn read(&self, at: usize, length: usize) -> Option<Vec<u8>> {
        let mut bytes = vec![0u8; length];
        let mut done = 0usize;
        let ok = unsafe { ReadProcessMemory(self.process.0, at as *const c_void, bytes.as_mut_ptr().cast(), length, &mut done) };
        (ok != 0 && done == length).then_some(bytes)
    }

    /// The loaded image's .text section, from its PE headers in memory.
    fn code_section(&self, base: usize, size: usize) -> Result<(usize, usize)> {
        let headers = self.read(base, 0x1000).ok_or("could not read the game's PE headers")?;
        let u16_at = |o: usize| u16::from_le_bytes([headers[o], headers[o + 1]]) as usize;
        let u32_at = |o: usize| u32::from_le_bytes(headers[o..o + 4].try_into().unwrap()) as usize;
        let pe = u32_at(0x3c);
        let first = pe + 24 + u16_at(pe + 20);
        for i in 0..u16_at(pe + 6) {
            let at = first + i * 40;
            if &headers[at..at + 5] == b".text" {
                let (vsize, va) = (u32_at(at + 8), u32_at(at + 12));
                if va + vsize > size {
                    return Err("the .text section runs past the image".into());
                }
                return Ok((base + va, base + va + vsize));
            }
        }
        Err("the game has no .text section".into())
    }

    /// The readable stretches of [start, end), joined where they touch so a pattern across a
    /// region boundary is still found. For the code: the pages the game has decrypted so far.
    /// `heap_only` keeps to private memory in pieces of at most 256 MB, for the menus' address.
    fn readable_runs(&self, start: usize, end: usize, heap_only: bool) -> Vec<(usize, Vec<u8>)> {
        let readable = PAGE_READONLY | PAGE_READWRITE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
        let mut spans: Vec<(usize, usize)> = vec![];
        let mut at = start;
        while at < end {
            let mut info: MEMORY_BASIC_INFORMATION = unsafe { zeroed() };
            if unsafe { VirtualQueryEx(self.process.0, at as *const c_void, &mut info, size_of::<MEMORY_BASIC_INFORMATION>()) } == 0 {
                break;
            }
            let region_end = (info.BaseAddress as usize).saturating_add(info.RegionSize).min(end);
            let wanted = info.State == MEM_COMMIT
                && info.Protect & readable != 0
                && info.Protect & PAGE_GUARD == 0
                && (!heap_only || (info.Type == MEM_PRIVATE && info.RegionSize <= 256 << 20));
            if wanted {
                match spans.last_mut() {
                    Some(last) if last.1 == at && !heap_only => last.1 = region_end,
                    _ => spans.push((at, region_end)),
                }
            }
            at = region_end.max(at + 0x1000);
        }
        spans.into_iter().filter_map(|(s, e)| self.read(s, e - s).map(|bytes| (s, bytes))).collect()
    }

    /// Rewrites the selector's operand from one provider to another, with the game's threads
    /// suspended and none of them on that instruction; reads it back.
    pub fn patch(&self, operand: usize, from: [u8; 4], to: [u8; 4]) -> Result<()> {
        let instruction = operand - 1;
        for _ in 0..20 {
            let suspended = suspend_threads(self.pid)?;
            if suspended.inside(instruction)? {
                drop(suspended);
                std::thread::sleep(Duration::from_millis(50));
                continue;
            }
            if self.read(instruction, 5) != Some([&[0xB9u8][..], &from[..]].concat()) {
                return Err(format!("the instruction at {instruction:#x} is not mov ecx,'{}'", name(from)));
            }
            let mut old = 0u32;
            if unsafe { VirtualProtectEx(self.process.0, operand as *const c_void, 4, PAGE_EXECUTE_READWRITE, &mut old) } == 0 {
                return Err(last_error("making the selector's page writable"));
            }
            let mut written = 0usize;
            let wrote = unsafe { WriteProcessMemory(self.process.0, operand as *const c_void, to.as_ptr().cast(), 4, &mut written) };
            let error = (wrote == 0 || written != 4).then(|| last_error("writing the provider"));
            let mut ignored = 0u32;
            unsafe { VirtualProtectEx(self.process.0, operand as *const c_void, 4, old, &mut ignored) };
            unsafe { FlushInstructionCache(self.process.0, instruction as *const c_void, 5) };
            if let Some(error) = error {
                return Err(error);
            }
            if self.read(operand, 4) != Some(to.to_vec()) {
                return Err("reading the selector back does not show the new provider".into());
            }
            return Ok(());
        }
        Err("a game thread kept sitting on the selector instruction".into())
    }
}

fn image_path(process: &Handle) -> Result<String> {
    let mut buffer = [0u16; 1024];
    let mut length = buffer.len() as u32;
    if unsafe { QueryFullProcessImageNameW(process.0, 0, buffer.as_mut_ptr(), &mut length) } == 0 {
        return Err(last_error("reading the game's path"));
    }
    Ok(from_wide(&buffer[..length as usize]))
}

fn file_version(path: &str) -> Option<String> {
    let path = wide(path);
    let size = unsafe { GetFileVersionInfoSizeW(path.as_ptr(), null_mut()) };
    if size == 0 {
        return None;
    }
    let mut data = vec![0u8; size as usize];
    if unsafe { GetFileVersionInfoW(path.as_ptr(), 0, size, data.as_mut_ptr().cast()) } == 0 {
        return None;
    }
    let mut info: *mut c_void = null_mut();
    let mut length = 0u32;
    if unsafe { VerQueryValueW(data.as_ptr().cast(), wide("\\").as_ptr(), &mut info, &mut length) } == 0 || info.is_null() {
        return None;
    }
    let info = unsafe { &*(info as *const VS_FIXEDFILEINFO) };
    Some(format!(
        "{}.{}.{}.{}",
        info.dwFileVersionMS >> 16,
        info.dwFileVersionMS & 0xffff,
        info.dwFileVersionLS >> 16,
        info.dwFileVersionLS & 0xffff
    ))
}

/// Where the game's exe is loaded, and how big it is.
fn game_module(pid: u32) -> Result<(usize, usize)> {
    let snapshot = Handle(unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid) });
    if snapshot.0 == INVALID_HANDLE_VALUE {
        return Err(last_error("listing the game's modules"));
    }
    let mut entry: MODULEENTRY32W = unsafe { zeroed() };
    entry.dwSize = size_of::<MODULEENTRY32W>() as u32;
    let mut more = unsafe { Module32FirstW(snapshot.0, &mut entry) } != 0;
    while more {
        if from_wide(&entry.szModule).to_lowercase() == GAME {
            return Ok((entry.modBaseAddr as usize, entry.modBaseSize as usize));
        }
        more = unsafe { Module32NextW(snapshot.0, &mut entry) } != 0;
    }
    Err("the game's own module is not in its module list".into())
}

/// A game's threads, suspended until this is dropped.
struct Suspended(Vec<Handle>);

impl Drop for Suspended {
    fn drop(&mut self) {
        for thread in &self.0 {
            unsafe { ResumeThread(thread.0) };
        }
    }
}

impl Suspended {
    /// Whether any thread is stopped part-way into the 5-byte instruction.
    fn inside(&self, instruction: usize) -> Result<bool> {
        for thread in &self.0 {
            let mut context: CONTEXT = unsafe { zeroed() };
            context.ContextFlags = CONTEXT_CONTROL_AMD64;
            if unsafe { GetThreadContext(thread.0, &mut context) } == 0 {
                return Err(last_error("reading a suspended thread's context"));
            }
            let rip = context.Rip as usize;
            if rip > instruction && rip < instruction + 5 {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

fn suspend_threads(pid: u32) -> Result<Suspended> {
    let snapshot = Handle(unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) });
    if snapshot.0 == INVALID_HANDLE_VALUE {
        return Err(last_error("listing threads"));
    }
    let mut entry: THREADENTRY32 = unsafe { zeroed() };
    entry.dwSize = size_of::<THREADENTRY32>() as u32;
    let mut suspended = Suspended(vec![]);
    let mut more = unsafe { Thread32First(snapshot.0, &mut entry) } != 0;
    while more {
        if entry.th32OwnerProcessID == pid {
            let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT, 0, entry.th32ThreadID) };
            if !thread.is_null() {
                let thread = Handle(thread);
                if unsafe { SuspendThread(thread.0) } != u32::MAX {
                    suspended.0.push(thread);
                }
            }
        }
        more = unsafe { Thread32Next(snapshot.0, &mut entry) } != 0;
    }
    if suspended.0.is_empty() {
        return Err("could not suspend any of the game's threads".into());
    }
    Ok(suspended)
}
