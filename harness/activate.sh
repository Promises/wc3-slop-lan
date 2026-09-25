#!/usr/bin/env bash
# Switches a running Warcraft III client to its real LAN provider (TCPN), the way W3Champions
# does it: a one-shot breakpoint in the game's InitializeLocalNetProvider handler changes the
# provider it is about to select from 'LOOP' to 'TCPN', then the debugger detaches. No code is
# written into the game. The game then opens its LAN socket (the first free UDP port from
# 16000) and finds LAN games the classic way. It lasts until the next provider switch - leaving
# a LAN lobby or the menus re-initialising - so run it again before each search.
#
#   bash harness/activate.sh <instance> [pid]
#
# The handler only runs when the game is told to initialise its provider, which comes from a
# page on the game's web UI socket: WC3_SERVER is the server that page answers to (default
# http://127.0.0.1:8777, Warcraft Maul's harness/webui/server.py). The instance is the
# number that server gave the game when its page checked in; the game's process is found from
# it (the one listening on the instance's web UI port), so the pid is only needed to override.
#
# Build-specific: refuses anything but Warcraft III 3.0.0.24268 with the expected bytes.
set -euo pipefail
server="${WC3_SERVER:-http://127.0.0.1:8777}"
instance="${1:?usage: activate.sh <instance> [pid]}"
pid="${2:-}"
if [[ -z "$pid" ]]; then
  # The instance's id is its game's web UI port; the game is whatever listens there
  port=$(curl -sf "$server/instances" | python3 -c '
import json, sys
print(next((k for k, v in json.load(sys.stdin).items() if str(v["number"]) == sys.argv[1]), ""))' "$instance") \
    || { echo "no web UI server at $server" >&2; exit 1; }
  [[ -n "$port" ]] || { echo "no instance $instance at $server (see: curl $server/instances)" >&2; exit 1; }
  pid=$(lsof -ti "tcp:$port" -sTCP:LISTEN | head -1)
  [[ -n "$pid" ]] || { echo "instance $instance's game (port $port) is gone" >&2; exit 1; }
fi

# The harness passes the game from configuration.toml; by hand, the standard macOS install
GAME="${WC3_GAME:-/Applications/Warcraft III/_retail_/x86_64/Warcraft III.app/Contents/MacOS/Warcraft III}"
UUID="C26F6D81-E702-3F44-B57E-91DD7C438B22"
SITE=0x100d3c6a2        # call SelectNetProvider; the mov edi,'LOOP' is the 5 bytes before
if ! dwarfdump --uuid "$GAME" | grep -q "$UUID"; then
  echo "this is not the game build these addresses were taken from; refusing" >&2
  exit 1
fi
python3 - "$GAME" <<'PY' || exit 1
import sys
data = open(sys.argv[1], 'rb').read()
if data[0xd3c69d:0xd3c69d + 10] != bytes.fromhex('bf504f4f4ce8092b4f00'):
    sys.exit('the provider handler bytes differ from what was verified; refusing')
PY

script=$(mktemp -t slop-activate)
cat > "$script" <<LLDB
breakpoint set --shlib "Warcraft III" --address $SITE --one-shot true --condition '(unsigned)\$rdi == 0x4c4f4f50'
continue
register write rdi 0x5443504e
process detach
LLDB
log=$(mktemp -t slop-activate-log)
lldb --batch -p "$pid" -s "$script" > "$log" 2>&1 &
debugger=$!

# Attaching takes a few seconds; only then can the handler run and hit the breakpoint
for _ in $(seq 60); do grep -q "^(lldb) continue" "$log" && break; sleep 0.5; done
order=$(printf '{"to":"%s","verb":"raw","message":"InitializeLocalNetProvider","payload":{}}' "$instance")
if ! curl -sf -X POST "$server/command" -d "$order" > /dev/null; then
  echo "no web UI server at $server to trigger the provider switch" >&2
  kill "$debugger" 2>/dev/null || true
  exit 1
fi
for _ in $(seq 40); do grep -q "detached" "$log" && break; sleep 0.5; done
wait "$debugger" 2>/dev/null || true

if grep -q "detached" "$log"; then
  ports=$(lsof -a -nP -p "$pid" -iUDP 2>/dev/null | awk 'NR>1 {print $9}' | grep -o ':1[67][0-9][0-9][0-9]' | tr '\n' ' ')
  echo "game $pid switched to its LAN provider; LAN port ${ports:-not open yet}"
else
  echo "activation did not complete; lldb said:" >&2
  grep -v '^warning' "$log" | tail -8 >&2
  exit 1
fi
rm -f "$script" "$log"
