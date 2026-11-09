import asyncio
import contextlib
import io
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image

from oiu import coredevice
from oiu.coredevice import CoreDeviceBackend, UNIT, capped, held, jpeg, panel
from oiu.core import Device, Registry
from oiu.keys import chord

_image = io.BytesIO()
Image.new('RGB', (9, 19), 'white').save(_image, format='JPEG')
JPEG = _image.getvalue()


class FakeConnection:
    def __init__(self, hid_available=True):
        self.hid_available = hid_available
        self.reason = coredevice.HID_UNAVAILABLE
        self.hid = None
        self.calls = []
        self.frame_delay = .01

    async def frames(self):
        while True:
            await asyncio.sleep(self.frame_delay)
            yield JPEG

    async def touch_service(self):
        if not self.hid_available: raise OSError(coredevice.HID_UNAVAILABLE)
        self.hid = self.hid or object()
        return self.hid

    async def tap(self, x, y):
        await self.touch_service(); self.calls.append(('tap', x, y))

    async def drag(self, *path):
        await self.touch_service(); self.calls.append(('drag', *path))

    async def press(self, keys):
        await self.touch_service(); self.calls.append(('press', keys))

    async def button(self, page, code):
        await self.touch_service(); self.calls.append(('button', page, code))

    async def release(self, x, y):
        self.calls.append(('release', x, y))


def opener(connection):
    @contextlib.asynccontextmanager
    async def open_connection(udid):
        yield connection
    return open_connection


