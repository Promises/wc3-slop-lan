//! slop-activator: keeps every Warcraft III on this machine - the Windows build, on Windows or
//! under Wine - on its real LAN provider (TCPN) instead of its loopback one (LOOP), so the games
//! can host and join LAN games. Start it once and leave it open: it notices each game that starts,
//! switches it, and switches it again whenever the game falls back to LOOP (a provider is rebuilt
//! as LOOP after a game ends, or when the menus ask for one). It is what W3Champions' launcher does
//! for its own games, done the same way:
//!
//! 1. The game's code is encrypted on disk and decrypted page by page as it runs. The provider
//!    selector - the `mov ecx,'LOOP'` in the handler behind InitializeLocalNetProvider - is only
//!    there once that handler has run, so the activator asks the menus for it once if need be.
//! 2. With the game's threads suspended (and none on that instruction), it writes 'TCPN' over the
//!    operand's 'LOOP', asks the menus for InitializeLocalNetProvider - the game builds a TCPN
//!    provider - and writes 'LOOP' back at once. The code stays changed for a moment only: the
//!    game checks its own code, and a patch left in place gets it shut down within a minute.
//!    The provider it built stays until the next rebuild.
//! 3. It talks to the menus the way their own page does, over their websocket; the page's port
//!    and guid are read from the game's memory. It writes them to
//!    `%TEMP%\slop-activator\<pid>.json` with a count of its switches so far, so a harness can
//!    drive that game's menus too (harness/webui/bridge.py). A harness about to search for a LAN
//!    game drops `<pid>.request` there: the activator switches again (which rebuilds the provider,
//!    so the search gets a fresh one) and the count goes up.
//!
//! It refuses game builds the patterns were not checked on (--any-build to try anyway), and a
//! selector pattern that matches more than one place.
//!
//!   slop-activator [--once] [--any-build]
//!
//! --once: switch the games running now, then exit, instead of watching.

// Built off Windows only for its tests, where the Windows side is missing
#![cfg_attr(not(windows), allow(dead_code))]

mod menus;
mod patterns;
#[cfg(windows)]
mod win;

#[cfg(not(windows))]
fn main() {
    eprintln!("slop-activator is a Windows program: build it with --target x86_64-pc-windows-gnu and run it on Windows, or with wine in the game's prefix");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    let mut once = false;
    let mut any_build = false;
    for arg in std::env::args().skip(1) {
        match arg.as_str() {
            "--once" => once = true,
            "--any-build" => any_build = true,
            _ => {
                eprintln!("unknown argument {arg}; usage: slop-activator [--once] [--any-build]");
                std::process::exit(2);
            }
        }
    }
    watch::run(once, any_build);
}

/// Seconds since midnight UTC as hh:mm:ss, for the log.
fn clock() -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    format!("{:02}:{:02}:{:02}", now / 3600 % 24, now / 60 % 60, now % 60)
}

#[allow(unused_macros)]
macro_rules! log {
    ($($arg:tt)*) => { println!("{} {}", crate::clock(), format!($($arg)*)) };
}

#[cfg(windows)]
mod watch {
    use crate::menus::Menus;
    use crate::patterns::{name, LOOP, TCPN};
    use crate::win::{running_games, Game, Result};
    use std::collections::HashMap;
    use std::path::PathBuf;
    use std::thread::JoinHandle;
    use std::time::{Duration, Instant};

    pub fn run(once: bool, any_build: bool) {
        log!("slop-activator: watching for Warcraft III (builds {})", crate::patterns::KNOWN_BUILDS.join(", "));
        let mut workers: HashMap<u32, JoinHandle<()>> = HashMap::new();
        loop {
            for pid in running_games() {
                workers.entry(pid).or_insert_with(|| {
                    std::thread::spawn(move || {
                        if let Err(error) = look_after(pid, any_build, once) {
                            log!("game {pid}: {error}");
                        }
                    })
                });
            }
            if once {
                for (_, worker) in workers.drain() {
                    let _ = worker.join();
                }
                return;
            }
            // A finished worker keeps its pid, so a game it gave up on is not tried again; the
            // pid is forgotten once that game is gone
            let running = running_games();
            workers.retain(|pid, worker| !worker.is_finished() || running.contains(pid));
            std::thread::sleep(Duration::from_secs(1));
        }
    }

