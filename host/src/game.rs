//! One game from lobby to the end: the join burst, countdown, loading, then the lockstep loop
//! that relays every player's actions and chat. The protocol is Flo's (W3Champions' client,
//! github.com/BogdanW3/W3C-Flo); the join burst mirrors their crates/client/src/lan/game/lobby.rs.

use crate::map::Result;
use crate::slots::Slots;
use bytes::{BufMut, Bytes, BytesMut};
use flo_util::binary::SockAddr;
use flo_w3gs::chat::{ChatFromHost, ChatToHost, MessageScope};
use flo_w3gs::constants::{LeaveReason, PacketTypeId, ProtoBufMessageTypeId};
use flo_w3gs::game::{CountDownEnd, CountDownStart};
use flo_w3gs::join::{ReqJoin, SlotInfoJoin};
use flo_w3gs::leave::{LeaveAck, LeaveReq, PlayerLeft};
use flo_w3gs::map::MapSize;
use flo_w3gs::packet::{Packet, PacketPayload, ProtoBufPayload};
use flo_w3gs::ping::PongToHost;
use flo_w3gs::player::{PlayerInfo, PlayerLoaded, PlayerProfileMessage, PlayerSkinsMessage};
use flo_w3gs::protocol::action::{IncomingAction, OutgoingAction, OutgoingKeepAlive, PlayerAction, TimeSlot};
use std::collections::HashMap;
use std::net::SocketAddr;
use std::time::{Duration, Instant};
use tokio::sync::mpsc;

/// Flo waits this long between the two countdown packets; starting sooner sends slow clients
/// straight to the score screen.
const COUNTDOWN: Duration = Duration::from_secs(6);
/// The longest time step one tick may carry, after a stall
const MAX_STEP_MS: u128 = 250;
/// How many ticks a player may fall behind (unanswered by a keepalive) before the game waits
/// for them, as gHost++'s sync limit does; without it a stalled client drifts ever further back
const SYNC_LIMIT: u64 = 50;

/// The host's own seat, when it is active: a player it plays itself, as gHost++'s fake player
/// does. Actions a test injects come from here - a client drops actions filed under its own
/// player that it did not send, and the engine ignores observers' actions, so they have to come
/// from a real player that no client controls. The map has to know the seat is not a real
/// player, and listen for sync data with the prefix from it (Warcraft Maul's test builds do).
pub struct Seat {
    pub slot: usize,
    pub name: String,
    pub prefix: String,
}

impl Seat {
    fn pid(&self) -> u8 {
        (self.slot + 1) as u8
    }
}

#[derive(PartialEq, Debug)]
enum Phase {
    Lobby,
    Countdown(Instant),
    Loading,
    Playing,
}

struct Player {
    pid: u8,
    name: String,
    conn: Option<u32>,
    map_ok: bool,
    skins: bool,
    unknown5: bool,
    loaded: bool,
    left: bool,
    keepalives: Vec<u32>,
}

pub struct Game {
    players: Vec<Player>,
    connections: HashMap<u32, (mpsc::UnboundedSender<Packet>, SocketAddr)>,
    slots: Slots,
    map_check: Packet,
    map_size: u32,
    seat: Option<Seat>,
    phase: Phase,
    pending: Vec<PlayerAction>,
    last_tick: Instant,
    ticks: u64,
    /// Holding ticks back until a player catches up
    waiting: bool,
    started: Instant,
}

impl Game {
    pub fn new(names: &[String], slots: Slots, map_check: Packet, map_size: u32, seat: Option<Seat>) -> Self {
        let players = names.iter().zip(&slots.player_ids).map(|(name, pid)| Player {
            pid: *pid,
            name: name.clone(),
            conn: None,
            map_ok: false,
            skins: false,
            unknown5: false,
            loaded: false,
            left: false,
            keepalives: vec![],
        }).collect();
        Game {
            players,
            connections: HashMap::new(),
            slots,
            map_check,
            map_size,
            seat,
            phase: Phase::Lobby,
            pending: vec![],
            last_tick: Instant::now(),
            ticks: 0,
            waiting: false,
            started: Instant::now(),
        }
    }

    pub fn started(&self) -> Instant {
        self.started
    }