class CoreDeviceTests(unittest.TestCase):
    def setUp(self):
        self.connection = FakeConnection()
        self.device = Device('phone', 'Phone', '00008110-TEST')
        self.device.backend = CoreDeviceBackend(self.device, self.device.udid, 60, opener(self.connection))
        self.addCleanup(self.device.backend.close)
        self.device.backend.wake(3)

    def act(self, operation, **arguments):
        deadline = time.monotonic() + 3  # A failed action also invalidates earlier frames.
        while self.device.at <= self.device.last_input_completed_at and time.monotonic() < deadline: time.sleep(.01)
        if operation not in ('ensure_session', 'prepare_session', 'pointer_quick_probe'):
            arguments['expectedFrameID'] = self.device.frame_id
        return self.device.action(operation, arguments)

    def test_frames_flow_and_status_names_the_connection(self):
        summary = self.device.summary()
        self.assertTrue(summary['capture_active'])
        self.assertEqual(summary['control_status'], 'Connected over USB')

    def test_tap_is_absolute_and_returns_a_later_frame(self):
        result = self.act('pointer_tap', x=.25, y=1)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.connection.calls, [('tap', round(.25 * UNIT), UNIT)])
        self.assertEqual(result['target'], [.25, 1.0])
        self.assertGreater(self.device.backend.requested, result['inputEndedAt'])
        self.assertEqual(result['frame']['frameID'], self.device.frame_id)
        self.assertTrue(self.act('pointer_tap', x=.5, y=.5)['ok'])  # The returned frame is valid next.

    def test_hold_uses_the_exact_point_and_rejects_invalid_durations(self):
        result = self.act('pointer_hold', x=.25, y=.75, duration=.8)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.connection.calls, [('drag', round(.25 * UNIT), round(.75 * UNIT), round(.25 * UNIT), round(.75 * UNIT), .8)])
        self.assertEqual(result['target'], [.25, .75])
        for duration in (0, 10.1, float('nan'), True):
            result = self.act('pointer_hold', x=.25, y=.75, duration=duration)
            self.assertFalse(result['ok'], result)
        self.assertEqual(len([call for call in self.connection.calls if call[0] == 'drag']), 1)

    def test_cancelled_hold_releases_the_same_hid_contact(self):
        from pymobiledevice3.remote.core_device.hid_service import UniversalHIDServiceService

        class HID:
            multi_drag = UniversalHIDServiceService.multi_drag

            def __init__(self):
                self.reports = []
                self.started = asyncio.Event()

            async def send_multitouch(self, contacts, *args):
                self.reports.append(contacts)
                self.started.set()

        async def check():
            hid = HID()
            connection = coredevice.stream_connection().__new__(coredevice.stream_connection())
            connection._uhs, connection._kb_service_id = hid, None
            connection._fb_width, connection._fb_height = 100, 200
            connection.touch_service = mock.AsyncMock(return_value=hid)
            task = asyncio.create_task(connection.drag(100, 200, 100, 200, 10))
            await hid.started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            await connection.release(100, 200)
            self.assertEqual([(c.identity, c.touching) for report in hid.reports for c in report], [(0, True), (0, False)])

        asyncio.run(check())

    def test_landscape_frames_turn_onto_the_portrait_touch_panel(self):
        corners = dict(top_left=(0, 0), top_right=(UNIT, 0), bottom_left=(0, UNIT), bottom_right=(UNIT, UNIT))
        for name, (x, y) in corners.items():
            self.assertEqual(panel(False, x, y), (x, y))
        self.assertEqual({name: panel(True, x, y) for name, (x, y) in corners.items()},
                         dict(top_left=(UNIT, 0), top_right=(UNIT, UNIT), bottom_left=(0, 0), bottom_right=(0, UNIT)))

    def test_idle_stream_stops_and_restarts_on_use(self):
        opens = []
        @contextlib.asynccontextmanager
        async def counting(udid):
            opens.append(udid)
            yield FakeConnection()
        device = Device('idle', 'Idle', 'u-idle')
        device.backend = CoreDeviceBackend(device, device.udid, .3, counting)
        self.addCleanup(device.backend.close)
        time.sleep(.2)
        self.assertEqual((opens, device.backend.label), ([], coredevice.IDLE))
        device.backend.wake()
        time.sleep(.8)
        self.assertEqual((len(opens), device.backend.label), (1, coredevice.IDLE))
        device.backend.wake()
        self.assertEqual(len(opens), 2)
        self.assertTrue(device.backend.is_fresh())

    def test_a_closing_stream_takes_no_input_and_wakes_after_it_closes(self):
        connection, closing, gate = FakeConnection(), threading.Event(), threading.Event()
        @contextlib.asynccontextmanager
        async def slow_close(udid):
            try: yield connection
            finally:
                closing.set()
                while not gate.is_set(): await asyncio.sleep(.01)
        device = Device('closing', 'Closing', 'u-closing')
        device.backend = backend = CoreDeviceBackend(device, device.udid, .3, slow_close)
        self.addCleanup(gate.set)
        self.addCleanup(backend.close)
        backend.wake(3)
        self.assertTrue(closing.wait(3))
        self.assertFalse(backend.request('type_text', text='a')['ok'])
        backend.wake(.2)
        self.assertEqual((backend.connection, backend.is_fresh(), list(device.history), connection.calls), (None, False, [], []))
        gate.set()
        backend.wake(3)
        self.assertTrue(backend.is_fresh())

    def test_failed_reconnects_do_not_keep_an_unused_phone_awake(self):
        opens = []
        class Broken(FakeConnection):
            async def frames(self):
                raise OSError('stream failed')
                yield
        @contextlib.asynccontextmanager
        async def broken(udid):
            opens.append(udid)
            yield Broken()
        device = Device('broken', 'Broken', 'u-broken')
        device.backend = backend = CoreDeviceBackend(device, device.udid, .1, broken)
        backend.RETRY_SECONDS = .025
        self.addCleanup(backend.close)
        backend.wake(.1)
        time.sleep(.4)
        settled = len(opens)
        time.sleep(.3)
        self.assertEqual(len(opens), settled)
        self.assertIn('stream failed', backend.label)

    def test_click_uses_the_last_pointer_target(self):
        self.act('pointer_move', x=.1, y=.2)
        self.act('click')
        self.assertEqual(self.connection.calls, [('tap', round(.1 * UNIT), round(.2 * UNIT))])

    def test_relative_motion_is_not_an_action(self):
        with self.assertRaisesRegex(ValueError, 'Unknown action'): self.act('move_pointer', dx=3, dy=0)
        self.assertEqual(self.connection.calls, [])

    def test_text_is_validated_before_any_key(self):
        self.assertFalse(self.act('type_text', text='aæ')['ok'])
        self.assertEqual(self.connection.calls, [])
        self.assertTrue(self.act('type_text', text='aB')['ok'])
        self.assertEqual(self.connection.calls, [('press', (0x04,)), ('press', (0xE1, 0x05))])

    def test_us_layout_composes_swedish_letters_with_option(self):
        self.assertTrue(self.act('type_text', text='åÄé')['ok'])
        self.assertEqual(self.connection.calls, [('press', (0xE2, 0x04)), ('press', (0xE2, 0x18)), ('press', (0xE1, 0x04)),
                                                 ('press', (0xE2, 0x08)), ('press', (0x08,))])

    def test_swedish_layout_types_its_own_keys(self):
        self.device.keyboard_layout = 'sv'
        self.assertTrue(self.act('type_text', text='@å~é')['ok'])
        self.assertEqual(self.connection.calls, [('press', (0xE2, 0x1F)), ('press', (0x2F,)), ('press', (0xE2, 0x30)),
                                                 ('press', (0x2C,)), ('press', (0x2E,)), ('press', (0x08,))])
        self.assertFalse(self.act('type_text', text='aæ')['ok'])
        self.act('send_key', key='cmd+-')
        self.assertEqual(self.connection.calls[-1], ('press', (0xE3, 0x38)))

    def test_key_chords_map_modifiers_to_usages(self):
        self.assertEqual(held(*chord('cmd+space', 'us')), (0xE3, 0x2C))
        self.assertEqual(held(0, 0x28), (0x28,))
        self.act('send_key', key='cmd+h')
        self.assertEqual(self.connection.calls, [('press', (0xE3, 0x0B))])

    def test_home_is_the_hardware_button(self):
        self.act('send_key', key='home')
        self.assertEqual(self.connection.calls, [('button', 0x0C, 0x40)])

    def test_swipes_and_scrolls_drag_the_finger(self):
        self.assertFalse(self.act('advance_feed', dy=1)['settled'])
        self.act('scroll', dy=600)
        drag, scroll = self.connection.calls
        self.assertEqual(drag[1:5], (round(.5 * UNIT), round(.7 * UNIT), round(.5 * UNIT), round(.3 * UNIT)))
        self.assertGreater(scroll[2], scroll[4])  # Positive dy drags up, moving content up.

    def test_missing_hid_reports_the_requirement(self):
        self.connection.hid_available = False
        result = self.act('ensure_session')
        self.assertFalse(result['ok'])
        self.assertIn('UI Automation', result['message'])

    def test_several_phones_can_share_a_server(self):
        other = Device('two', 'Two', 'other')
        other.backend = CoreDeviceBackend(other, 'other', 60, opener(FakeConnection()))
        self.addCleanup(other.backend.close)
        other.backend.wake(3)
        self.assertTrue(other.summary()['capture_active'])

    def test_a_closed_phone_refuses_work_without_touching_its_loop(self):
        backend = self.device.backend
        backend.close()
        backend.wake(0)
        with self.assertRaisesRegex(ValueError, 'Device removed'):
            backend.call(backend.perform(None, 'pointer_move', dict(x=.5, y=.5)))

    def test_stop_releases_the_touch(self):
        self.act('pointer_tap', x=.5, y=.5)
        self.device.action('stop_control', {})
        self.assertEqual(self.connection.calls[-1][0], 'release')


