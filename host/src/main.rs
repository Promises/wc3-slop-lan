//! wc3-slop-lan: host a LAN game for clients on this machine, put the map library into a map,
//! or look a map over. See docs/host.md.

use flo_util::binary::BinEncode;
use flo_w3gs::constants::GameSettingFlags;
use flo_w3gs::game::GameSettings;
use flo_w3gs::map::MapCheck;
use flo_w3gs::net::W3GSListener;
use flo_w3gs::packet::Packet;
use flo_w3gs::ping::PingFromHost;
use std::ffi::CString;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use tokio::sync::mpsc;
use wc3_slop_lan::discovery::{self, Listing};
use wc3_slop_lan::game::{Game, Seat};
use wc3_slop_lan::inject::{self, LibraryConfig};
use wc3_slop_lan::map::{self, MapFacts, Result};
use wc3_slop_lan::net::{self, Event};
use wc3_slop_lan::slots::Slots;

/// The lockstep tick, as Flo and Battle.net run it
const TICK: Duration = Duration::from_millis(30);
const PING: Duration = Duration::from_secs(5);

const USAGE: &str = "usage:
  wc3-slop-lan host --map <file> [options] <player name>...
      --host active|hidden     active: the host plays a seat of its own and can act in the game;
                               hidden: it takes no slot and only relays (default)
      --seat <slot|auto>       the active host's slot, 0-based; auto: the first slot the map leaves undefined
      --seat-name <name>       default: HOST
      --prefix <prefix>        the sync prefix the map library listens on (default: slop)
      --map-path <path>        the map as the game names it (default: maps\\... from a Maps folder)
      --name <game name>       default: the map's file name
      --control <port>         the control port (default: 8778)
      --break-map-check <field> diagnostics: break path|size|crc|xoro|sha1 in the map check
      --xoro <hex>             diagnostics: send this map checksum instead
  wc3-slop-lan inject --map <file> --out <file> [--seat <slot|auto>] [--prefix <prefix>]
                               a copy of a Lua map with the map library in its script
  wc3-slop-lan map <file>      the map's checksums, players, forces, free slot and files";

/// Where the active host sits: a slot, or the first one the map leaves undefined.
#[derive(Clone, Copy)]
enum SeatChoice {
    Auto,
    Slot(usize),
}

impl SeatChoice {
    fn parse(value: &str) -> std::result::Result<Self, String> {
        match value {
            "auto" => Ok(SeatChoice::Auto),
            slot => slot.parse().map(SeatChoice::Slot).map_err(|_| "--seat takes a slot number or auto".into()),
        }
    }

    fn resolve(self, facts: &MapFacts) -> Result<usize> {
        match self {
            SeatChoice::Slot(slot) => Ok(slot),
            SeatChoice::Auto => Ok(facts.free_slot().ok_or("the map defines every slot; name one with --seat")?),
        }
    }
}

struct HostArgs {
    map: PathBuf,
    map_path: Option<String>,
    name: Option<String>,
    active: bool,
    seat: SeatChoice,
    seat_name: String,
    prefix: String,
    control: u16,
    break_map_check: Option<String>,
    xoro: Option<u32>,
    players: Vec<String>,
}