    pub fn is_over(&self) -> bool {
        self.phase == Phase::Playing && self.players.iter().all(|p| p.left)
    }

    pub fn connected(&mut self, conn: u32, tx: mpsc::UnboundedSender<Packet>, local: SocketAddr) {
        self.connections.insert(conn, (tx, local));
    }

    pub fn closed(&mut self, conn: u32) {
        self.connections.remove(&conn);
        if let Some(pid) = self.player_by_conn(conn).map(|p| p.pid) {
            tracing::info!("player {} disconnected", pid);
            self.player_left(pid, LeaveReason::LeaveDisconnect);
            // Leaving the lobby frees the seat for another try
            if self.phase == Phase::Lobby {
                if let Some(p) = self.player_by_conn(conn) {
                    p.conn = None;
                    p.left = false;
                }
            }
        }
    }

    fn player_by_conn(&mut self, conn: u32) -> Option<&mut Player> {
        self.players.iter_mut().find(|p| p.conn == Some(conn))
    }

    fn send_to(&self, pid: u8, packet: Packet) {
        if let Some(conn) = self.players.iter().find(|p| p.pid == pid).and_then(|p| p.conn) {
            if let Some((tx, _)) = self.connections.get(&conn) {
                tx.send(packet).ok();
            }
        }
    }

    pub fn broadcast(&self, packet: &Packet) {
        for p in self.players.iter().filter(|p| !p.left) {
            if let Some((tx, _)) = p.conn.and_then(|c| self.connections.get(&c)) {
                tx.send(packet.clone()).ok();
            }
        }
    }

    /// The one burst a client gets for its ReqJoin, as Flo sends it: its slot and the table,
    /// every other player's info and skins, everyone's profile, and the map to check.
    fn welcome(&self, pid: u8, local: SocketAddr) -> Result<Vec<Packet>> {
        let SocketAddr::V4(local) = local else { return Err("ipv6".into()) };
        let others: Vec<(u8, &str)> = self.players.iter().filter(|p| p.pid != pid).map(|p| (p.pid, p.name.as_str()))
            .chain(self.seat.iter().map(|s| (s.pid(), s.name.as_str())))
            .collect();
        let mut out = vec![
            self.slots.with_layout(Packet::simple(SlotInfoJoin {
                slot_info: self.slots.info.clone(),
                player_id: pid,
                external_addr: SockAddr::from(local),
            })?),
            self.slots.with_layout(Packet::simple(self.slots.info.clone())?),
        ];
        for (other, name) in &others {
            out.push(Packet::simple(PlayerInfo::new(*other, *name))?);
        }
        for (other, _) in &others {
            out.push(Packet::simple(ProtoBufPayload::new(PlayerSkinsMessage::new(*other)))?);
        }
        let everyone = self.players.iter().map(|p| (p.pid, p.name.as_str())).chain(self.seat.iter().map(|s| (s.pid(), s.name.as_str())));
        for (id, name) in everyone {
            out.push(Packet::simple(ProtoBufPayload::new(PlayerProfileMessage::new(id, name)))?);
        }
        out.push(self.map_check.clone());
        Ok(out)
    }

