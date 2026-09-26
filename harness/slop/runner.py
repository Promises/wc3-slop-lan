"""Runs a map's tests: every `test_*` function in the config's tests file, each on a fresh game.

The clients are launched once and kept: between tests the game is ended, the clients go back
to the menus, and the next game is hosted and joined on them. A client that does not make it
back is replaced by starting over; `reuse=False` (slop test --fresh) starts over every time.

A test takes the session and fails by raising (TestFailed, or anything else); after it, every
test is also checked for desyncs. A test that needs the library is skipped when the map has
none. Artifacts (host log, each client's trace) are kept per test under .slop/ next to the config.
"""
import importlib.util
import inspect
import sys
import time
import traceback

from .config import Config
from .session import NeedsLibrary, Session, TestFailed


def load_tests(config: Config):
    """Every test_* function of the config's tests files (tests.file: one file or a list), in
    file order, then source order."""
    if not config.tests_files:
        raise RuntimeError('no tests.file in the config')
    cases = []
    for number, path in enumerate(config.tests_files):
        spec = importlib.util.spec_from_file_location(f'slop_tests_{number}', path)
        module = importlib.util.module_from_spec(spec)
        # The tests' own folder is importable, so tests files can share a helper module
        if str(path.parent) not in sys.path:
            sys.path.insert(0, str(path.parent))
        spec.loader.exec_module(module)
        found = [(name, fn) for name, fn in inspect.getmembers(module, inspect.isfunction)
                 if name.startswith('test_') and fn.__module__ == module.__name__]
        found.sort(key=lambda case: inspect.getsourcelines(case[1])[1])
        cases += found
    return cases


def _game_for(config, name, session, reuse, log):
    """A fresh game for the next test: on the clients already up when it can, else from scratch."""
    quiet = lambda text: log(f'    {text}')
    if session is not None and reuse:
        try:
            return session.next_game(name)
        except Exception as problem:
            quiet(f'could not reuse the clients ({str(problem).splitlines()[0]}); starting over')
    if session is not None:
        session.stop()
    fresh = Session(config, name, log=quiet)
    try:
        return fresh.start()
    except BaseException:
        fresh.stop()
        raise


def run(config: Config, wanted=(), log=print, reuse=True):
    cases = [(name, fn) for name, fn in load_tests(config)
             if not wanted or name in wanted or name[5:] in wanted]
    if not cases:
        log('no tests matched')
        return False
    results = []
    session = None
    try:
        for name, fn in cases:
            started = time.time()
            summary = (fn.__doc__ or '').strip().splitlines()
            log(f'{name}: {summary[0] if summary else ""}')
            try:
                session = _game_for(config, name[5:], session, reuse, log)
                fn(session)
                session.check_in_step()
                outcome = 'PASS'
            except NeedsLibrary as skipped:
                outcome = f'SKIP  {skipped}'
            except TestFailed as failure:
                outcome = f'FAIL  {failure}'
            except Exception:
                outcome = 'ERROR ' + traceback.format_exc().strip().splitlines()[-1]
                # Whatever broke may have left the clients anywhere: start the next test afresh
                if session is not None:
                    session.stop()
                    session = None
            log(f'  {outcome}  ({time.time() - started:.0f}s)')
            results.append(outcome.split()[0])
    finally:
        if session is not None:
            session.stop()
    passed = results.count('PASS')
    log(f'\n{passed} passed, {results.count("SKIP")} skipped, {len(results) - passed - results.count("SKIP")} failed')
    return all(result in ('PASS', 'SKIP') for result in results)
