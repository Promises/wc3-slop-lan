//! What a host needs to know about a map, read from the map file itself.

use flo_util::binary::BinDecode;
use flo_w3map::{MapFlags, MapInfo, W3Map};
use std::path::Path;
use stormlib::{Archive, OpenArchiveFlags};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

/// One player the map defines (war3map.w3i).
#[derive(Debug, Clone)]
pub struct MapPlayer {
    /// The slot, 0-based
    pub id: usize,
    /// 1 human, 2 computer, 3 neutral, 4 rescuable
    pub kind: u32,
    /// 1 human, 2 orc, 3 undead, 4 night elf, 0 selectable
    pub race: u32,
}

#[derive(Debug, Clone)]
pub struct MapFacts {
    /// The map as the game names it in a lobby, e.g. `maps\Folder\Map.w3x`
    pub path_in_game: String,
    pub size: u32,
    pub crc32: u32,
    pub sha1: [u8; 20],
    /// The checksum the client compares when it joins
    pub xoro: u32,
    /// The slot table's layout byte: 1 custom forces, 2 fixed player settings
    pub layout: u8,
    pub players: Vec<MapPlayer>,
    /// Each force's players, as a bit per slot
    pub forces: Vec<u32>,
}

impl MapFacts {
    pub fn load(file: &Path, path_in_game: Option<&str>) -> Result<Self> {
        let path_in_game = match path_in_game {
            Some(path) => path.to_string(),
            None => path_under_maps(file).ok_or("the map is not under a Maps folder; give its path in the game")?,
        };
        let (map, checksum) = W3Map::open_with_checksum(file)?;
        let mut archive = Archive::open(file, OpenArchiveFlags::STREAM_FLAG_READ_ONLY)?;
        let info = MapInfo::decode(&mut read(&mut archive, "war3map.w3i").ok_or("no war3map.w3i")?.as_slice())
            .map_err(|e| format!("war3map.w3i: {:?}", e))?;
        let players = match (&info.players_reforged, &info.players_classic) {
            (Some(players), _) => players.iter().map(|p| MapPlayer { id: p.id as usize, kind: p.type_, race: p.race }).collect(),
            (None, Some(players)) => players.iter().map(|p| MapPlayer { id: p.id as usize, kind: p.type_, race: p.race }).collect(),
            _ => vec![],
        };
        Ok(MapFacts {
            path_in_game,
            size: map.file_size() as u32,
            crc32: checksum.crc32,
            sha1: checksum.sha1,
            xoro: xoro(&mut archive)?,
            layout: layout(map.flags()),
            players,
            forces: info.forces.iter().map(|f| f.player_set).collect(),
        })
    }

    /// The force (team) a slot belongs to; the first one when the map does not say.
    pub fn team_of(&self, slot: usize) -> u8 {
        self.forces.iter().position(|set| set & (1 << slot) != 0).unwrap_or(0) as u8
    }

    /// Whether the map sets the teams itself; without custom forces every player is on a team of
    /// their own, as in a melee game.
    pub fn custom_forces(&self) -> bool {
        self.layout & 1 != 0
    }

    /// The first slot the map does not define at all: where the host's seat goes when no slot is
    /// named, since the map's own code has no reason to look at it.
    pub fn free_slot(&self) -> Option<usize> {
        (0..crate::slots::SLOTS).find(|slot| self.players.iter().all(|p| p.id != *slot))
    }
}

/// `maps\<path below the Maps folder>` for a file somewhere under a folder called Maps.
pub fn path_under_maps(file: &Path) -> Option<String> {
    let parts: Vec<String> = file.iter().map(|p| p.to_string_lossy().to_string()).collect();
    let maps = parts.iter().rposition(|p| p.eq_ignore_ascii_case("maps"))?;
    Some(std::iter::once("maps".to_string()).chain(parts[maps + 1..].iter().cloned()).collect::<Vec<_>>().join("\\"))
}

fn read(archive: &mut Archive, name: &str) -> Option<Vec<u8>> {
    archive.open_file(name).ok().and_then(|mut f| f.read_all().ok()).filter(|b| !b.is_empty())
}

/// The map checksum the 3.0.0.24268 client compares. Flo's algorithm with the one file its list
/// lacks: the client's routine (0x100763470) also folds in war3map.w3l, Reforged's lighting
/// file, after w3q. A map without that file gets Flo's value; one with it does not.
fn xoro(archive: &mut Archive) -> Result<u32> {
    fn rol3(v: u32) -> u32 { v.rotate_left(3) }
    fn update(mut v: u32, bytes: &[u8]) -> u32 {
        let mut chunks = bytes.chunks_exact(4);
        for c in &mut chunks { v = rol3(v ^ u32::from_le_bytes([c[0], c[1], c[2], c[3]])); }
        for b in chunks.remainder() { v = rol3(v ^ *b as u32); }
        v
    }
    let script = ["war3map.j", "scripts\\war3map.j", "war3map.lua", "scripts\\war3map.lua"]
        .iter().find_map(|n| read(archive, n)).ok_or("the map has no script")?;
    let mut v = update(0, &script);
    for name in ["war3map.w3e", "war3map.wpm", "war3map.doo", "war3map.w3u", "war3map.w3b",
                 "war3map.w3d", "war3map.w3a", "war3map.w3q", "war3map.w3l"] {
        if let Some(bytes) = read(archive, name) {
            v = rol3(v ^ update(0, &bytes));
        }
    }
    Ok(v)
}

/// 1 for custom forces, plus 2 for fixed player settings. Flo's SlotLayout enum has no value for
/// both together, which a map like Warcraft Maul needs, so the byte is written directly.
fn layout(flags: MapFlags) -> u8 {
    let mut layout = 0;
    if flags.contains(MapFlags::CUSTOM_FORCES) { layout |= 1; }
    if flags.contains(MapFlags::FIXED_PLAYER_SETTINGS) { layout |= 2; }
    layout
}

/// Every file of interest to a join, and its size, for looking a map over.
pub fn list_files(file: &Path) -> Result<Vec<(String, u64)>> {
    let mut archive = Archive::open(file, OpenArchiveFlags::STREAM_FLAG_READ_ONLY)?;
    let mut out = vec![];
    for name in ["war3map.j", "war3map.lua", "war3map.w3e", "war3map.wpm", "war3map.doo", "war3map.w3u",
                 "war3map.w3b", "war3map.w3d", "war3map.w3a", "war3map.w3q", "war3map.w3l", "war3map.w3t",
                 "war3map.w3h", "war3map.w3i", "war3map.w3r", "war3map.w3c", "war3map.imp", "war3mapUnits.doo",
                 "war3map.wts", "war3map.w3s", "war3mapSkin.w3u", "war3mapSkin.w3t", "war3mapSkin.w3a"] {
        if let Ok(mut f) = archive.open_file(name) {
            out.push((name.to_string(), f.get_size().unwrap_or(0)));
        }
    }
    Ok(out)
}