    pub fn handle(&mut self, conn: u32, packet: Packet) -> Result<()> {
        let type_id = packet.type_id();
        if type_id == ReqJoin::PACKET_TYPE_ID {
            return self.join(conn, packet.decode_simple()?);
        }
        let Some(pid) = self.player_by_conn(conn).map(|p| p.pid) else { return Ok(()) };

        if type_id == MapSize::PACKET_TYPE_ID {
            let size: MapSize = packet.decode_simple()?;
            // The client answers with its own copy's size; anything else (0 above all) means it
            // found no matching map: wrong path, or a checksum it does not agree with
            if size.map_size == self.map_size {
                tracing::info!("player {} has the map", pid);
                self.player_by_conn(conn).unwrap().map_ok = true;
            } else {
                tracing::error!("player {} does not have this map: it reports {} bytes, expected {}", pid, size.map_size, self.map_size);
            }
        } else if type_id == ProtoBufPayload::PACKET_TYPE_ID {
            let payload: ProtoBufPayload = packet.decode_simple()?;
            match payload.type_id {
                ProtoBufMessageTypeId::PlayerSkins => self.player_by_conn(conn).unwrap().skins = true,
                ProtoBufMessageTypeId::PlayerUnknown5 => self.player_by_conn(conn).unwrap().unknown5 = true,
                _ => {}
            }
            // Flo hands these straight back to the client that sent them
            self.send_to(pid, packet);
        } else if type_id == PongToHost::PACKET_TYPE_ID {
        } else if type_id == PacketTypeId::GameLoadedSelf {
            tracing::info!("player {} finished loading", pid);
            self.player_by_conn(conn).unwrap().loaded = true;
            if let Some(seat) = &self.seat {
                self.send_to(pid, Packet::simple(PlayerLoaded::new(seat.pid()))?);
            }
            self.broadcast(&Packet::simple(PlayerLoaded::new(pid))?);
        } else if type_id == OutgoingAction::PACKET_TYPE_ID {
            let action: OutgoingAction = packet.decode_payload()?;
            tracing::debug!("player {} action {}", pid, hex(&action.data));
            self.pending.push(PlayerAction { player_id: pid, data: action.data });
        } else if type_id == OutgoingKeepAlive::PACKET_TYPE_ID {
            let keepalive: OutgoingKeepAlive = packet.decode_simple()?;
            self.player_by_conn(conn).unwrap().keepalives.push(keepalive.checksum);
            self.compare_checksums();
        } else if type_id == ChatToHost::PACKET_TYPE_ID {
            let chat: ChatToHost = packet.decode_simple()?;
            if let Some(text) = chat.chat_message() {
                tracing::info!("chat from {}: {}", pid, String::from_utf8_lossy(text));
            }
            // The same payload, retyped, to the players it was for - never back to the sender
            let relay = Packet::simple(ChatFromHost::from(chat.clone()))?;
            for to in chat.to_players.iter().filter(|to| **to != pid) {
                self.send_to(*to, relay.clone());
            }
        } else if type_id == LeaveReq::PACKET_TYPE_ID {
            let req: LeaveReq = packet.decode_simple()?;
            tracing::info!("player {} leaves: {:?}", pid, req.reason());
            self.send_to(pid, Packet::simple(LeaveAck)?);
            self.player_left(pid, req.reason());
        } else {
            tracing::debug!("player {} sent {:?}", pid, type_id);
        }
        Ok(())
    }

    fn join(&mut self, conn: u32, req: ReqJoin) -> Result<()> {
        let name = req.player_name.to_string_lossy().to_string();
        let local = self.connections.get(&conn).map(|c| c.1).ok_or("no connection")?;
        // By name when it matches; otherwise the first free place, since which account a client
        // carries (or none, offline) is not always the one expected
        let free = match self.players.iter().position(|p| p.conn.is_none() && p.name.eq_ignore_ascii_case(&name)) {
            Some(i) => Some(i),
            None => self.players.iter().position(|p| p.conn.is_none()),
        };
        let Some(index) = free else {
            tracing::warn!("{} joined a full game; ignoring them", name);
            return Ok(());
        };
        let player = &mut self.players[index];
        // An offline client joins without a name; it then keeps the configured one
        if !name.is_empty() && !player.name.eq_ignore_ascii_case(&name) {
            tracing::info!("{:?} takes the place of {}", name, player.name);
            player.name = name.clone();
        }
        player.conn = Some(conn);
        let pid = player.pid;
        tracing::info!("{:?} joined as player {} ({})", name, pid, player.name);
        for packet in self.welcome(pid, local)? {
            self.send_to(pid, packet);
        }
        Ok(())
    }

    fn player_left(&mut self, pid: u8, reason: LeaveReason) {
        let Some(player) = self.players.iter_mut().find(|p| p.pid == pid) else { return };
        if player.left {
            return;
        }
        player.left = true;
        if let Ok(packet) = Packet::simple(PlayerLeft { player_id: pid, reason }) {
            self.broadcast(&packet);
        }
    }