class FrameTests(unittest.TestCase):
    def test_decoded_bgra_becomes_jpeg(self):
        with Image.open(io.BytesIO(jpeg(bgra=bytes([0, 0, 255, 0]) * 6, size=(2, 3)))) as image:
            self.assertEqual(image.size, (2, 3))
            self.assertGreater(image.getpixel((0, 0))[0], 200)  # BGRA red stays red.

    def test_encoder_padding_is_cropped_to_the_display(self):
        padded = jpeg(bgra=bytes(4) * 4 * 5, size=(4, 5), visible=(3, 4))
        with Image.open(io.BytesIO(padded)) as image:
            self.assertEqual(image.size, (3, 4))
        with Image.open(io.BytesIO(jpeg(bgra=bytes(4) * 4 * 5, size=(4, 5), visible=(5, 4)))) as image:
            self.assertEqual(image.size, (4, 5))


class BitrateTests(unittest.TestCase):
    def test_only_network_caps_are_lowered(self):
        tiers = ((4074, 0, 16384), (0, 75_000_000, 524288), (16, 4100, None), (0, 1_000_000, 1))
        self.assertEqual(capped(tiers, 2_000_000), ((4074, 0, 16384), (0, 2_000_000, 524288), (16, 4100, None), (0, 1_000_000, 1)))


class FailingOpenerTests(unittest.TestCase):
    def test_connection_errors_are_reported_and_retried(self):
        attempts = threading.Event()

        @contextlib.asynccontextmanager
        async def failing(udid):
            attempts.set()
            raise OSError('Developer Mode is off')
            yield

        device = Device('phone', 'Phone', 'u1')
        backend = CoreDeviceBackend(device, 'u1', 60, failing)
        backend.RETRY_SECONDS = 0
        self.addCleanup(backend.close)
        backend.wake(0)
        self.assertTrue(attempts.wait(2))
        time.sleep(.05)
        self.assertEqual(backend.label, 'Not connected: Developer Mode is off')
        self.assertFalse(backend.request('pointer_tap', x=.5, y=.5)['ok'])


