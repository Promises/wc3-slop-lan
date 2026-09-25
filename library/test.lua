-- Runs slop.lua outside the game, against stand-ins for the natives it calls, and checks what it
-- writes. `lua library/test.lua` from the repo root; exits non-zero on the first failure.
local here = arg[0]:match('^(.*)/[^/]*$') or '.'

-- The game, as far as the library sees it --------------------------------------------------
local timers, files, generating, handles = {}, {}, nil, 1000
local syncTrigger, tooltip, preloaded = nil, 'Cloud', {}
local players, units = {}, {}
bj_MAX_PLAYERS, bj_MAX_PLAYER_SLOTS = 24, 28
PLAYER_SLOT_STATE_PLAYING, MAP_CONTROL_USER = 'playing', 'user'
PLAYER_STATE_RESOURCE_GOLD, PLAYER_STATE_RESOURCE_LUMBER = 'gold', 'lumber'

function Player(i)
    players[i] = players[i] or {id = i, gold = 500, lumber = 100, playing = i <= 1 or i == 17}
    return players[i]
end
function GetPlayerId(p) return p.id end
function GetLocalPlayer() return Player(1) end
function GetPlayerSlotState(p) return p.playing and 'playing' or 'empty' end
function GetPlayerController(p) return 'user' end
function GetPlayerState(p, which) return p[which] end
function SetPlayerState(p, which, value) p[which] = value end
function Location() handles = handles + 1; return {id = handles} end
function GetHandleId(h) return h.id end
function RemoveLocation() end
function CreateTimer() return {} end
function TimerStart(t, seconds, periodic, fn) timers[#timers + 1] = {seconds = seconds, fn = fn} end
function PreloadGenClear() generating = {} end
function PreloadGenStart() end
function Preload(line) generating[#generating + 1] = line end
function PreloadGenEnd(name) files[name] = generating end
function PreloadStart() end
function PreloadEnd() end
function Preloader(name) if preloaded[name] then tooltip = preloaded[name] end end
function BlzGetAbilityTooltip() return tooltip end
function CreateTrigger() return {events = {}} end
function BlzTriggerRegisterPlayerSyncEvent(t, p, prefix) t.events[#t.events + 1] = prefix; syncTrigger = t end
function TriggerAddAction(t, fn) t.action = fn end
local sync = {}
function GetTriggerPlayer() return sync.player end
function BlzGetTriggerSyncData() return sync.data end
function BlzGetTriggerSyncPrefix() return sync.prefix end
local sent = {}
function BlzSendSyncData(prefix, data) sent[#sent + 1] = {prefix = prefix, data = data} end
function CreateGroup() return {} end
function DestroyGroup() end
function GroupEnumUnitsOfPlayer(g, p) for _, u in ipairs(units) do if u.owner == p.id then g[#g + 1] = u end end end
function GroupEnumUnitsInRect(g) for _, u in ipairs(units) do g[#g + 1] = u end end
function GetPlayableMapRect() return {} end
local enum
function ForGroup(g, fn) for _, u in ipairs(g) do enum = u; fn() end end
function GetEnumUnit() return enum end
function GetUnitTypeId(u) return u.type end
function GetUnitX(u) return u.x end
function GetUnitY(u) return u.y end
function GetWidgetLife(u) return u.life end
function GetUnitCurrentOrder() return 0 end
function OrderId2String() return nil end
local orders = {}
function IssuePointOrder(u, name, x, y) orders[#orders + 1] = {u.id, name, x, y}; return true end
function IssueImmediateOrder(u, name) orders[#orders + 1] = {u.id, name}; return true end
function IssueTargetOrder(u, name, t) orders[#orders + 1] = {u.id, name, t.id}; return true end
local ended
function EndGame(scoreScreen) ended = scoreScreen end
function IssueBuildOrderById(u, id, x, y) orders[#orders + 1] = {u.id, 'build', id, x, y}; return true end

units[1] = {id = 2001, owner = 0, type = 0x68303030, x = 100.7, y = -50.2, life = 420.0}   -- h000

-- The map ----------------------------------------------------------------------------------
local mapStarted = false
function main() mapStarted = true end
SLOP_CONFIG = {seat = 17, prefix = 'slop'}
dofile(here .. '/slop.lua')

local failures = 0
local function check(what, ok)
    if not ok then
        failures = failures + 1
        print('FAIL ' .. what)
    end
end
local function run(seconds)
    for _, t in ipairs(timers) do
        for _ = 1, math.floor(seconds / t.seconds + 0.5) do t.fn() end
    end
end
local function traced()
    local all = {}
    for n = 1, 9999 do
        local chunk = files[string.format('slop-trace-p1-%04d.txt', n)]
        if not chunk then break end
        for _, line in ipairs(chunk) do all[#all + 1] = line end
    end
    return all
end
local function find(pattern)
    for _, line in ipairs(traced()) do
        if line:match(pattern) then return line end
    end
end
local function fromHost(data)
    sync = {player = Player(17), data = data, prefix = 'slop'}
    syncTrigger.action()
end

check('Slop is defined before main', Slop ~= nil and Slop.seat == 17)
main()
check("the map's main still runs", mapStarted)
check('it started after main, writing under the local player', find('slop started v1 seat=17 prefix=slop') ~= nil)

Slop.heartbeat(function() return 'wave=3' end)
Slop.playerFields(function(p) return 'k=' .. p end)
run(1)
local beat = find(' beat ')
check('a heartbeat was written: ' .. tostring(beat), beat and beat:match('wave=3') and beat:match('p0%(g=500 l=100 k=0%)')
    and beat:match('p1%(') and not beat:match('p17%('))

fromHost('0 .units')
check('.units lists the unit', find('unit p0 id=2001 type=h000 at=100,%-51 life=420 order=0') ~= nil)
check('.units ends the list', find('unit p0 end') ~= nil)

fromHost('0 .order 2001 move 300 400')
check('a point order was issued', orders[1] and orders[1][2] == 'move' and orders[1][3] == 300)
check('and traced', find('order p0 2001 move 300 400 issued') ~= nil)
fromHost('0 .build 2001 h001 64 128')
check('a build order was issued', orders[2] and orders[2][3] == 0x68303031)
fromHost('1 .order 2001 stop')
check("another player's unit refuses", find('order p1 2001 stop rejected') ~= nil)
fromHost('0 .gold 1717')
check('.gold sets gold', Player(0).gold == 1717)

local hooked
Slop.onCommand(function(p, line) if line == '-hello' then hooked = p return true end end)
fromHost('1 -hello')
check('a map hook takes its command', hooked == 1)
fromHost('1 -nothing')
check('an unknown command is noted', find('slop p1 unhandled') ~= nil)
Slop.onCommand(function() error('boom') end)
fromHost('0 -explode')
check('a failing hook is noted, not raised', find('command hook failed') ~= nil)

sync = {player = Player(1), data = '0 .gold 1', prefix = 'slop'}
syncTrigger.action()
check('host commands from anyone but the seat are ignored', Player(0).gold == 1717)

-- The file channel: the beat file names the poll, a command file is read and synced as self
run(1)
local poll = tonumber(files['slop-beat-p1.txt'][1]:match('cmdpoll=(%d+)'))
check('the beat file names the poll', poll and poll > 0)
preloaded[string.format('slop-cmd-p1-%04d.txt', poll + 1)] = 'CMD:42:.lumber 99'
run(0.5)
check('a command file is synced as the reading player', sent[1] and sent[1].prefix == 'slopself' and sent[1].data == '.lumber 99')
sync = {player = Player(1), data = sent[1] and sent[1].data, prefix = 'slopself'}
syncTrigger.action()
check('and runs as that player', Player(1).lumber == 99)
run(2)
check('the same command runs once', #sent == 1)

fromHost('1 .end')
check('.end ends the game with a score screen, after writing the trace', ended == true and find('slop p1 ends the game') ~= nil)

local lines = traced()
for n = 2, #lines do
    check('the trace is in order', tonumber(lines[n]:match('^%d+')) == tonumber(lines[n - 1]:match('^%d+')) + 1)
end
print(failures == 0 and ('ok: ' .. #lines .. ' trace lines') or (failures .. ' failed'))
os.exit(failures == 0 and 0 or 1)