fn parse_host(args: &[String]) -> std::result::Result<HostArgs, String> {
    let mut out = HostArgs {
        map: PathBuf::new(),
        map_path: None,
        name: None,
        active: false,
        seat: SeatChoice::Auto,
        seat_name: "HOST".into(),
        prefix: "slop".into(),
        control: 8778,
        break_map_check: None,
        xoro: None,
        players: vec![],
    };
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        let mut value = || it.next().cloned().ok_or(format!("{} needs a value", arg));
        match arg.as_str() {
            "--map" => out.map = value()?.into(),
            "--map-path" => out.map_path = Some(value()?),
            "--name" => out.name = Some(value()?),
            "--host" => out.active = match value()?.as_str() {
                "active" => true,
                "hidden" => false,
                _ => return Err("--host takes active or hidden".into()),
            },
            "--seat" => out.seat = SeatChoice::parse(&value()?)?,
            "--seat-name" => out.seat_name = value()?,
            "--prefix" => out.prefix = value()?,
            "--control" => out.control = value()?.parse().map_err(|_| "--control takes a port")?,
            "--break-map-check" => out.break_map_check = Some(value()?),
            "--xoro" => out.xoro = Some(u32::from_str_radix(value()?.trim_start_matches("0x"), 16).map_err(|_| "--xoro takes hex")?),
            flag if flag.starts_with("--") => return Err(format!("unknown option {}", flag)),
            name => out.players.push(name.to_string()),
        }
    }
    if out.map.as_os_str().is_empty() || out.players.is_empty() {
        return Err("a map and at least one player are needed".into());
    }
    Ok(out)
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt().with_max_level(tracing::Level::INFO).init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let outcome = match args.first().map(String::as_str) {
        Some("host") => match parse_host(&args[1..]) {
            Ok(host) => run_host(host).await,
            Err(e) => Err(format!("{}\n{}", e, USAGE).into()),
        },
        Some("map") if args.len() == 2 => show_map(&PathBuf::from(&args[1])),
        Some("inject") => run_inject(&args[1..]).map_err(|e| format!("{}\n{}", e, USAGE).into()),
        _ => Err(USAGE.into()),
    };
    if let Err(e) = outcome {
        eprintln!("{}", e);
        std::process::exit(2);
    }
}

fn show_map(file: &PathBuf) -> Result<()> {
    let path = map::path_under_maps(file);
    let facts = MapFacts::load(file, Some(path.as_deref().unwrap_or("(not under a Maps folder)")))?;
    println!("path {}", facts.path_in_game);
    println!("size {} crc32 {:#010x} xoro {:#010x} layout {}", facts.size, facts.crc32, facts.xoro, facts.layout);
    println!("sha1 {}", facts.sha1.iter().map(|b| format!("{:02x}", b)).collect::<String>());
    for p in &facts.players {
        println!("player slot={:2} kind={} race={} team={}", p.id, p.kind, p.race, facts.team_of(p.id));
    }
    for (i, set) in facts.forces.iter().enumerate() {
        println!("force {} players={:#010x}", i, set);
    }
    println!("free slot {}", facts.free_slot().map(|s| s.to_string()).unwrap_or_else(|| "none".into()));
    println!("library {}", if inject::is_injected(file) { "injected" } else { "absent" });
    for (name, size) in map::list_files(file)? {
        println!("file {:<18} {}", name, size);
    }
    Ok(())
}

/// The settings every GameInfo and map check carries. Width and height stay 0, as Flo sends them.
fn game_settings(facts: &MapFacts) -> Result<GameSettings> {
    Ok(GameSettings {
        game_setting_flags: GameSettingFlags::default(),
        unk_1: 0,
        map_width: 0,
        map_height: 0,
        map_checksum: facts.xoro,
        map_path: CString::new(facts.path_in_game.clone())?,
        host_name: CString::new("FLO")?,
        map_sha1: facts.sha1,
    })
}

/// The map check a joining client gets, optionally broken on purpose to learn which fields the
/// client compares (it checks path, sha1 and xoro; it echoes size and ignores crc).
fn map_check(facts: &MapFacts, mut settings: GameSettings, host: &HostArgs) -> Result<Packet> {
    let mut size = facts.size;
    let mut crc = facts.crc32;
    if let Some(value) = host.xoro {
        tracing::warn!("sending map checksum {:#010x} instead of {:#010x}", value, settings.map_checksum);
        settings.map_checksum = value;
    }
    if let Some(field) = &host.break_map_check {
        tracing::warn!("map check deliberately broken: {}", field);
        match field.as_str() {
            "path" => settings.map_path = CString::new("maps\\nowhere.w3x")?,
            "size" => size += 1,
            "crc" => crc ^= 0xffff,
            "xoro" => settings.map_checksum ^= 0xffff,
            "sha1" => settings.map_sha1[0] ^= 0xff,
            other => return Err(format!("--break-map-check: no field {}", other).into()),
        }
    }
    Ok(Packet::simple(MapCheck::new(size, crc, &settings))?)
}

