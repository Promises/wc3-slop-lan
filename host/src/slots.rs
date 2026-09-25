//! The lobby's slot table, built from the map's own player and force tables.

use crate::map::MapFacts;
use bytes::Bytes;
use flo_w3gs::constants::RacePref;
use flo_w3gs::packet::Packet;
use flo_w3gs::slot::{SlotInfo, SlotStatus};

pub const SLOTS: usize = 24;

/// Where the layout byte sits in an encoded slot table: length, count, the slots, the seed.
const LAYOUT_OFFSET: usize = 2 + 1 + 9 * SLOTS + 4;

/// The slot table: the first human slots the map defines go to the players, in order; every
/// computer the map defines keeps its slot; the host's seat, when the host is active, is a
/// player too.
pub struct Slots {
    pub info: SlotInfo,
    pub layout: u8,
    /// The player id (slot + 1) each joining player gets, in the order they were named
    pub player_ids: Vec<u8>,
}

impl Slots {
    pub fn build(map: &MapFacts, players: usize, seat: Option<usize>) -> Result<Self, String> {
        let humans: Vec<_> = map.players.iter().filter(|p| p.kind == 1).collect();
        if humans.len() < players {
            return Err(format!("the map has {} human slots, {} players were named", humans.len(), players));
        }
        if let Some(seat) = seat {
            if seat >= SLOTS || humans.iter().take(players).any(|p| p.id == seat) {
                return Err(format!("the host's seat {} is not a free slot", seat));
            }
        }
        let seed = (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() & 0x7fff_ffff) as i32;
        let mut info = SlotInfo::build()
            .random_seed(seed)
            .num_slots(SLOTS)
            .num_players(players + seat.is_some() as usize)
            .build();

        // The map's forces when it has them; otherwise one team per occupied slot, in order
        let mut next_team = 0u8;
        let mut team = |slot: usize| {
            if map.custom_forces() {
                map.team_of(slot)
            } else {
                next_team += 1;
                next_team - 1
            }
        };
        let mut player_ids = vec![];
        for p in humans.iter().take(players) {
            occupy(&mut info, p.id, team(p.id), race(p.race), false);
            player_ids.push((p.id + 1) as u8);
        }
        for p in map.players.iter().filter(|p| p.kind == 2) {
            occupy(&mut info, p.id, team(p.id), race(p.race), true);
        }
        if let Some(seat) = seat {
            occupy(&mut info, seat, team(seat), RacePref::HUMAN, false);
        }
        Ok(Slots { info, layout: map.layout, player_ids })
    }

    /// A slot table packet (SlotInfo or SlotInfoJoin, which starts with one) with the map's layout.
    pub fn with_layout(&self, mut packet: Packet) -> Packet {
        let mut bytes = packet.payload.to_vec();
        bytes[LAYOUT_OFFSET] = self.layout;
        packet.payload = Bytes::from(bytes);
        packet
    }
}

fn occupy(info: &mut SlotInfo, index: usize, team: u8, race: RacePref, computer: bool) {
    let slot = info.slot_mut(index).expect("slot in range");
    if !computer {
        slot.player_id = (index + 1) as u8;
    }
    slot.computer = computer;
    slot.slot_status = SlotStatus::Occupied;
    slot.race = race;
    slot.color = index as u8;
    slot.team = team;
    slot.handicap = 100;
    slot.download_status = 100;
}

fn race(map_race: u32) -> RacePref {
    match map_race {
        1 => RacePref::HUMAN,
        2 => RacePref::ORC,
        3 => RacePref::UNDEAD,
        4 => RacePref::NIGHTELF,
        _ => RacePref::RANDOM | RacePref::SELECTABLE,
    }
}
