#!/usr/bin/env bash
# Hand control of the running games. The management server (harness/webui/server.py) holds
# a queue per instance and the page inside each game picks its orders up; instances are
# numbered in the order they check in, and "all" reaches every one of them.
#
#   harness/wc3.sh who                              what is connected and where it is
#   harness/wc3.sh host 1 <map.w3x> [folder] [name] [password]   a lobby others can join
#   harness/wc3.sh solo 1 <map.w3x> [folder]        a single player lobby (local provider)
#   harness/wc3.sh join 2 <name> [password]
#   harness/wc3.sh lanjoin all <name>               join a LAN game (the local host)
#   harness/wc3.sh start 1
#   harness/wc3.sh leave 2
#   harness/wc3.sh raw 1 GetMapList '{"useLastMap":true}'        any message the menus send
#   harness/wc3.sh eval 1 'window.slop.sent.length'            any code, answered on the log
#   harness/wc3.sh reload 1                         re-read the page after editing it
#   harness/wc3.sh trace 1 [quiet|all] [chars]      how much traffic is recorded
#   harness/wc3.sh cmd 1 -log                       run a chat command in that game
#   harness/wc3.sh log 1 [lines] [wide]             what that instance last saw
#   harness/wc3.sh ask 1 GetMapList '{}' [lines]    send one, then show what came back
#   harness/wc3.sh reset                            forget the instances that checked in
set -euo pipefail
SERVER="${WC3_SERVER:-http://127.0.0.1:8777}"
MAPS="${WC3_MAP_DIR:-$HOME/Library/Application Support/Blizzard/Warcraft III/Maps/}"

# Every order is the same POST; the arguments are name=value pairs, JSON for anything that is
# not a plain string (payload=...), so no quoting games in each branch below.
command_post() {
    python3 - "$SERVER" "$@" <<'PY'
import json, sys, urllib.error, urllib.request
server, pairs = sys.argv[1], sys.argv[2:]
order = {}
for pair in pairs:
    key, _, value = pair.partition('=')
    if key.endswith(':'):
        key, value = key[:-1], json.loads(value)
    order[key] = value
try:
    request = urllib.request.Request(server + '/command', data=json.dumps(order).encode())
    print(urllib.request.urlopen(request, timeout=5).read().decode().strip())
except urllib.error.URLError as error:
    sys.exit('no server at %s (%s)' % (server, error))
PY
}

show_log() {
    python3 - "$SERVER" "$1" "$2" "${3:-}" <<'PY'
import json, sys, urllib.request
server, who, lines, width = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
url = '%s/log?id=%s&n=%s' % (server, who, lines)
for line in json.load(urllib.request.urlopen(url, timeout=5)):
    print(line if width == 'wide' else line[:200])
PY
}

# Where an instance keeps its files. Each game process carries its own data root in HOME
# (the alt client sets it), and CustomMapData under it is the one place a running game can be
# reached from outside: it reads command files from there itself, so nothing has to be typed
# and no window has to be focused.
data_root_of() {
    python3 - "$SERVER" "$1" <<'PYROOT'
import json, os, subprocess, sys, urllib.request
server, who = sys.argv[1], sys.argv[2]
instances = json.load(urllib.request.urlopen(server + '/instances', timeout=5))
port = who if who in instances else next(
    (key for key, value in instances.items() if str(value['number']) == str(who)), None)
if port is None:
    sys.exit('no instance %s' % who)
listeners = subprocess.run(['lsof', '-ti', 'tcp:%s' % port, '-sTCP:LISTEN'],
                           capture_output=True, text=True).stdout.split()
if not listeners:
    sys.exit('nothing listens on %s any more' % port)
environment = subprocess.run(['ps', 'eww', '-p', listeners[0]],
                             capture_output=True, text=True).stdout
home = next((word[len('HOME='):] for word in environment.split()
             if word.startswith('HOME=')), os.path.expanduser('~'))
print(home + '/Library/Application Support/Blizzard/Warcraft III/CustomMapData')
PYROOT
}

