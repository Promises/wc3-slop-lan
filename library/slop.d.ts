/**
 * wc3-slop-lan's map library (slop.lua), for TypeScript-to-Lua maps. The harness injects it
 * into a staged copy of the map, so it is undefined in every normal build: always guard. Its
 * functions are plain table fields, called with a dot (no self), hence `this: void` throughout.
 *
 *     if (Slop !== undefined) {
 *         Slop.onCommand((player, line) => myCommands.run(player, line));
 *     }
 *
 * See docs/library.md.
 */
declare interface SlopLibrary {
    readonly version: number;
    /** The host's seat (0-based slot), a player no client plays; undefined when the host is hidden. */
    readonly seat: number | undefined;
    /** The sync prefix the seat's commands arrive under. */
    readonly prefix: string;
    /** Whether the file channel polls CustomMapData for command files. */
    readonly files: boolean;

    /** One traced event. Only from code every client runs alike - never under GetLocalPlayer. */
    note(this: void, category: string, text: string): void;
    /** Categories written the moment they are noted instead of on the next flush. */
    urgent(this: void, ...categories: string[]): void;
    /** Writes what was noted since the last flush. */
    flush(this: void): void;
    /** Roughly how many handles this client has made: a hint when hunting a desync, not proof of one. */
    handleMark(this: void): number;
    /**
     * A unit's ref: how the library names units, the same number on every client (handle ids
     * are not). Use it to name units in notes.
     */
    ref(this: void, unit: unit): number;

    /** Takes a command line for a player (0-based slot); return true when it was handled. */
    onCommand(this: void, hook: (this: void, player: number, line: string) => boolean): void;
    /** Adds "key=value ..." to every heartbeat. */
    heartbeat(this: void, hook: (this: void) => string): void;
    /** Adds "key=value ..." to one player's part of the heartbeat. */
    playerFields(this: void, hook: (this: void, player: number) => string): void;

    /** Runs a command line as a player, on this client. Only from code every client runs alike. */
    run(this: void, player: number, line: string): void;
    /** Starts the trace and channels; happens by itself right after main. */
    start(this: void): void;
}

declare const Slop: SlopLibrary | undefined;
