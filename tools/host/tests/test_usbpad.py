"""The usb:// backend (usbpad.py) against the switch-pad core on a pseudo-terminal (firmware/out/padsim: the same
swpad::Pad the ESP32-S3 runs, 1 ms report loop, boot noise first). Report changes are read from padsim's log.
Run: make -C firmware out/padsim && /usr/bin/python3 -I -m unittest discover -s tools/host/tests"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
REPO = HOST.parent.parent
sys.path.insert(0, str(HOST))
import switch_remote as sr  # noqa: E402
import usbpad  # noqa: E402
from PIL import Image  # noqa: E402

PADSIM = REPO / 'firmware' / 'out' / 'padsim'
HOME_STILL = Path(__file__).resolve().parent / 'data' / 'home-ssbu-first-tile.jpg'
NEUTRAL = [0, 0, 8, 128, 128, 128, 128, 0]


class FakeScene:
    def __init__(self, st):
        self.st = st

    def status(self):
        return dict(self.st)


@unittest.skipUnless(PADSIM.exists(), 'make -C firmware out/padsim')
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sr-usb-'))
        self.log = self.tmp / 'reports.jsonl'
        self.sim = subprocess.Popen([str(PADSIM), '--log', str(self.log)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, text=True)
        self.tty = self.sim.stdout.readline().strip()
        self.c = self.client()

    def client(self, **kw):
        kw.setdefault('scene', FakeScene({'gate': 'no_scene_receiver'}))
        return usbpad.UsbClient(self.tty, capture_backend='capd', capd_socket='/nonexistent/capd.sock',
                                still_dir=self.tmp / 'stills', settle_s=0.05, **kw)

    def tearDown(self):
        self.c.close()
        self.sim.stdin.close()
        self.sim.wait(5)
        self.sim.stdout.close()
        shutil.rmtree(self.tmp)

    def reports(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def last(self):
        return self.reports()[-1]['r']

    def wait_report(self, pred, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rs = self.reports()
            if rs and pred(rs[-1]['r']):
                return rs[-1]
            time.sleep(0.01)
        self.fail(f'report never matched; last {self.reports()[-1:]}')


class Link(Base):
    def test_connects_through_boot_noise_and_answers(self):
        self.assertEqual(self.c.request('ping')['ok'], True)
        v = self.c.request('switch.version')['result']['reply']
        self.assertEqual(v, 'OK version switch-pad 0.1.2 usb=hori-0f0d:0092')
        s = self.c.request('status')['result']
        self.assertEqual((s['backend'], s['state'], s['fw'], s['usb'], s['usb_mounted'], s['gate']),
                         ('usb', 'reachable', 'switch-pad/0.1.2', 'hori-0f0d:0092', '1', 'not_applicable'))
        self.assertIsNone(s['running'])  # no scene receiver: unknown (information only)

    def test_make_client_usb_scheme(self):
        c = sr.make_client('usb://ttyACM9')
        self.assertIsInstance(c, usbpad.UsbClient)
        self.assertEqual(c.serial_device, '/dev/ttyACM9')
        self.assertEqual(sr.make_client('usb:///dev/serial/by-id/x').serial_device, '/dev/serial/by-id/x')

    def test_unreachable_device(self):
        c = usbpad.UsbClient(str(self.tmp / 'nope'), capture_backend='capd', capd_socket='/x', timeout=0.5)
        self.assertEqual(c.request('input.button', {'button': 'a'})['error']['code'], 'unreachable')


class Input(Base):
    def test_tap_press_release_stick(self):
        self.assertTrue(self.c.request('input.button', {'button': 'a', 'duration_ms': 150})['ok'])
        self.wait_report(lambda r: r[0] == 0x04)
        self.wait_report(lambda r: r == NEUTRAL)
        self.c.request('input.button', {'button': 'zr', 'state': 'press', 'lease_ms': 5000})
        self.wait_report(lambda r: r[0] == 0x80)
        self.c.request('input.button', {'button': 'zr', 'state': 'release'})
        self.wait_report(lambda r: r == NEUTRAL)
        self.c.request('input.stick', {'stick': 'left', 'x': 0, 'y': 1, 'lease_ms': 200})
        self.wait_report(lambda r: r[4] == 1)  # stick up -> HID top
        self.c.request('input.button', {'button': 'dright', 'duration_ms': 100})
        self.wait_report(lambda r: r[2] == 2)

    def test_home_is_not_a_plain_button(self):
        self.assertEqual(self.c.request('input.button', {'button': 'home'})['error']['code'], 'refused')

    def test_no_online_gate_on_the_wired_pad(self):
        c = self.client(scene=FakeScene({'gate': 'online_mode'}))
        try:
            self.assertTrue(c.request('input.button', {'button': 'b'})['ok'])
        finally:
            c.close()


class Watchdog(Base):
    def test_keepalive_holds_a_long_press_and_silence_releases_it(self):
        self.c.request('input.button', {'button': 'x', 'state': 'press', 'lease_ms': 4000})
        time.sleep(1.6)  # longer than the 1 s watchdog: the keep-alive pings hold it
        self.assertEqual(self.last()[0], 0x08)
        self.c.stop_keepalive()  # the host goes silent (as if it crashed)
        self.wait_report(lambda r: r == NEUTRAL, timeout=1.8)
        self.assertEqual(self.c.request('status')['result']['watchdog_trips'], '1')


class Macros(Base):
    def test_device_timed_macro(self):
        doc = {'format': 'eden-input-macro', 'version': 1, 'duration_frames': 40, 'events': [
            {'frame': 0, 'buttons': 1, 'sticks': [0, 0, 0, 0]},
            {'frame': 12, 'buttons': 0, 'sticks': [1.0, 0, 0, 0]},
            {'frame': 30, 'buttons': 4, 'sticks': [0, 0, 0, 0]}]}
        path = self.tmp / 'm.edenmacro.json'
        path.write_text(json.dumps(doc))
        r = self.c.request('macro.play', {'path': str(path)})
        self.assertTrue(r['ok'], r)
        self.c.wait_leases()
        self.assertEqual(self.c.request('macro.status')['result']['state'], 'finished')
        rs = self.reports()
        a = next(x for x in rs if x['r'][0] == 0x04)
        stick = next(x for x in rs if x['r'][3] == 255)
        xb = next(x for x in rs if x['r'][0] == 0x08)
        end = next(x for x in rs if x['t_us'] > xb['t_us'] and x['r'] == NEUTRAL)
        # Device clock: frame 12 at 200 ms, 30 at 500 ms, 40 at 666.7 ms after the start (1 ms report loop). The simulator
        # runs on a loaded non-RT host, so +-10 ms here; the device's own timing is measured on hardware.
        self.assertAlmostEqual((stick['t_us'] - a['t_us']) / 1000, 200.0, delta=10)
        self.assertAlmostEqual((xb['t_us'] - a['t_us']) / 1000, 500.0, delta=10)
        self.assertAlmostEqual((end['t_us'] - a['t_us']) / 1000, 666.7, delta=10)

    def test_library_segment_runs_unchanged(self):
        seg = Path('/home/user/ssbu-mod-playbook/tools/eden-runner/library/segments/tap-a.edenmacro.json')
        if not seg.exists():
            self.skipTest('playbook library not present')
        self.assertTrue(self.c.request('macro.play', {'path': str(seg)})['ok'])
        self.c.wait_leases()
        self.assertEqual(self.c.request('macro.status')['result']['state'], 'finished')
        self.assertTrue(any(x['r'][0] == 0x04 for x in self.reports()))


class Sequences(Base):
    def setUp(self):
        super().setUp()
        self.home = Image.open(HOME_STILL).convert('RGB')
        self.frames = []

        def capd(method, params=None):
            if method == 'ui.screenshot':
                (self.frames.pop(0) if self.frames else self.home).save(params['path'])
                return {'path': params['path']}
            raise sr.SwitchError('capture_unavailable', 'fake')
        self.c._capd = capd

    def pressed(self, mask_byte, mask):
        return any(x['r'][mask_byte] & mask for x in self.reports())

    def test_launch_presses_home_then_a(self):
        r = self.c.request('switch.launch')
        self.assertTrue(r['ok'], r)
        rs = self.reports()
        home = next(x['t_us'] for x in rs if x['r'][1] & 0x10)
        a = next(x['t_us'] for x in rs if x['r'][0] & 0x04)
        self.assertLess(home, a)

    def test_launch_stops_after_home_on_a_wrong_still(self):
        self.frames = [Image.new('RGB', (1280, 720))]
        self.assertEqual(self.c.request('switch.launch')['error']['code'], 'not_home_first_tile')
        time.sleep(0.05)
        self.assertTrue(self.pressed(1, 0x10))
        self.assertFalse(self.pressed(0, 0x04))

    def test_dismiss_refuses_on_the_home_first_tile_and_presses_a_elsewhere(self):
        self.assertEqual(self.c.request('switch.dismiss')['error']['code'], 'nothing_to_dismiss')
        self.assertFalse(self.pressed(0, 0x04))
        self.frames = [Image.new('RGB', (1280, 720), (40, 40, 40))]
        self.assertTrue(self.c.request('switch.dismiss')['ok'])
        time.sleep(0.05)
        self.assertTrue(self.pressed(0, 0x04))

    def test_terminate_home_x_a(self):
        from PIL import ImageEnhance
        self.frames = [self.home, ImageEnhance.Brightness(self.home).enhance(0.35)]
        r = self.c.request('switch.terminate')
        self.assertTrue(r['ok'], r)
        rs = self.reports()
        home = next(x['t_us'] for x in rs if x['r'][1] & 0x10)
        x_ = next(x['t_us'] for x in rs if x['r'][0] & 0x08)
        a = next(x['t_us'] for x in rs if x['r'][0] & 0x04)
        self.assertLess(home, x_)
        self.assertLess(x_, a)


class SmokeAndRunner(Base):
    def test_smoke_pad_check(self):
        import smoke
        s = smoke.Smoke.__new__(smoke.Smoke)
        s.c, s.out, s.n, s.backend = self.c, self.tmp / 'smoke', 0, 'usb'
        (s.out / 'shots').mkdir(parents=True)
        r = s.pad_check()
        self.assertTrue(r['ok'], r)

    def test_switch_test_usb_instance(self):
        import switch_test
        inst = switch_test.instances_for('/dev/serial/by-id/x', 0, self.tmp, [], 'usb')
        self.assertEqual(inst['switch']['socket'], 'usb:///dev/serial/by-id/x')


@unittest.skipUnless(PADSIM.exists(), 'make -C firmware out/padsim')
class PiLink(unittest.TestCase):
    """pi:// (DESIGN.md 13a): the same core behind a token-guarded TCP line link, as the Pi daemon would run it."""
    TOKEN = 'test-token-0123456789'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sr-pi-'))
        self.log = self.tmp / 'reports.jsonl'
        self.sim = subprocess.Popen([str(PADSIM), '--tcp', '0', '--token', self.TOKEN, '--log', str(self.log)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.port = int(self.sim.stdout.readline().split()[1])

    def tearDown(self):
        self.sim.stdin.close()
        self.sim.wait(5)
        self.sim.stdout.close()
        shutil.rmtree(self.tmp)

    def client(self, token=TOKEN):
        return usbpad.PiClient(f'pi://127.0.0.1:{self.port}', token=token, capture_backend='capd',
                               capd_socket='/nonexistent/capd.sock', scene=FakeScene({'gate': 'no_scene_receiver'}))

    def reports(self):
        return [json.loads(x)['r'] for x in self.log.read_text().splitlines()]

    def test_same_api_over_the_network_link(self):
        c = self.client()
        try:
            self.assertTrue(c.request('input.button', {'button': 'a', 'duration_ms': 100})['ok'])
            self.assertEqual(c.request('switch.version')['result']['reply'][:30], 'OK version switch-pad 0.1.2 us')
            time.sleep(0.2)
            self.assertTrue(any(r[0] == 0x04 for r in self.reports()))
            self.assertEqual(c.request('status')['result']['backend'], 'usb')
        finally:
            c.close()

    def test_wrong_token_is_refused(self):
        c = self.client(token='wrong-token-0123456789')
        r = c.request('input.button', {'button': 'a'})
        self.assertEqual(r['error']['code'], 'auth')
        self.assertFalse(any(r[0] for r in self.reports()))

    def test_a_dropped_link_goes_neutral(self):
        c = self.client()
        c.request('input.button', {'button': 'y', 'state': 'press', 'lease_ms': 5000})
        c.stop_keepalive()
        c.conn.close()
        deadline = time.time() + 2
        while time.time() < deadline and self.reports()[-1] != NEUTRAL:
            time.sleep(0.02)
        self.assertEqual(self.reports()[-1], NEUTRAL)

    def test_make_client_pi_scheme(self):
        c = sr.make_client('pi://172.30.254.105', token=self.TOKEN)
        self.assertIsInstance(c, usbpad.PiClient)
        self.assertEqual((c.conn.host, c.conn.port), ('172.30.254.105', 47140))


class V0Check(Base):
    def test_pad_v0_test_against_the_simulator(self):
        import pad_v0_test
        home, frames = Image.open(HOME_STILL).convert('RGB'), []
        game = Image.new('RGB', (1280, 720), (10, 20, 30))
        frames[:] = [home, game, home]

        orig = usbpad.UsbClient.__init__

        def init(this, device, **kw):
            orig(this, device, capture_backend='capd', capd_socket='/x', **kw)

            def capd(method, params=None):
                frames.pop(0).save(params['path'])
                return {'path': params['path']}
            this._capd = capd
        usbpad.UsbClient.__init__ = init
        try:
            code = pad_v0_test.main(['run', '--out', str(self.tmp / 'v0'), '--device', self.tty])
        finally:
            usbpad.UsbClient.__init__ = orig
        lines = [json.loads(x) for x in (self.tmp / 'v0' / 'receipts.jsonl').read_text().splitlines()]
        self.assertEqual(code, 0, lines[-1])
        self.assertTrue(any(x['r'][0] == 0x04 for x in self.reports()))   # press a
        self.assertTrue(any(x['r'][1] & 0x10 for x in self.reports()))    # press home
        self.assertTrue(lines[-1]['reply']['ok'])

    def test_detect_shape(self):
        import pad_v0_test
        d = pad_v0_test.detect()
        self.assertIn('device', d)
        self.assertIn('hori_pad_here', d)


class Files(Base):
    def test_logs_need_the_optional_sys_autopilot(self):
        self.assertEqual(self.c.request('switch.list', {'root': 'crash_reports'})['error']['code'], 'unsupported')

        class FakeFiles:
            def _dispatch(self, method, p):
                return [{'name': 'x.log', 'method': method, 'root': p['root']}]
        c = self.client(files=FakeFiles())
        try:
            self.assertEqual(c.request('switch.list', {'root': 'crash_reports'})['result'][0]['method'], 'switch.list')
        finally:
            c.close()


if __name__ == '__main__':
    unittest.main()
