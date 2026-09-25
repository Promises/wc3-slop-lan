"""Offline checks of the harness's parsing: python3 -m unittest discover harness/tests"""
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from slop import trace  # noqa: E402


class TraceTest(unittest.TestCase):
    def test_beat(self):
        beat = trace.parse_beat('41 t120 h5000 beat handles=5000 cmdpoll=12 lives=100 wave=2 spawning=true '
                                'p0(g=500 l=100 k=3 t=2) p1(g=250 l=0)')
        self.assertEqual(beat['tick'], 120)
        self.assertEqual(beat['wave'], 2)
        self.assertIs(beat['spawning'], True)
        self.assertEqual(beat.gold(0), 500)
        self.assertEqual(beat.player(0, 't'), 2)
        self.assertEqual(beat.lumber(1), 0)
        self.assertNotIn('g', beat)
        self.assertIsNone(trace.parse_beat('42 t121 h5001 unit p0 end'))

    def test_without_handles(self):
        a = trace.without_handles('99 t800 h1051577 beat handles=1051576 cmdpoll=159 wave=1')
        b = trace.without_handles('99 t800 h1051576 beat handles=1051575 cmdpoll=159 wave=1')
        self.assertEqual(a, b)
        self.assertEqual(a, '99 t800 beat cmdpoll=159 wave=1')

    def test_unit(self):
        unit = trace.parse_unit('7 t3 h9 unit p0 id=2001 type=h000 at=100,-51 life=420 order=')
        self.assertEqual((unit['id'], unit['x'], unit['y'], unit['order']), (2001, 100, -51, ''))

    def test_read_and_kinds(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder)
            (folder / 'slop-trace-0002.txt').write_text('\tcall Preload( "3 t1 h1 order p0 x issued" )\n')
            (folder / 'slop-trace-0001.txt').write_text('\tcall Preload( "1 t0 h1 slop started" )\n'
                                                       '\tcall Preload( "2 t0 h1 beat handles=1" )\n')
            lines = trace.read(folder)
            self.assertEqual([line.split()[0] for line in lines], ['1', '2', '3'])
            self.assertEqual(trace.of_kind(lines, 'order'), ['3 t1 h1 order p0 x issued'])
            self.assertEqual(trace.last_beat(lines)['handles'], 1)

    def test_command_file(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder)
            self.assertIsNone(trace.write_command(folder, '.units', 1))
            (folder / trace.BEAT_FILE).write_text('\tcall Preload( "cmdpoll=10" )\n')
            names = trace.write_command(folder, '.gold 5', 7)
            self.assertEqual(names[0], 14)
            body = (folder / 'slop-cmd-0014.txt').read_bytes()
            self.assertIn(b'CMD:7:.gold 5', body)


if __name__ == '__main__':
    unittest.main()
