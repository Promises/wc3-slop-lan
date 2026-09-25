//! How a client finds the game: classic LAN discovery over UDP, as W3Champions does it today
//! (captured 2026-09-24). The client broadcasts SearchGame to UDP 16000 and whoever holds that
//! port answers with GameInfo. A client only searches once its LAN provider is on
//! (scripts/activate.sh); the Bonjour record Flo's public source publishes is not what it lists.

use crate::slots::SLOTS;
use bytes::{BufMut, BytesMut};
use std::time::{Duration, Instant};

/// Where clients look for LAN games; a client takes the first free port from here up itself
const DISCOVERY_PORT: u16 = 16000;
/// Where clients listen, for announcing when the search port is taken
const CLIENT_PORTS: &[u16] = &[16000, 16001, 16002, 16003];
/// What a 3.0.0.24268 client puts in its SearchGame (captured)
const PRODUCT: &[u8; 4] = b"PX3W";
const PROTOCOL_VERSION: u32 = 10200;
/// Game flags W3Champions' host sends (observers: full)
const GAME_FLAGS: u32 = 0x0010_0000;

/// Everything a GameInfo answer needs.
pub struct Listing {
    pub name: String,
    /// The encoded game settings (stat string), as the map check also carries them
    pub settings: Vec<u8>,
    pub port: u16,
    pub players: u32,
    pub started: Instant,
}

impl Listing {
    /// The answer to a SearchGame, laid out as W3Champions' host sends it: product and version
    /// echoed from the search, host counter, entry key, name, empty password, the settings,
    /// then slots, game flags, a constant 1, open slots, uptime and the TCP port.
    fn game_info(&self, product: &[u8], version: u32) -> Vec<u8> {
        let mut body = BytesMut::new();
        body.put_slice(product);
        body.put_u32_le(version);
        body.put_u32_le(1);
        body.put_u32_le(0);
        body.put_slice(self.name.as_bytes());
        body.put_u8(0);
        body.put_u8(0);
        body.put_slice(&self.settings);
        body.put_u32_le(SLOTS as u32);
        body.put_u32_le(GAME_FLAGS);
        body.put_u32_le(1);
        body.put_u32_le(SLOTS as u32 - self.players);
        body.put_u32_le(self.started.elapsed().as_secs() as u32);
        body.put_u16_le(self.port);
        let mut packet = vec![0xF7, 0x30];
        packet.extend_from_slice(&((body.len() + 4) as u16).to_le_bytes());
        packet.extend_from_slice(&body);
        packet
    }
}

/// Answers searches when it can hold the search port. When something else holds it (W3Champions
/// does, when it runs), it does what classic LAN hosts did anyway: announces the game to the
/// clients' own ports every second.
pub async fn serve(listing: Listing) {
    let socket = match tokio::net::UdpSocket::bind(("0.0.0.0", DISCOVERY_PORT)).await {
        Ok(s) => {
            tracing::info!("answering LAN searches on UDP {}", DISCOVERY_PORT);
            s
        }
        Err(e) => {
            tracing::warn!("UDP {} is taken ({}); announcing to the clients' ports instead", DISCOVERY_PORT, e);
            tokio::net::UdpSocket::bind(("127.0.0.1", 0)).await.expect("udp")
        }
    };
    socket.set_broadcast(true).ok();
    let mut announce = tokio::time::interval(Duration::from_secs(1));
    let mut buf = [0u8; 2048];
    loop {
        tokio::select! {
            _ = announce.tick() => {
                let info = listing.game_info(PRODUCT, PROTOCOL_VERSION);
                for port in CLIENT_PORTS {
                    socket.send_to(&info, ("127.0.0.1", *port)).await.ok();
                }
            }
            received = socket.recv_from(&mut buf) => {
                let Ok((len, from)) = received else { continue };
                let packet = &buf[..len];
                if len < 16 || packet[0] != 0xF7 || packet[1] != 0x2F {
                    continue;
                }
                let version = u32::from_le_bytes(packet[8..12].try_into().unwrap());
                tracing::debug!("search from {} (version {})", from, version);
                // The client listens on its own port on loopback, whatever address it searched from
                let answer = listing.game_info(&packet[4..8], version);
                socket.send_to(&answer, ("127.0.0.1", from.port())).await.ok();
            }
        }
    }
}
