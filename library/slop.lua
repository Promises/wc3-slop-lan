--[[
wc3-slop-lan's map library: lets a test drive a running Lua map from outside and read it back.

The harness appends this to a staged copy of the map's script (`wc3-slop-lan inject`); no build
of the map has to contain it. A map that wants more than the built-ins registers hooks, guarded
by `if Slop then ... end`, and costs nothing when it is absent. See docs/library.md.

  In:  commands from the host's seat (sync prefix `Slop.prefix`, data "<player> <line>"), or from
       a file dropped into a client's CustomMapData (see Slop.files) - run as that player on
       every client, so the game stays in step.
  Out: the lockstep trace, numbered chunks <trace>-NNNN.txt in CustomMapData: a heartbeat each
       second, the events the map notes, and what commands did. Every client writes the same
       lines while they agree.

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
    local BEAT_FILE = (config.trace or 'slop') .. '-beat.txt'
    local COMMAND_FILE = config.commands or 'slop-cmd'
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
        PreloadGenEnd(string.format('%s-%04d.txt', TRACE, chunk))
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
                                'l=' .. GetPlayerState(p, PLAYER_STATE_RESOURCE_LUMBER)}
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

    local function findUnit(group, handleId)
        local match
        eachUnit(group, function(u)
            if GetHandleId(u) == handleId then
                match = u
            end
        end)
        return match
    end

    local builtins = {}

    -- .units: the player's units into the trace, one 'unit' line each, then "p<n> end"
    builtins['.units'] = function(index)
        eachUnit(unitsOf(index), function(u)
            local order = GetUnitCurrentOrder(u)
            Slop.note('unit', string.format('p%d id=%d type=%s at=%d,%d life=%d order=%s', index, GetHandleId(u),
                decodeFourcc(GetUnitTypeId(u)), math.floor(GetUnitX(u)), math.floor(GetUnitY(u)),
                math.floor(GetWidgetLife(u)), OrderId2String(order) or tostring(order)))
        end)
        Slop.note('unit', 'p' .. index .. ' end')
    end

    -- .order <unit> <order> [x y | target]: an order to one of the player's own units
    builtins['.order'] = function(index, words)
        local u = findUnit(unitsOf(index), tonumber(words[2]))
        local name = words[3]
        local ok = false
        if u and name then
            if #words >= 5 then
                ok = IssuePointOrder(u, name, tonumber(words[4]), tonumber(words[5]))
            elseif #words == 4 then
                local all = CreateGroup()
                GroupEnumUnitsInRect(all, GetPlayableMapRect(), nil)
                local target = findUnit(all, tonumber(words[4]))
                ok = target ~= nil and IssueTargetOrder(u, name, target)
            else
                ok = IssueImmediateOrder(u, name)
            end
        end
        Slop.note('order', 'p' .. index .. ' ' .. table.concat(words, ' ', 2) .. (ok and ' issued' or ' rejected'))
    end

    -- .build <builder> <type> <x> <y>: a build order, the type as its four letters
    builtins['.build'] = function(index, words)
        local u = findUnit(unitsOf(index), tonumber(words[2]))
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
        local name = string.format('%s-%04d.txt', COMMAND_FILE, attempt)
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
        PreloadGenEnd(BEAT_FILE)
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