    /// One game, from when it is noticed until it exits (or, with `once`, until it is switched).
    fn look_after(pid: u32, any_build: bool, once: bool) -> Result<()> {
        let game = Game::open(pid, any_build)?;
        log!("game {pid}: Warcraft III {}", game.version);
        let (port, guid) = wait(&game, Duration::from_secs(180), || Ok(game.menus_address()))
            .map_err(|e| format!("its menus never came up ({e})"))?;
        let file = instance_file(pid);
        log!("game {pid}: menus on 127.0.0.1:{port}, guid {guid}");
        let mut menus = Menus::connect(port, &guid).map_err(|e| format!("connecting to its menus: {e}"))?;
        write_instance(&file, pid, port, &guid, 0);
        activate(&game, &mut menus)?;
        let mut activations = 1;
        write_instance(&file, pid, port, &guid, activations);
        if once {
            return Ok(());
        }
        // The game's answers to the activator's own rebuild are not a fall back to LOOP
        let mut quiet_until = Instant::now() + Duration::from_secs(3);
        let result = (|| -> Result<()> {
            while game.alive() {
                let message = match menus.next(Duration::from_secs(1)) {
                    Ok(message) => message,
                    Err(_) if !game.alive() => break,
                    Err(error) => return Err(format!("its menus went away: {error}")),
                };
                // Asked for (a harness about to search: a switch rebuilds the provider, so the search
                // gets a fresh TCPN one), or seen falling back to LOOP
                let asked = std::fs::remove_file(request_file(pid)).is_ok();
                let fell_back = message.is_some_and(|m| {
                    m["messageType"] == "OnNetProviderChanged" && m["payload"]["providerId"] == "LOOP"
                });
                if asked || (fell_back && Instant::now() > quiet_until) {
                    log!("game {pid}: {}", if asked { "switch asked for" } else { "back on LOOP" });
                    activate(&game, &mut menus)?;
                    activations += 1;
                    write_instance(&file, pid, port, &guid, activations);
                    quiet_until = Instant::now() + Duration::from_secs(3);
                }
            }
            log!("game {pid}: exited");
            Ok(())
        })();
        let _ = std::fs::remove_file(&file);
        result
    }

    /// Switches the game's provider to TCPN: patch, rebuild, restore.
    fn activate(game: &Game, menus: &mut Menus) -> Result<()> {
        let rebuild = |menus: &mut Menus| -> Result<()> {
            menus.send("InitializeLocalNetProvider").map_err(|e| format!("asking the menus for a provider: {e}"))?;
            match menus.wait_for("OnNetProviderChanged", Duration::from_secs(10)) {
                Ok(Some(_)) => Ok(()),
                Ok(None) => Err("the game did not rebuild its provider".into()),
                Err(e) => Err(format!("its menus went away: {e}")),
            }
        };
        let (operand, current) = match game.selector()? {
            Some(site) => site,
            None => {
                // Not decrypted yet: running the handler once brings its page in
                rebuild(menus)?;
                wait(game, Duration::from_secs(10), || game.selector()).map_err(|e| {
                    format!("the provider selector never showed up after a provider rebuild ({e}; a build the pattern does not fit?)")
                })?
            }
        };
        if current == TCPN {
            // Left switched by an activator that stopped half-way
            game.patch(operand, TCPN, LOOP)?;
        }
        game.patch(operand, LOOP, TCPN)?;
        let rebuilt = rebuild(menus);
        let restored = game.patch(operand, TCPN, LOOP);
        rebuilt?;
        restored.map_err(|e| format!("putting the selector back to LOOP failed ({e}); the game may shut itself down"))?;
        log!("game {}: on {} (selector at {operand:#x}, back to {} again)", game.pid, name(TCPN), name(LOOP));
        Ok(())
    }

    /// Calls `find` until it gives something, while the game runs; Err on timeout or exit.
    fn wait<T>(game: &Game, timeout: Duration, mut find: impl FnMut() -> Result<Option<T>>) -> Result<T> {
        let end = Instant::now() + timeout;
        loop {
            if let Some(found) = find()? {
                return Ok(found);
            }
            if !game.alive() {
                return Err("the game exited".into());
            }
            if Instant::now() > end {
                return Err(format!("nothing in {} s", timeout.as_secs()));
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    }

    fn instance_file(pid: u32) -> PathBuf {
        std::env::temp_dir().join("slop-activator").join(format!("{pid}.json"))
    }

    /// Dropped by a harness to ask for a switch now; taken away when it is done.
    fn request_file(pid: u32) -> PathBuf {
        std::env::temp_dir().join("slop-activator").join(format!("{pid}.request"))
    }

    /// What a harness needs to drive this game's menus; `activations` counts the switches to TCPN,
    /// so it can wait for the one after a provider rebuild it asked for.
    fn write_instance(file: &PathBuf, pid: u32, port: u16, guid: &str, activations: u32) {
        let body = serde_json::json!({"pid": pid, "port": port, "guid": guid, "activations": activations});
        // Whole or not at all: a reader never sees half a file
        let temporary = file.with_extension("json.part");
        let written = std::fs::create_dir_all(file.parent().unwrap())
            .and_then(|_| std::fs::write(&temporary, body.to_string()))
            .and_then(|_| std::fs::rename(&temporary, file));
        if let Err(error) = written {
            log!("game {pid}: could not write {}: {error}", file.display());
        }
    }
}
