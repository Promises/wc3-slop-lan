"""Runs a map's tests: every `test_*` function in the config's tests file, each on a fresh game.

A test takes the session and fails by raising (TestFailed, or anything else); after it, every
test is also checked for desyncs. A test that needs the library is skipped when the map has
none. Artifacts (host log, each client's trace) are kept per test under .slop/ next to the config.
"""
import importlib.util
import inspect
import time
import traceback

from .config import Config
from .session import NeedsLibrary, Session, TestFailed


def load_tests(config: Config):
    if config.tests_file is None:
        raise RuntimeError('no tests.file in the config')
    spec = importlib.util.spec_from_file_location('slop_tests', config.tests_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [(name, fn) for name, fn in inspect.getmembers(module, inspect.isfunction)
             if name.startswith('test_') and fn.__module__ == module.__name__]
    cases.sort(key=lambda case: inspect.getsourcelines(case[1])[1])
    return cases


def run(config: Config, wanted=(), log=print):
    cases = [(name, fn) for name, fn in load_tests(config)
             if not wanted or name in wanted or name[5:] in wanted]
    if not cases:
        log('no tests matched')
        return False
    results = []
    for name, fn in cases:
        started = time.time()
        summary = (fn.__doc__ or '').strip().splitlines()
        log(f'{name}: {summary[0] if summary else ""}')
        session = Session(config, name[5:], log=lambda text: log(f'    {text}'))
        try:
            session.start()
            fn(session)
            session.check_in_step()
            outcome = 'PASS'
        except NeedsLibrary as skipped:
            outcome = f'SKIP  {skipped}'
        except TestFailed as failure:
            outcome = f'FAIL  {failure}'
        except Exception:
            outcome = 'ERROR ' + traceback.format_exc().strip().splitlines()[-1]
        finally:
            session.stop()
        log(f'  {outcome}  ({time.time() - started:.0f}s)')
        results.append(outcome.split()[0])
    passed = results.count('PASS')
    log(f'\n{passed} passed, {results.count("SKIP")} skipped, {len(results) - passed - results.count("SKIP")} failed')
    return all(result in ('PASS', 'SKIP') for result in results)