    /// Each client answers every tick with a checksum of its game state; the Nth from every
    /// player must agree, or the game has desynced.
    fn compare_checksums(&self) {
        let active: Vec<&Player> = self.players.iter().filter(|p| !p.left && p.conn.is_some()).collect();
        let n = active.iter().map(|p| p.keepalives.len()).min().unwrap_or(0);
        if n == 0 {
            return;
        }
        let first = active[0].keepalives[n - 1];
        if active.iter().any(|p| p.keepalives[n - 1] != first) {
            let sums: Vec<String> = active.iter().map(|p| format!("p{}={:08x}", p.pid, p.keepalives[n - 1])).collect();
            tracing::error!("DESYNC at tick {}: {}", n - 1, sums.join(" "));
        }
    }

    /// Advances the game: countdown and loading while they are due, then one lockstep tick.
    pub fn step(&mut self) -> Result<()> {
        match self.phase {
            Phase::Lobby if self.players.iter().all(|p| p.conn.is_some() && p.map_ok && p.skins && p.unknown5) => {
                tracing::info!("everyone is in, counting down");
                self.broadcast(&self.slots.with_layout(Packet::simple(self.slots.info.clone())?));
                self.broadcast(&Packet::simple(CountDownStart)?);
                self.phase = Phase::Countdown(Instant::now() + COUNTDOWN);
            }
            Phase::Countdown(at) if Instant::now() >= at => {
                tracing::info!("loading");
                self.broadcast(&Packet::simple(CountDownEnd)?);
                self.phase = Phase::Loading;
            }
            Phase::Loading if self.players.iter().filter(|p| !p.left).all(|p| p.loaded) => {
                tracing::info!("everyone has loaded, the game is on");
                self.phase = Phase::Playing;
                self.last_tick = Instant::now();
            }
            Phase::Playing => {
                let behind = self.players.iter().filter(|p| !p.left && p.conn.is_some())
                    .map(|p| self.ticks.saturating_sub(p.keepalives.len() as u64)).max().unwrap_or(0);
                if behind > SYNC_LIMIT {
                    if !self.waiting {
                        tracing::warn!("a player is {} ticks behind; waiting for them", behind);
                        self.waiting = true;
                    }
                    self.last_tick = Instant::now();
                    return Ok(());
                }
                if self.waiting {
                    tracing::info!("everyone caught up");
                    self.waiting = false;
                }
                let now = Instant::now();
                let step = now.duration_since(self.last_tick).as_millis().min(MAX_STEP_MS) as u16;
                self.last_tick = now;
                let actions = std::mem::take(&mut self.pending);
                self.broadcast(&Packet::with_payload(IncomingAction(TimeSlot { time_increment_ms: step, actions }))?);
                self.ticks += 1;
            }
            _ => {}
        }
        Ok(())
    }

