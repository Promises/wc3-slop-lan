--[[
wc3-slop-lan's map library: lets a test drive a running Lua map from outside and read it back.

The harness appends this to a staged copy of the map's script (`wc3-slop-lan inject`); no build
of the map has to contain it. A map that wants more than the built-ins registers hooks, guarded
by `if Slop then ... end`, and costs nothing when it is absent. See docs/library.md.

  In:  commands from the host's seat (sync prefix `Slop.prefix`, data "<player> <line>"), or from
       a file dropped into a client's CustomMapData (see Slop.files) - run as that player on
       every client, so the game stays in step.
  Out: the lockstep trace, numbered chunks <trace>-p<slot>-NNNN.txt in CustomMapData: a
       heartbeat each second, the events the map notes, and what commands did. Every client
       writes the same lines while they agree.

Every file carries the local player's slot in its name, so two clients' files are easy to tell
apart (each needs a user folder of its own anyway: two games on one break real maps). File names
are local to each client; only what goes into the files has to be the same everywhere.

Everything here must run the same on every client: no GetLocalPlayer, no wall clock, no UI.
]]
do
    local config = SLOP_CONFIG or {}

    Slop = {
        version = 1,
        -- The host's seat, a slot no client plays; nil when the host is hidden
        seat = config.seat,
        prefix = config.prefix or 'slop',
        files = config.files ~= false,
    }

    local TRACE = config.trace or 'slop-trace'
    local BEAT = config.beat or 'slop-beat'
    local COMMAND_FILE = config.commands or 'slop-cmd'
    -- '-p<slot>' for the player this client plays, set at start: it names files only, never
    -- anything the game does, so it may differ between clients
    local me = ''
    -- Commands a client read from its own folder, sent as its own player
    local SELF_PREFIX = Slop.prefix .. 'self'
    -- A flush writes at most this many lines, which bounds the stall it causes
    local CHUNK_LINES = 400
    -- A cap so a long game cannot fill the folder; a heartbeat-only game takes hours to reach it
    local MAX_CHUNKS = 2000
    local TICK_SECONDS = 0.1
    local POLL_SECONDS = 0.5
    -- The ability tooltip a command file sets and the poll reads back
    local CARRIER_ABILITY = 'ANcl'

    local pending = {}
    local sequence, ticks, chunk, attempt, lastFileCommand = 0, 0, 0, 0, 0
    local started = false
    local urgent = {slop = true, unit = true, order = true}
    local commandHooks, beatHooks, playerHooks = {}, {}, {}

    local function fourcc(s)
        return ((s:byte(1) * 256 + s:byte(2)) * 256 + s:byte(3)) * 256 + s:byte(4)
    end

    local function decodeFourcc(id)
        return string.char((id >> 24) & 255, (id >> 16) & 255, (id >> 8) & 255, id & 255)
    end

    --- The id a new handle would get: roughly how many handles this client has made. A hint when
    --- hunting a desync, not proof of one: local-only code (UI frames, effects one player sees)
    --- makes and frees handles on one client only, so clients in step can differ here.
    function Slop.handleMark()
        local probe = Location(0, 0)
        local mark = GetHandleId(probe)
        RemoveLocation(probe)
        return mark
    end

    --- Writes what was noted since the last flush as the next chunk.
    function Slop.flush()
        if #pending == 0 or chunk >= MAX_CHUNKS then
            return
        end
        chunk = chunk + 1
        PreloadGenClear()
        PreloadGenStart()
        for _, line in ipairs(pending) do
            -- The game writes each line inside a quoted string, so quotes have to go
            Preload((line:gsub('["\r\n]', "'")))
        end
        PreloadGenEnd(string.format('%s%s-%04d.txt', TRACE, me, chunk))
        pending = {}
    end

    --- One traced event. Call it only from code that runs on every client alike.
    function Slop.note(category, text)
        if not started then
            return
        end
        sequence = sequence + 1
        pending[#pending + 1] = string.format('%d t%d h%d %s %s', sequence, ticks, Slop.handleMark(), category, text)
        if #pending >= CHUNK_LINES or urgent[category] then
            Slop.flush()
        end
    end

    --- Categories written the moment they are noted rather than on the next flush: a client
    --- that diverges is dropped within the second, and its last lines are the ones that matter.
    function Slop.urgent(...)
        for _, category in ipairs({...}) do
            urgent[category] = true
        end
    end

    --- hook(player, line) -> true when it handled the line. Tried in order after the built-ins.
    function Slop.onCommand(hook)
        commandHooks[#commandHooks + 1] = hook
    end

    --- hook() -> "key=value ..." added to every heartbeat.
    function Slop.heartbeat(hook)
        beatHooks[#beatHooks + 1] = hook
    end

    --- hook(player) -> "key=value ..." added to that player's part of the heartbeat.
    function Slop.playerFields(hook)
        playerHooks[#playerHooks + 1] = hook
    end

    local function safely(what, fn, ...)
        local ok, result = pcall(fn, ...)
        if not ok then
            Slop.note('slop', what .. ' failed: ' .. tostring(result))
            return nil
        end
        return result
    end

    -- The players a test can act as: slots in use by a person, not the host's seat
    local function isPlayer(index)
        return index ~= Slop.seat
            and GetPlayerSlotState(Player(index)) == PLAYER_SLOT_STATE_PLAYING
            and GetPlayerController(Player(index)) == MAP_CONTROL_USER
    end

    local function heartbeat()
        local parts = {'handles=' .. Slop.handleMark(), 'cmdpoll=' .. attempt}
        for _, hook in ipairs(beatHooks) do
            parts[#parts + 1] = safely('heartbeat hook', hook)
        end
        for index = 0, bj_MAX_PLAYERS - 1 do
            if isPlayer(index) then
                local p = Player(index)
                local fields = {'g=' .. GetPlayerState(p, PLAYER_STATE_RESOURCE_GOLD),
                                'l=' .. GetPlayerState(p, PLAYER_STATE_RESOURCE_LUMBER),
                                'fu=' .. GetPlayerState(p, PLAYER_STATE_RESOURCE_FOOD_USED),
                                'fc=' .. GetPlayerState(p, PLAYER_STATE_RESOURCE_FOOD_CAP)}
                for _, hook in ipairs(playerHooks) do
                    fields[#fields + 1] = safely('player hook', hook, index)
                end
                parts[#parts + 1] = 'p' .. index .. '(' .. table.concat(fields, ' ') .. ')'
            end
        end
        Slop.note('beat', table.concat(parts, ' '))
    end

    -- Built-in commands -----------------------------------------------------------------------
    -- Named with a leading dot so they cannot shadow a map's own chat commands.

    local function eachUnit(group, fn)
        local found = {}
        ForGroup(group, function() found[#found + 1] = GetEnumUnit() end)
        DestroyGroup(group)
        for _, u in ipairs(found) do
            fn(u)
        end
    end

    local function unitsOf(index)
        local group = CreateGroup()
        GroupEnumUnitsOfPlayer(group, Player(index), nil)
        return group
    end

    -- Units are named by ref, not handle id. A handle id is local: clients hand them out and
    -- reuse them differently (local-only objects such as UI frames take ids on one client and
    -- not the other), so the same unit can have a different id on each client, and a command
    -- naming a handle id reaches a different unit, or none, on the other. Refs are handed out in
    -- the order units first appear in commands and events, which every client runs alike.
    -- Keyed by the unit itself, as Lua maps do: a unit is one Lua object while anything holds
    -- it, and these tables hold every unit given a ref
    local refs, byRef, nextRef = {}, {}, 0

    --- The unit's ref: the same number on every client.
    function Slop.ref(u)
        local ref = refs[u]
        if ref == nil then
            nextRef = nextRef + 1
            ref = nextRef
            refs[u] = ref
            byRef[ref] = u
        end
        return ref
    end

    --- The unit with that ref, if it still exists; with an owner given, only that player's.
    local function unitByRef(ref, owner)
        local u = byRef[tonumber(ref) or -1]
        if u == nil or GetUnitTypeId(u) == 0 then
            return nil
        end
        if owner ~= nil and GetPlayerId(GetOwningPlayer(u)) ~= owner then
            return nil
        end
        return u
    end

    local builtins = {}

    -- .units: the player's units into the trace, one 'unit' line each, then "p<n> end"
    builtins['.units'] = function(index)
        eachUnit(unitsOf(index), function(u)
            local order = GetUnitCurrentOrder(u)
            Slop.note('unit', string.format('p%d id=%d type=%s at=%d,%d life=%d order=%s', index, Slop.ref(u),
                decodeFourcc(GetUnitTypeId(u)), math.floor(GetUnitX(u)), math.floor(GetUnitY(u)),
                math.floor(GetWidgetLife(u)), OrderId2String(order) or tostring(order)))
        end)
        Slop.note('unit', 'p' .. index .. ' end')
    end

    -- .order <unit> <order> [x y | target]: an order to one of the player's own units
    builtins['.order'] = function(index, words)
        local u = unitByRef(words[2], index)
        local name = words[3]
        local ok = false
        if u and name then
            if #words >= 5 then
                ok = IssuePointOrder(u, name, tonumber(words[4]), tonumber(words[5]))
            elseif #words == 4 then
                local target = unitByRef(words[4])
                ok = target ~= nil and IssueTargetOrder(u, name, target)
            else
                ok = IssueImmediateOrder(u, name)
            end
        end
        Slop.note('order', 'p' .. index .. ' ' .. table.concat(words, ' ', 2) .. (ok and ' issued' or ' rejected'))
    end

    -- .build <builder> <type> <x> <y>: a build order, the type as its four letters
    builtins['.build'] = function(index, words)
        local u = unitByRef(words[2], index)
        local ok = u ~= nil and words[3] ~= nil and #words[3] == 4 and #words >= 5
            and IssueBuildOrderById(u, fourcc(words[3]), tonumber(words[4]), tonumber(words[5]))
        Slop.note('order', 'p' .. index .. ' build ' .. table.concat(words, ' ', 3) .. (ok and ' issued' or ' rejected'))
    end

    -- .gold <n> / .lumber <n>: set the player's resources
    builtins['.gold'] = function(index, words)
        SetPlayerState(Player(index), PLAYER_STATE_RESOURCE_GOLD, math.tointeger(tonumber(words[2]) or 0) or 0)
        Slop.note('order', 'p' .. index .. ' gold ' .. tostring(words[2]))
    end
    builtins['.lumber'] = function(index, words)
        SetPlayerState(Player(index), PLAYER_STATE_RESOURCE_LUMBER, math.tointeger(tonumber(words[2]) or 0) or 0)
        Slop.note('order', 'p' .. index .. ' lumber ' .. tostring(words[2]))
    end

    -- .create <type> <x> <y> [count] [life=<n>] [frozen|rooted]: units of that type for the
    -- player, e.g. creeps for a test to point towers at, run as the creep player; one 'created'
    -- line each. Life and the rest are set in the same step, before any tower can shoot at them.
    -- frozen pauses a unit; rooted only stops it moving, and it stays an ordinary unit (some
    -- attacks, Burning Oil's among them, ignore paused units)
    builtins['.create'] = function(index, words)
        local count = math.max(1, math.tointeger(tonumber(words[5]) or 1) or 1)
        local life, frozen, rooted
        for i = 6, #words do
            life = life or math.tointeger(tonumber(words[i]:match('^life=(%d+)$') or ''))
            frozen = frozen or words[i] == 'frozen'
            rooted = rooted or words[i] == 'rooted'
        end
        for _ = 1, count do
            local u = CreateUnit(Player(index), fourcc(words[2]), tonumber(words[3]), tonumber(words[4]), 270)
            if u and life then
                BlzSetUnitMaxHP(u, life)
                SetWidgetLife(u, life)
            end
            if u and frozen then
                PauseUnit(u, true)
            end
            if u and rooted then
                SetUnitMoveSpeed(u, 0)
                SetUnitPropWindow(u, 0)
            end
            Slop.note('created', string.format('p%d id=%d type=%s at=%d,%d', index, u and Slop.ref(u) or 0,
                words[2], math.floor(tonumber(words[3])), math.floor(tonumber(words[4]))))
        end
    end

    -- .inspect <unit> [ability or buff ...]: what a unit is right now, any owner: its attack
    -- (base damage, dice, cooldown, range), armour, speed, life, mana, and the level of each
    -- ability or buff named (0 when it has none)
    builtins['.inspect'] = function(index, words)
        local u = unitByRef(words[2])
        if not u then
            Slop.note('inspect', 'id=' .. tostring(words[2]) .. ' missing')
            return
        end
        local order = GetUnitCurrentOrder(u)
        local parts = {string.format('id=%d type=%s owner=p%d at=%d,%d order=%s life=%d/%d mana=%d dmg=%d+%dd%d cd=%.2f range=%d armor=%d speed=%d',
            Slop.ref(u), decodeFourcc(GetUnitTypeId(u)), GetPlayerId(GetOwningPlayer(u)),
            math.floor(GetUnitX(u)), math.floor(GetUnitY(u)), OrderId2String(order) or tostring(order),
            math.floor(GetWidgetLife(u)), BlzGetUnitMaxHP(u), math.floor(GetUnitState(u, UNIT_STATE_MANA)),
            BlzGetUnitBaseDamage(u, 0), BlzGetUnitDiceNumber(u, 0), BlzGetUnitDiceSides(u, 0),
            BlzGetUnitAttackCooldown(u, 0), math.floor(BlzGetUnitWeaponRealField(u, UNIT_WEAPON_RF_ATTACK_RANGE, 0)),
            math.floor(BlzGetUnitArmor(u)), math.floor(GetUnitMoveSpeed(u)))}
        for i = 3, #words do
            parts[#parts + 1] = words[i] .. '=' .. GetUnitAbilityLevel(u, fourcc(words[i]))
        end
        Slop.note('inspect', table.concat(parts, ' '))
    end

    -- .watch: every hit on this player's units goes into the trace as a 'hit' line: source,
    -- target, amount, the amount before armor (raw), attack type, and whether it was an attack
    -- or a spell
    local attackTypes
    local watching = {}
    builtins['.watch'] = function(index)
        if watching[index] then
            return
        end
        watching[index] = true
        -- Named as the object editor names them; compared with ==, since the game may not hand
        -- back the very same object for the same attack type
        attackTypes = attackTypes or {
            {ATTACK_TYPE_MELEE, 'normal'}, {ATTACK_TYPE_PIERCE, 'pierce'}, {ATTACK_TYPE_SIEGE, 'siege'},
            {ATTACK_TYPE_MAGIC, 'magic'}, {ATTACK_TYPE_CHAOS, 'chaos'}, {ATTACK_TYPE_HERO, 'hero'},
            {ATTACK_TYPE_NORMAL, 'spells'},
        }
        -- The amount before armor, from the damaging event that comes first: registered after
        -- the map's own damage triggers, it is what the map made of the hit, before the target's
        -- armor and armor type had their say
        local raw = {}
        local before = CreateTrigger()
        TriggerRegisterPlayerUnitEvent(before, Player(index), EVENT_PLAYER_UNIT_DAMAGING, nil)
        TriggerAddAction(before, function()
            raw[BlzGetEventDamageTarget()] = GetEventDamage()
        end)
        local trigger = CreateTrigger()
        TriggerRegisterPlayerUnitEvent(trigger, Player(index), EVENT_PLAYER_UNIT_DAMAGED, nil)
        TriggerAddAction(trigger, function()
            local source, target = GetEventDamageSource(), BlzGetEventDamageTarget()
            local amount = raw[target] or GetEventDamage()
            raw[target] = nil
            local attackType, name = BlzGetEventAttackType(), '?'
            for _, known in ipairs(attackTypes) do
                if known[1] == attackType then
                    name = known[2]
                end
            end
            Slop.note('hit', string.format('src=%d srctype=%s dst=%d amount=%d raw=%d atk=%s attack=%s',
                source and Slop.ref(source) or 0, source and decodeFourcc(GetUnitTypeId(source)) or '----',
                target and Slop.ref(target) or 0, math.floor(GetEventDamage()), math.floor(amount),
                name, tostring(BlzGetEventIsAttack())))
        end)
        Slop.note('order', 'p' .. index .. ' watching hits')
    end

    -- .casts: from now on, every spell a unit of this player casts goes into the trace as a
    -- 'cast' line: caster, ability, and the unit it targets (0 for none)
    local castWatching = {}
    builtins['.casts'] = function(index)
        if castWatching[index] then
            return
        end
        castWatching[index] = true
        local trigger = CreateTrigger()
        TriggerRegisterPlayerUnitEvent(trigger, Player(index), EVENT_PLAYER_UNIT_SPELL_EFFECT, nil)
        TriggerAddAction(trigger, function()
            local caster, target = GetTriggerUnit(), GetSpellTargetUnit()
            Slop.note('cast', string.format('src=%d srctype=%s ability=%s dst=%d',
                Slop.ref(caster), decodeFourcc(GetUnitTypeId(caster)), decodeFourcc(GetSpellAbilityId()),
                target and Slop.ref(target) or 0))
        end)
        Slop.note('order', 'p' .. index .. ' watching casts')
    end

    -- .hp <unit> <life>: sets one of the player's units' maximum and current life
    builtins['.hp'] = function(index, words)
        local u = unitByRef(words[2], index)
        local life = math.tointeger(tonumber(words[3]) or 0) or 0
        if u and life > 0 then
            BlzSetUnitMaxHP(u, life)
            SetWidgetLife(u, life)
        end
        Slop.note('order', 'p' .. index .. ' hp ' .. tostring(words[2]) .. ' ' .. life .. (u and ' set' or ' rejected'))
    end

    -- .upgrade <unit> <type>: upgrades one of the player's buildings to that type (the
    -- upgrade's cost is charged as for a player)
    builtins['.upgrade'] = function(index, words)
        local u = unitByRef(words[2], index)
        local ok = u ~= nil and words[3] ~= nil and #words[3] == 4 and IssueImmediateOrderById(u, fourcc(words[3]))
        Slop.note('order', 'p' .. index .. ' upgrade ' .. tostring(words[2]) .. ' ' .. tostring(words[3])
            .. (ok and ' issued' or ' rejected'))
    end

    -- .foodcap <n>: sets the player's food cap
    builtins['.foodcap'] = function(index, words)
        SetPlayerState(Player(index), PLAYER_STATE_RESOURCE_FOOD_CAP, math.tointeger(tonumber(words[2]) or 0) or 0)
        Slop.note('order', 'p' .. index .. ' foodcap ' .. tostring(words[2]))
    end

    -- .freeze <unit>: pauses one of the player's units where it stands; it can still be hit
    builtins['.freeze'] = function(index, words)
        local u = unitByRef(words[2], index)
        if u then
            PauseUnit(u, true)
        end
        Slop.note('order', 'p' .. index .. ' freeze ' .. tostring(words[2]) .. (u and ' done' or ' rejected'))
    end

    -- .kill <unit>: kills one of the player's units
    builtins['.kill'] = function(index, words)
        local u = unitByRef(words[2], index)
        if u then
            KillUnit(u)
        end
        Slop.note('order', 'p' .. index .. ' kill ' .. tostring(words[2]) .. (u and ' done' or ' rejected'))
    end

    -- .remove <unit>: takes one of the player's units out of the game, with no death (nothing
    -- dies, so nothing reacts to a death)
    builtins['.remove'] = function(index, words)
        local u = unitByRef(words[2], index)
        if u then
            RemoveUnit(u)
        end
        Slop.note('order', 'p' .. index .. ' remove ' .. tostring(words[2]) .. (u and ' done' or ' rejected'))
    end

    -- .tech <tech> <level>: researches an upgrade for the player to that level
    builtins['.tech'] = function(index, words)
        SetPlayerTechResearched(Player(index), fourcc(words[2]), math.tointeger(tonumber(words[3]) or 1) or 1)
        Slop.note('order', 'p' .. index .. ' tech ' .. tostring(words[2]) .. ' ' .. tostring(words[3] or 1))
    end

    -- .end: ends the game for everyone, to the score screen. Every client runs this from the
    -- same sync event, so every client leaves at the same point and the next game can reuse them
    builtins['.end'] = function(index)
        Slop.note('slop', 'p' .. index .. ' ends the game')
        Slop.flush()
        EndGame(true)
    end

    --- Runs one command line as a player (0-based slot), on the client this is called on. Only
    --- call it from something every client runs alike, such as a sync event.
    function Slop.run(index, line)
        Slop.note('slop', 'p' .. index .. ' ' .. line)
        local words = {}
        for word in line:gmatch('%S+') do
            words[#words + 1] = word
        end
        local builtin = builtins[words[1] or '']
        if builtin then
            safely(words[1], builtin, index, words)
            return
        end
        for _, hook in ipairs(commandHooks) do
            if safely('command hook', hook, index, line) then
                return
            end
        end
        Slop.note('slop', 'p' .. index .. ' unhandled')
    end

    -- Channels ---------------------------------------------------------------------------------

    local function listen()
        local trigger = CreateTrigger()
        for index = 0, bj_MAX_PLAYER_SLOTS - 1 do
            BlzTriggerRegisterPlayerSyncEvent(trigger, Player(index), Slop.prefix, false)
            BlzTriggerRegisterPlayerSyncEvent(trigger, Player(index), SELF_PREFIX, false)
        end
        TriggerAddAction(trigger, function()
            local from = GetPlayerId(GetTriggerPlayer())
            local data = BlzGetTriggerSyncData() or ''
            if BlzGetTriggerSyncPrefix() == SELF_PREFIX then
                Slop.run(from, data)
            elseif from == Slop.seat then
                local index, line = data:match('^(%d+) (.*)$')
                if index then
                    Slop.run(tonumber(index), line)
                end
            else
                Slop.note('slop', 'ignored a host command from p' .. from .. ', which is not the seat')
            end
        end)
    end

    -- The file channel. Preloader runs a preload file; a command file sets an ability tooltip,
    -- which is read straight back. The read must sit in its own preload pass or it does nothing
    -- once the game is on, and a name asked for while it does not exist is remembered as
    -- missing for good - so each poll asks for the next name and never asks twice, and the
    -- writer aims just ahead of the number the beat file announces.
    local function poll()
        attempt = attempt + 1
        local name = string.format('%s%s-%04d.txt', COMMAND_FILE, me, attempt)
        local ok, value = pcall(function()
            PreloadStart()
            Preloader(name)
            PreloadEnd(1)
            return BlzGetAbilityTooltip(fourcc(CARRIER_ABILITY), 0) or ''
        end)
        if not ok or type(value) ~= 'string' then
            return
        end
        local id, line = value:match('^CMD:(%d+):(.*)$')
        id = tonumber(id)
        if id and id > lastFileCommand then
            lastFileCommand = id
            -- Reading a file is local; the line goes out as this client's player, and every
            -- client runs it when the sync arrives
            BlzSendSyncData(SELF_PREFIX, line)
        end
    end

    local function announce()
        PreloadGenClear()
        PreloadGenStart()
        Preload('cmdpoll=' .. attempt)
        PreloadGenEnd(BEAT .. me .. '.txt')
    end

    local function every(seconds, fn)
        TimerStart(CreateTimer(), seconds, true, fn)
    end

    --- Starts the trace and the channels. Runs by itself right after the map's main; a map may
    --- call it earlier to trace its own start-up.
    function Slop.start()
        if started then
            return
        end
        started = true
        me = '-p' .. GetPlayerId(GetLocalPlayer())
        -- A simulation clock: timers run in lockstep, the wall clock does not
        every(TICK_SECONDS, function() ticks = ticks + 1 end)
        every(1, heartbeat)
        every(1, Slop.flush)
        listen()
        if Slop.files then
            every(POLL_SECONDS, poll)
            every(1, function() pcall(announce) end)
        end
        Slop.note('slop', string.format('started v%d seat=%s prefix=%s', Slop.version, tostring(Slop.seat), Slop.prefix))
    end

    if config.autostart ~= false and type(main) == 'function' then
        local mapMain = main
        main = function()
            mapMain()
            Slop.start()
        end
    end
end
