//! What the activator looks for in the game, as bytes: the provider selector it rewrites, the
//! provider factory that shows TCPN is still supported, and the menus' address.

/// A byte pattern with wildcards, written as hex ("48 83 EC 58 E8 ?? ...").
pub fn pattern(text: &str) -> Vec<Option<u8>> {
    text.split_whitespace()
        .map(|b| if b == "??" { None } else { Some(u8::from_str_radix(b, 16).expect("hex byte")) })
        .collect()
}

/// Every place the pattern matches in `bytes`.
pub fn find_all(bytes: &[u8], pattern: &[Option<u8>]) -> Vec<usize> {
    if bytes.len() < pattern.len() {
        return vec![];
    }
    (0..=bytes.len() - pattern.len())
        .filter(|&i| pattern.iter().enumerate().all(|(k, p)| p.map_or(true, |b| bytes[i + k] == b)))
        .collect()
}

/// A provider id as the game spells it; the operand's bytes are little-endian.
pub fn name(operand: [u8; 4]) -> String {
    operand.iter().rev().map(|&b| b as char).collect()
}

pub const LOOP: [u8; 4] = *b"POOL";
pub const TCPN: [u8; 4] = *b"NPCT";

/// Game builds these patterns were checked on (file version of Warcraft III.exe).
pub const KNOWN_BUILDS: &[&str] = &["3.0.0.24268"];

/// The provider selector, in the handler behind InitializeLocalNetProvider: `sub rsp,0x58;
/// call ..; mov ecx,'LOOP'; call ..; call ..; lea rcx,..; mov qword [rsp+0x28],4`. The operand to
/// rewrite is 10 bytes in. (W3Champions' launcher rewrites the same instruction.)
pub const SELECTOR: &str =
    "48 83 EC 58 E8 ?? ?? ?? ?? B9 ?? ?? ?? ?? E8 ?? ?? ?? ?? E8 ?? ?? ?? ?? 48 8D 0D ?? ?? ?? ?? 48 C7 44 24 28 04 00 00 00";
pub const SELECTOR_OPERAND: usize = 10;

/// The provider factory: `cmp ebx,'BNET'; je ..; cmp ebx,'LOOP'; je ..; cmp ebx,'TCPN'; jne ..` -
/// evidence that this build still makes a TCPN provider when asked for one.
pub const FACTORY: &str = "81 FB 54 45 4E 42 74 ?? 81 FB 50 4F 4F 4C 74 ?? 81 FB 4E 50 43 54 0F 85";

/// Selectors in `bytes` (offsets of their operands) with what each selects now: LOOP as the
/// game has it, or TCPN if it was left switched.
pub fn selectors(bytes: &[u8]) -> Vec<(usize, [u8; 4])> {
    let selector = pattern(SELECTOR);
    find_all(bytes, &selector)
        .into_iter()
        .filter_map(|at| {
            let operand: [u8; 4] = bytes[at + SELECTOR_OPERAND..at + SELECTOR_OPERAND + 4].try_into().unwrap();
            (operand == LOOP || operand == TCPN).then_some((at + SELECTOR_OPERAND, operand))
        })
        .collect()
}

/// The menus' address as the game writes it into its memory,
/// `127.0.0.1:<port>/webui/index.html?guid=<guid>`: every one found in `bytes`. Memory also holds
/// cut-off copies (`guid=141...`), so the caller takes the longest guid (see [`best_address`]).
pub fn menus_addresses(bytes: &[u8]) -> Vec<(u16, String)> {
    const HEAD: &[u8] = b"127.0.0.1:";
    const MIDDLE: &[u8] = b"/webui/index.html?guid=";
    let digits = |from: usize| bytes[from..].iter().take_while(|b| b.is_ascii_digit()).count();
    let mut found = vec![];
    let mut at = 0;
    while let Some(offset) = bytes[at..].windows(HEAD.len()).position(|w| w == HEAD) {
        let start = at + offset + HEAD.len();
        at = start;
        let middle = start + digits(start);
        if middle == start || !bytes[middle..].starts_with(MIDDLE) {
            continue;
        }
        let guid_start = middle + MIDDLE.len();
        let guid_end = guid_start + digits(guid_start);
        let port = std::str::from_utf8(&bytes[start..middle]).ok().and_then(|p| p.parse().ok());
        if let (Some(port), true) = (port, guid_end > guid_start) {
            found.push((port, String::from_utf8_lossy(&bytes[guid_start..guid_end]).into_owned()));
        }
    }
    found
}

/// The address with the longest guid: the whole one, not a cut-off copy.
pub fn best_address(found: impl IntoIterator<Item = (u16, String)>) -> Option<(u16, String)> {
    found.into_iter().max_by_key(|(_, guid)| guid.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    const GAME_SELECTOR: [u8; 41] = [
        0x48, 0x83, 0xEC, 0x58, 0xE8, 1, 2, 3, 4, 0xB9, b'P', b'O', b'O', b'L', 0xE8, 5, 6, 7, 8, 0xE8, 9, 9, 9, 9, 0x48, 0x8D,
        0x0D, 1, 1, 1, 1, 0x48, 0xC7, 0x44, 0x24, 0x28, 4, 0, 0, 0, 0x90,
    ];

    #[test]
    fn finds_the_selector_and_what_it_selects() {
        let mut code = vec![0x90];
        code.extend_from_slice(&GAME_SELECTOR);
        assert_eq!(selectors(&code), vec![(1 + SELECTOR_OPERAND, LOOP)]);
        code[1 + SELECTOR_OPERAND..1 + SELECTOR_OPERAND + 4].copy_from_slice(&TCPN);
        assert_eq!(selectors(&code), vec![(1 + SELECTOR_OPERAND, TCPN)]);
        code[1 + SELECTOR_OPERAND] = b'X';
        assert!(selectors(&code).is_empty());
    }

    #[test]
    fn factory_pattern_matches_the_game() {
        // As the running game has it (3.0.0.24268)
        let code = [
            0x81, 0xFB, 0x54, 0x45, 0x4E, 0x42, 0x74, 0x3A, 0x81, 0xFB, 0x50, 0x4F, 0x4F, 0x4C, 0x74, 0x1F, 0x81, 0xFB, 0x4E,
            0x50, 0x43, 0x54, 0x0F, 0x85, 0x9B, 0, 0, 0,
        ];
        assert_eq!(find_all(&code, &pattern(FACTORY)), vec![0]);
    }

    #[test]
    fn names_read_as_the_game_spells_them() {
        assert_eq!(name(LOOP), "LOOP");
        assert_eq!(name(TCPN), "TCPN");
    }

    #[test]
    fn menus_address_takes_the_longest_guid() {
        let memory = b"xx127.0.0.1:41899/webui/index.html?guid=141\xff\x01yy\
            127.0.0.1:41899/webui/index.html?guid=14165155096931006943\0zz127.0.0.1:9/other";
        let found = menus_addresses(memory);
        assert_eq!(found.len(), 2);
        assert_eq!(best_address(found), Some((41899, "14165155096931006943".to_string())));
    }
}