    /// One control command; the reply is one line.
    pub fn control(&mut self, line: &str) -> String {
        let mut words = line.trim().splitn(3, ' ');
        let verb = words.next().unwrap_or("");
        let first = words.next().unwrap_or("");
        let rest = words.next().unwrap_or("");
        let playing = self.phase == Phase::Playing;
        match verb {
            "status" => {
                let players: Vec<String> = self.players.iter().map(|p| {
                    format!("p{} {} conn={} map={} loaded={} left={}", p.pid, p.name, p.conn.is_some(), p.map_ok, p.loaded, p.left)
                }).collect();
                let seat = self.seat.as_ref().map(|s| format!("seat={}", s.slot)).unwrap_or_else(|| "hidden".into());
                format!("{:?} ticks={}{} up={}s {} | {}", self.phase, self.ticks, if self.waiting { " waiting" } else { "" },
                        self.started.elapsed().as_secs(), seat, players.join(" | "))
            }
            "type" => {
                // What a client sends when its player types a line in a map with chat triggers
                // (0x60): the map's chat events fire as the seat's player, no library needed.
                // The two words before the text are not understood; clients send ids there, and
                // whether the engine checks them is unverified.
                let Some(seat) = &self.seat else { return "the host is hidden: start it with --host active".into() };
                let text = line.trim().splitn(2, ' ').nth(1).unwrap_or("");
                if text.is_empty() {
                    return "usage: type <text>".into();
                }
                if !playing {
                    return "not in a game yet".into();
                }
                let mut action = BytesMut::new();
                action.put_u8(0x60);
                action.put_u32_le(0);
                action.put_u32_le(0);
                action.put_slice(text.as_bytes());
                action.put_u8(0);
                self.pending.push(PlayerAction { player_id: seat.pid(), data: action.freeze() });
                format!("queued '{}' typed by the seat", text)
            }
            "cmd" => {
                let Some(seat) = &self.seat else { return "the host is hidden: start it with --host active".into() };
                if first.parse::<u8>().is_err() || rest.is_empty() {
                    return "usage: cmd <map player, 0 = red> <line>".into();
                }
                if !playing {
                    return "not in a game yet".into();
                }
                let action = sync_data(&seat.prefix, &format!("{} {}", first, rest));
                self.pending.push(PlayerAction { player_id: seat.pid(), data: action });
                format!("queued '{}' for map player {}", rest, first)
            }
            "sync" => {
                let (Ok(pid), Some((prefix, data))) = (first.parse::<u8>(), rest.split_once(' ')) else {
                    return "usage: sync <player id> <prefix> <data>".into();
                };
                if !playing {
                    return "not in a game yet".into();
                }
                self.pending.push(PlayerAction { player_id: pid, data: sync_data(prefix, data) });
                format!("queued for player {}", pid)
            }
            "raw" => {
                let Ok(pid) = first.parse::<u8>() else { return "usage: raw <player id> <hex bytes>".into() };
                let digits: String = rest.chars().filter(|c| c.is_ascii_hexdigit()).collect();
                let bytes: Option<Vec<u8>> = (0..digits.len() / 2).map(|i| u8::from_str_radix(&digits[2 * i..2 * i + 2], 16).ok()).collect();
                match bytes {
                    _ if !playing => "not in a game yet".into(),
                    Some(b) if !b.is_empty() => {
                        let n = b.len();
                        self.pending.push(PlayerAction { player_id: pid, data: Bytes::from(b) });
                        format!("queued {} bytes for player {}", n, pid)
                    }
                    _ => "bad hex".into(),
                }
            }
            "say" => {
                let Ok(pid) = first.parse::<u8>() else { return "usage: say <player id> <text>".into() };
                match Packet::simple(ChatFromHost::private_to_self(pid, format!("[host] {}", rest))) {
                    Ok(p) => {
                        self.send_to(pid, p);
                        "sent".into()
                    }
                    Err(e) => e.to_string(),
                }
            }
            "chat" => {
                let text = line.trim().splitn(2, ' ').nth(1).unwrap_or("");
                if text.is_empty() {
                    return "usage: chat <text>".into();
                }
                // From the seat when there is one, else the first player still in, as gHost++
                // picks its sender; everyone sees it under that player's name, the map does not
                let Some(from) = self.seat.as_ref().map(|s| s.pid()).or(self.players.iter().find(|p| !p.left && p.conn.is_some()).map(|p| p.pid)) else {
                    return "nobody to send it to".into();
                };
                let to: Vec<u8> = self.players.iter().filter(|p| !p.left && p.conn.is_some()).map(|p| p.pid).collect();
                let chat = match self.phase {
                    Phase::Lobby => ChatFromHost::lobby(from, &to, text),
                    _ => ChatToHost::in_game(MessageScope::All, from, &to, text).into(),
                };
                match Packet::simple(chat) {
                    Ok(p) => {
                        self.broadcast(&p);
                        format!("sent to {} players as player {}", to.len(), from)
                    }
                    Err(e) => e.to_string(),
                }
            }
            _ => "commands: status | chat <text> | type <text> | cmd <map player> <line> | sync <player id> <prefix> <data> | raw <player id> <hex> | say <player id> <text>".into(),
        }
    }
}

/// The action BlzSendSyncData produces: 0x77, prefix, data, from_server = 0.
fn sync_data(prefix: &str, data: &str) -> Bytes {
    let mut buf = BytesMut::new();
    buf.put_u8(0x77);
    buf.put_slice(prefix.as_bytes());
    buf.put_u8(0);
    buf.put_slice(data.as_bytes());
    buf.put_u8(0);
    buf.put_u32_le(0);
    buf.freeze()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{:02x}", b)).collect()
}