class UsbHandoffTests(unittest.TestCase):
    def setUp(self):
        tunnel = threading.Lock()

        @contextlib.asynccontextmanager
        async def usb(udid):
            if not tunnel.acquire(blocking=False): raise OSError(coredevice.USB_BUSY)
            try:
                connection = FakeConnection()
                connection.transport = 'USB'
                yield connection
            finally:
                tunnel.release()

        self.phones = []
        for name in ('iPad', 'iPhone'):
            device = Device(name, name, 'u-' + name)
            device.backend = CoreDeviceBackend(device, device.udid, 60, usb)
            device.backend.RETRY_SECONDS = .05
            self.addCleanup(device.backend.close)
            self.phones.append(device.backend)

    def test_an_unused_phone_hands_the_usb_connection_over(self):
        ipad, iphone = self.phones
        ipad.wake(3)
        with mock.patch.object(coredevice, 'USB_HANDOFF_SECONDS', .3):
            iphone.wake(3)
        self.assertTrue(iphone.is_fresh())
        self.assertEqual((iphone.label, ipad.label), ('Connected over USB', coredevice.IDLE))
        self.assertFalse(ipad.is_fresh() or ipad.device.summary()['capture_active'] or ipad.device.history)

    def test_a_request_during_the_handoff_check_keeps_the_usb_connection(self):
        ipad, _ = self.phones
        with mock.patch.object(coredevice, 'USB_HANDOFF_SECONDS', .1):
            time.sleep(.2)
            ipad.touch()
            self.assertFalse(ipad.hand_over_usb())

    def test_a_phone_waiting_for_its_first_frame_keeps_the_usb_connection(self):
        ipad, _ = self.phones
        gate = threading.Event()
        frames = FakeConnection.frames
        async def gated(connection):
            while not gate.is_set(): await asyncio.sleep(.01)
            async for frame in frames(connection): yield frame
        self.addCleanup(gate.set)
        with mock.patch.object(FakeConnection, 'frames', gated), mock.patch.object(coredevice, 'USB_HANDOFF_SECONDS', .1):
            waking = threading.Thread(target=ipad.wake, args=(3,))
            waking.start()
            time.sleep(.3)
            self.assertFalse(ipad.hand_over_usb())
            gate.set()
            waking.join()
        self.assertTrue(ipad.is_fresh())

    def test_a_phone_in_use_keeps_the_usb_connection_and_names_itself(self):
        ipad, iphone = self.phones
        ipad.wake(3)
        iphone.wake(.3)
        self.assertEqual(ipad.label, 'Connected over USB')
        self.assertFalse(iphone.is_fresh())
        self.assertIn('iPad used it 0 s ago', iphone.label)

    def test_a_running_action_keeps_the_usb_connection(self):
        ipad, iphone = self.phones
        ipad.wake(3)

        async def slow(keys):
            await asyncio.sleep(1)

        ipad.connection.press = slow
        typing = threading.Thread(target=ipad.request, args=('type_text',), kwargs=dict(text='a'))
        with mock.patch.object(coredevice, 'USB_HANDOFF_SECONDS', .1):
            typing.start()
            time.sleep(.3)
            iphone.wake(.5)
            self.assertEqual(ipad.label, 'Connected over USB')
        typing.join()


class LiveStreamTests(unittest.TestCase):
    def test_web_ui_streams_frames_as_mjpeg(self):
        import threading
        from urllib.request import urlopen
        from oiu.http import Server
        with tempfile.TemporaryDirectory() as directory:
            registry = Registry(directory)
            device = registry.create('u1', 'Phone')
            device.upload(JPEG)
            server = Server(('127.0.0.1', 0), registry, watchdog=False)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/devices/{device.id}/stream', timeout=5) as response:
                    self.assertTrue(response.headers['Content-Type'].startswith('multipart/x-mixed-replace'))
                    head = response.read(len(b'--frame\r\nContent-Type: image/jpeg\r\n'))
                    self.assertEqual(head, b'--frame\r\nContent-Type: image/jpeg\r\n')
            finally:
                server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
