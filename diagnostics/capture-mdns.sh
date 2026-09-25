#!/usr/bin/env bash
# Records the Bonjour traffic while W3Champions advertises a game, so the exact service
# subtype and game record the real client accepts can be read back - it turned out the game
# does not list these; this is kept for checking that again on a new game build.
#
#   sudo bash diagnostics/capture-mdns.sh [seconds]
#
# Then start W3Champions and a game against the computer, as for diagnostics/capture-tcp.sh.
# Decode with: python3 diagnostics/mdns-decode.py captures/mdns.pcap
set -euo pipefail
seconds="${1:-120}"
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out="${WC3_CAPTURE:-$root/captures/mdns.pcap}"
mkdir -p "$(dirname "$out")"
echo "Recording Bonjour for ${seconds}s into $out - start a W3Champions game now."
# Every interface: the advertisement goes out on loopback and the LAN at once
timeout "$seconds" tcpdump -i any -s 0 -w "$out" udp port 5353 2>&1 | tail -2 || true
[[ -n "${SUDO_USER:-}" ]] && chown "$SUDO_USER" "$out"
ls -la "$out"