fn run_inject(args: &[String]) -> Result<()> {
    let (mut map, mut out, mut seat, mut prefix) = (None, None, None, "slop".to_string());
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        let value = it.next().ok_or(format!("{} needs a value", arg))?;
        match arg.as_str() {
            "--map" => map = Some(PathBuf::from(value)),
            "--out" => out = Some(PathBuf::from(value)),
            "--seat" => seat = Some(SeatChoice::parse(value)?),
            "--prefix" => prefix = value.clone(),
            other => return Err(format!("unknown option {}", other).into()),
        }
    }
    let (map, out) = (map.ok_or("--map is needed")?, out.ok_or("--out is needed")?);
    let facts = MapFacts::load(&map, Some(""))?;
    let seat = seat.map(|s| s.resolve(&facts)).transpose()?;
    inject::inject(&map, &out, &LibraryConfig { seat, prefix })?;
    println!("injected into {}; seat {}", out.display(), seat.map(|s| s.to_string()).unwrap_or_else(|| "none".into()));
    Ok(())
}

async fn run_host(host: HostArgs) -> Result<()> {
    let facts = MapFacts::load(&host.map, host.map_path.as_deref())?;
    let name = host.name.clone().unwrap_or_else(|| {
        host.map.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_else(|| "slop".into())
    });
    let seat_slot = if host.active { Some(host.seat.resolve(&facts)?) } else { None };
    let slots = Slots::build(&facts, host.players.len(), seat_slot)?;
    let seat = seat_slot.map(|slot| Seat { slot, name: host.seat_name.clone(), prefix: host.prefix.clone() });
    match seat_slot {
        Some(slot) => tracing::info!("the host is active: it plays slot {} as {}", slot, host.seat_name),
        None => tracing::info!("the host is hidden: it takes no slot"),
    }
    if !inject::is_injected(&host.map) {
        tracing::info!("the map carries no library: commands need a map that listens (see docs/library.md)");
    }
    let settings = game_settings(&facts)?;
    let check = map_check(&facts, settings.clone(), &host)?;
    tracing::info!("{} as {}: checksum {:#010x}, layout {}", host.map.display(), facts.path_in_game, facts.xoro, facts.layout);

    let mut listener = W3GSListener::bind().await?;
    let port = listener.port();
    tracing::info!("hosting '{}' on {} for {}", name, listener.local_addr(), host.players.join(", "));
    let mut encoded = bytes::BytesMut::new();
    settings.encode(&mut encoded);
    tokio::spawn(discovery::serve(Listing {
        name: name.clone(),
        settings: encoded.to_vec(),
        port,
        players: (host.players.len() + seat.is_some() as usize) as u32,
        started: Instant::now(),
    }));

    let (events_tx, mut events) = mpsc::unbounded_channel::<Event>();
    {
        let events_tx = events_tx.clone();
        tokio::spawn(async move {
            let mut next = 1u32;
            loop {
                match listener.accept().await {
                    Ok(Some(stream)) => {
                        tracing::info!("connection {} from {:?}", next, stream.peer_addr());
                        tokio::spawn(net::serve_client(next, stream, events_tx.clone()));
                        next += 1;
                    }
                    Ok(None) => break,
                    Err(e) => tracing::warn!("accept: {}", e),
                }
            }
        });
    }
    tokio::spawn(net::serve_control(host.control, events_tx.clone()));

    let mut game = Game::new(&host.players, slots, check, facts.size, seat);
    let mut ticker = tokio::time::interval(TICK);
    let mut pinger = tokio::time::interval(PING);
    loop {
        tokio::select! {
            event = events.recv() => match event {
                Some(Event::Connected { conn, tx, local }) => game.connected(conn, tx, local),
                Some(Event::Received { conn, packet }) => {
                    if let Err(e) = game.handle(conn, packet) {
                        tracing::warn!("connection {}: {}", conn, e);
                    }
                }
                Some(Event::Closed { conn }) => game.closed(conn),
                Some(Event::Control { line, reply }) => { reply.send(game.control(&line)).ok(); }
                None => break,
            },
            _ = ticker.tick() => {
                if let Err(e) = game.step() {
                    tracing::warn!("tick: {}", e);
                }
            }
            _ = pinger.tick() => {
                if let Ok(p) = Packet::simple(PingFromHost::with_payload_since(game.started())) {
                    game.broadcast(&p);
                }
            }
        }
        if game.is_over() {
            tracing::info!("everyone has left; game over");
            break;
        }
    }
    Ok(())
}