verb="${1:-who}"
case "$verb" in
  who)
    curl -s "$SERVER/instances" | python3 -m json.tool
    ;;
  host|solo)
    to="${2:-1}"; map="${3:?map file}"; folder="${4:-}"; name="${5:-devgame}"; pass="${6:-devtest}"
    local_flag=false; [ "$verb" = solo ] && local_flag=true
    command_post to="$to" verb=host map="$map" folder="$folder" directory="$MAPS" \
                 gameName="$name" password="$pass" "local:=$local_flag"
    ;;
  join)
    # With no name, the server fills in whatever was hosted last
    to="${2:-2}"; name="${3:-}"; pass="${4:-}"
    command_post to="$to" verb=join gameName="$name" password="$pass"
    ;;
  lanjoin)
    # Join a LAN game by name, e.g. one run by the wc3-slop-lan host
    command_post to="${2:-all}" verb=lanjoin gameName="${3:?game name}" "keepProvider:=${4:-false}"
    ;;
  start|leave|reload)
    command_post to="${2:-all}" verb="$verb"
    ;;
  raw)
    to="${2:?instance}"; message="${3:?message}"
    command_post to="$to" verb=raw message="$message" "payload:=${4:-{\}}"
    ;;
  eval)
    to="${2:?instance}"; code="${3:?code}"
    command_post to="$to" verb=eval code="$code" > /dev/null
    sleep 2
    show_log "$to" 6 wide
    ;;
  trace)
    to="${2:-all}"; level="${3:-all}"; room="${4:-1500}"
    quiet=false; [ "$level" = quiet ] && quiet=true
    command_post to="$to" verb=trace "quiet:=$quiet" "room:=$room"
    ;;
  cmd)
    to="${2:?instance}"; shift 2
    root=$(data_root_of "$to")
    python3 - "$root" "$*" <<'PYCMD'
import pathlib, re, sys, time
root, line = pathlib.Path(sys.argv[1]), sys.argv[2]
root.mkdir(parents=True, exist_ok=True)

# Where the game has got to: it asks for one name per poll and never asks twice, because a
# name asked for while the file is missing is remembered as missing. The heartbeat in the
# lockstep trace says which name it is on.
polling = 0
beat = root / 'slop-beat.txt'
if beat.exists():
    for found in re.finditer(r'cmdpoll=(\d+)', beat.read_text(errors='replace')):
        polling = max(polling, int(found.group(1)))
if polling == 0:
    sys.exit('no running game to command: nothing has published a poll number yet')

# Aim a little ahead of it and fill a few names, so one is in place before it is asked for.
# They all carry the same id, and the game takes the first and ignores the rest.
AHEAD, SPREAD = 4, 8
command_id = int(time.time())
payload = 'CMD:%d:%s' % (command_id, line)
body = (b'function PreloadFiles takes nothing returns nothing\n\r\n'
        b'\tcall PreloadStart()\r\n'
        b'\tcall Preload( "")\ncall BlzSetAbilityTooltip(\'ANcl\', "' + payload.encode()
        + b'", 0)\n//" )\r\n'
        b'\tcall PreloadEnd( 0.0 )\r\n\nendfunction\n\n\r\n')
for number in range(polling + AHEAD, polling + AHEAD + SPREAD):
    (root / ('slop-cmd-%04d.txt' % number)).write_bytes(body)

# Names already passed are dead weight
for old in root.glob('slop-cmd-*.txt'):
    if int(old.stem.split('-')[-1]) < polling:
        old.unlink()

print('%s  ->  names %d-%d (it is asking for %d)'
      % (line, polling + AHEAD, polling + AHEAD + SPREAD - 1, polling))
PYCMD
    ;;
  log)
    show_log "${2:-1}" "${3:-30}" "${4:-}"
    ;;
  ask)
    to="${2:?instance}"; message="${3:?message}"
    command_post to="$to" verb=raw message="$message" "payload:=${4:-{\}}" > /dev/null
    sleep 3
    show_log "$to" "${5:-15}" wide
    ;;
  reset)
    curl -s -X POST "$SERVER/reset" -o /dev/null && echo 'instances cleared'
    ;;
  *)
    /usr/bin/sed -n '2,20p' "$0"
    exit 1
    ;;
esac
