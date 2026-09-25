#!/usr/bin/env bash
# Records the loopback traffic between W3Champions and the game, so what their client sends
# over the game's web UI socket can be read back.
#
# They connect to the same socket our own page uses (their binary carries "/webui-socket/" and
# links a websocket client), but from their own process, so our page's recorder never sees it.
# A packet capture does, and loopback is the only place this traffic ever goes.
#
# Needs root for the capture device, so run it yourself:
#
#   sudo bash diagnostics/capture-tcp.sh [seconds]
#
# Then start W3Champions, let it launch the game and get into a match. Decode with
#   python3 diagnostics/tcp-decode.py captures/w3c-tcp.pcap
set -euo pipefail

seconds="${1:-180}"
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out="${WC3_CAPTURE:-$root/captures/w3c-tcp.pcap}"
mkdir -p "$(dirname "$out")"

echo "Recording loopback for ${seconds}s into $out"
echo "Start W3Champions now, let it launch the game, and get into a match."
# Everything on loopback: the game's port is picked at launch, so it cannot be named here.
# The decoder picks out the streams that carry a web UI socket handshake.
timeout "$seconds" tcpdump -i lo0 -s 0 -w "$out" tcp 2>&1 | tail -2 || true

# The file is owned by root after this; hand it back so it can be read without sudo
if [[ -n "${SUDO_USER:-}" ]]; then
  chown "$SUDO_USER" "$out"
fi
echo "Done: $out"
ls -la "$out"
