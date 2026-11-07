"""Tests für WowWalker. Ausführen mit:  python -m unittest -v"""
from __future__ import annotations

import json
import math
import os
import random
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import wowwalker as ww

RATE = 100  # Hz, wie das iPhone in phyphox


# --------------------------------------------------------------------------- #
# Hilfen: künstliche Messdaten
# --------------------------------------------------------------------------- #

def walking_signal(t0: float, duration: float, step_period: float = 0.55, amplitude: float = 3.5,
                   noise: float = 0.15, seed: int = 1):
    """Gehen: pro Schritt ein kurzer Stoß (wie in der phyphox-Kurve), dazwischen Rauschen."""
    rnd = random.Random(seed)
    samples = []
    n = int(duration * RATE)
    for i in range(n):
        t = t0 + i / RATE
        phase = ((t - t0) % step_period) / step_period
        bump = amplitude * math.exp(-((phase - 0.5) ** 2) / (2 * 0.04 ** 2))
        samples.append((t, bump * 0.8 + rnd.gauss(0, noise), bump * 0.4 + rnd.gauss(0, noise),
                        -bump * 0.45 + rnd.gauss(0, noise)))
    return samples


def idle_signal(t0: float, duration: float, noise: float = 0.15, seed: int = 2):
    """Stehen (z.B. auf den Seitenleisten, Motor vibriert): nur kleines Rauschen."""
    rnd = random.Random(seed)
    return [(t0 + i / RATE, rnd.gauss(0, noise), rnd.gauss(0, noise), rnd.gauss(0, noise))
            for i in range(int(duration * RATE))]


def feed_in_batches(detector, samples, batch=5):
    """Wie im echten Betrieb: Daten kommen in kleinen Paketen. Liefert (t, walking) je Paket."""
    states = []
    for i in range(0, len(samples), batch):
        chunk = samples[i:i + batch]
        detector.add_samples(chunk)
        states.append((chunk[-1][0], detector.walking))
    return states


# --------------------------------------------------------------------------- #
# Nachbau des phyphox-Fernzugriffs (Verhalten wie RemoteServer.java)
# --------------------------------------------------------------------------- #

class FakePhyphox:
    BUFFERS = ("acc_time", "accX", "accY", "accZ", "acc")

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {name: [] for name in self.BUFFERS}
        self.measuring = True
        self.session = "abc123"
        self.commands = []
        self.experiment_buffers = set(self.BUFFERS)

    def add(self, samples):
        with self.lock:
            for t, x, y, z in samples:
                self.data["acc_time"].append(t)
                self.data["accX"].append(x)
                self.data["accY"].append(y)
                self.data["accZ"].append(z)
                self.data["acc"].append(math.sqrt(x * x + y * y + z * z))

    def handle_get(self, query):
        out = {}
        with self.lock:
            for name, value in query:
                if name not in self.experiment_buffers:
                    continue
                values = self.data[name]
                if value == "":
                    out[name] = {"size": 0, "updateMode": "single", "buffer": [values[-1] if values else None]}
                    continue
                if value == "full":
                    out[name] = {"size": 0, "updateMode": "full", "buffer": list(values)}
                    continue
                thr_text, _, ref = value.partition("|")
                thr = float(thr_text)
                thr += 10 ** math.floor(math.log10(abs(thr) / 1e7)) if thr != 0 else 0
                ref_values = self.data[ref] if ref else values
                selected = [v for v, r in zip(values, ref_values) if r > thr]
                out[name] = {"size": 0, "updateMode": "partial", "buffer": selected}
            return {"buffer": out, "status": {"session": self.session, "measuring": self.measuring,
                                              "timedRun": False, "countDown": 0}}

    def handle_control(self, cmd):
        with self.lock:
            self.commands.append(cmd)
            if cmd == "start":
                self.measuring = True
            elif cmd == "stop":
                self.measuring = False
            elif cmd == "clear":
                self.data = {name: [] for name in self.BUFFERS}
                self.measuring = False  # wie bei phyphox: Löschen beendet die Messung
        return {"result": True}


class FakePhyphoxServer:
    def __init__(self):
        self.phyphox = FakePhyphox()
        fake = self.phyphox

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                if parts.path == "/get":
                    body = fake.handle_get(query)
                elif parts.path == "/control":
                    body = fake.handle_control(dict(query).get("cmd"))
                else:
                    self.send_error(404)
                    return
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakePlatform:
    dry_run = True

    def __init__(self, title="World of Warcraft"):
        self.title = title
        self.events = []
        self.os_key_down = False   # Was Windows über die Taste denkt
        self.pause_down = False

    def press(self):
        self.events.append("down")
        self.os_key_down = True
        return True

    def release(self):
        self.events.append("up")
        self.os_key_down = False

    def move_key_down(self):
        return self.os_key_down

    def pause_key_down(self):
        return self.pause_down

    def foreground_title(self):
        return self.title

    def beep(self, high):
        pass


class FakeClient:
    """Ersetzt PhyphoxClient im Controller-Test: liefert vorbereitete Pakete."""

    def __init__(self):
        self.measuring = True
        self.restarted = False
        self.queue = []
        self.fail = None
        self.cleared = 0

    def fetch(self):
        if self.fail:
            raise ww.PhyphoxError(self.fail)
        out, self.queue = self.queue, []
        return out

    def clear(self):
        self.cleared += 1
        self.restarted = True


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.s = ww.Settings()
        self.d = ww.WalkDetector(self.s)

    def test_walking_starts_quickly_and_stops_after_timeout(self):
        samples = idle_signal(0, 3) + walking_signal(3, 6) + idle_signal(9, 4)
        states = feed_in_batches(self.d, samples)
        start = next(t for t, w in states if w)
        stop = next(t for t, w in states if t > start and not w)
        self.assertTrue(3.0 < start < 4.6, f"Start bei {start:.2f} s")
        # letzter Schritt-Stoß bei ca. 8.8 s; stehen nach stop_after (1.0 s)
        self.assertTrue(9.0 < stop < 10.2, f"Stopp bei {stop:.2f} s")
        # zwischendurch kein Flackern
        self.assertTrue(all(w for t, w in states if start <= t < 8.9))

    def test_idle_noise_never_counts_as_walking(self):
        states = feed_in_batches(self.d, idle_signal(0, 30, noise=0.3))
        self.assertFalse(any(w for _, w in states))

    def test_single_bump_is_not_walking(self):
        samples = idle_signal(0, 2)
        samples.append((2.0, 6.0, 0.0, 0.0))  # z.B. Handy kurz angestoßen
        samples += idle_signal(2.01, 3)
        states = feed_in_batches(self.d, samples)
        self.assertFalse(any(w for _, w in states))

    def test_slow_walking_on_low_speed(self):
        # sehr langsam (ca. 1 km/h): ein Stoß pro 0.9 s, schwächer
        samples = walking_signal(0, 8, step_period=0.9, amplitude=2.2)
        states = feed_in_batches(self.d, samples)
        start = next(t for t, w in states if w)
        self.assertLess(start, 3.0)
        self.assertTrue(all(w for t, w in states if t >= start))

    def test_time_going_backwards_resets(self):
        feed_in_batches(self.d, walking_signal(100, 3))
        self.assertTrue(self.d.walking)
        self.d.add_samples([(0.0, 0.0, 0.0, 0.0)])  # neue Messung beginnt wieder bei 0
        self.assertFalse(self.d.walking)

    def test_display_peak(self):
        self.d.add_samples([(0.0, 3.0, 4.0, 0.0), (0.01, 0.1, 0.0, 0.0)])
        self.assertAlmostEqual(self.d.take_display_peak(), 5.0)
        self.assertEqual(self.d.take_display_peak(), 0.0)


class ClientTests(unittest.TestCase):
    def test_incremental_fetch_without_duplicates(self):
        with FakePhyphoxServer() as srv:
            srv.phyphox.add(walking_signal(0, 1))  # schon vorhandene Daten werden übersprungen
            client = ww.PhyphoxClient(srv.url)
            client.sync()
            self.assertAlmostEqual(client.last_t, 0.99)
            received = []
            for k in range(5):
                srv.phyphox.add(walking_signal(1 + k * 0.1, 0.1, seed=k))
                received += client.fetch()
            times = [s[0] for s in received]
            self.assertEqual(len(times), 50)
            self.assertEqual(times, sorted(set(times)))
            self.assertGreater(times[0], 0.99)
            self.assertEqual(client.fetch(), [])

    def test_empty_buffer_then_data(self):
        with FakePhyphoxServer() as srv:
            client = ww.PhyphoxClient(srv.url)
            client.sync()
            self.assertEqual(client.last_t, -1.0)
            srv.phyphox.add(idle_signal(0, 0.5))
            self.assertEqual(len(client.fetch()), 50)  # inklusive t = 0

    def test_wrong_experiment_is_reported(self):
        with FakePhyphoxServer() as srv:
            srv.phyphox.experiment_buffers = {"lux", "time"}
            client = ww.PhyphoxClient(srv.url)
            with self.assertRaises(ww.PhyphoxError) as ctx:
                client.sync()
            self.assertIn("Beschleunigung (ohne g)", str(ctx.exception))

    def test_connection_error(self):
        client = ww.PhyphoxClient("http://127.0.0.1:9", timeout=0.5)
        with self.assertRaises(ww.PhyphoxError):
            client.fetch()

    def test_autostart_when_not_measuring(self):
        with FakePhyphoxServer() as srv:
            srv.phyphox.measuring = False
            client = ww.PhyphoxClient(srv.url)
            client.fetch()
            self.assertIn("start", srv.phyphox.commands)
            self.assertTrue(srv.phyphox.measuring)

    def test_no_autostart_if_disabled(self):
        with FakePhyphoxServer() as srv:
            srv.phyphox.measuring = False
            client = ww.PhyphoxClient(srv.url, autostart=False)
            client.fetch()
            self.assertNotIn("start", srv.phyphox.commands)

    def test_clear_restarts_and_continues(self):
        with FakePhyphoxServer() as srv:
            srv.phyphox.add(walking_signal(0, 50))
            client = ww.PhyphoxClient(srv.url)
            client.fetch()
            client.clear()
            self.assertEqual(srv.phyphox.commands[-1], "clear")
            self.assertTrue(client.restarted)
            client.fetch()  # synchronisiert neu und startet die Messung wieder
            self.assertTrue(srv.phyphox.measuring)
            srv.phyphox.add(walking_signal(0, 0.3))
            self.assertEqual(len(client.fetch()), 30)

    def test_measurement_cleared_on_phone_is_detected(self):
        with FakePhyphoxServer() as srv:
            clock = FakeClock()
            srv.phyphox.add(idle_signal(0, 100))
            client = ww.PhyphoxClient(srv.url, clock=clock)
            client.fetch()
            with srv.phyphox.lock:  # jemand tippt in phyphox auf "Löschen" und startet neu
                srv.phyphox.data = {n: [] for n in srv.phyphox.BUFFERS}
            srv.phyphox.add(idle_signal(0, 0.2))
            self.assertEqual(client.fetch(), [])
            clock.advance(2.0)
            client.fetch()
            self.assertTrue(client.restarted)
            srv.phyphox.add(idle_signal(0.2, 0.2))
            self.assertEqual(len(client.fetch()), 20)

    def test_session_change(self):
        with FakePhyphoxServer() as srv:
            srv.phyphox.add(idle_signal(0, 1))
            client = ww.PhyphoxClient(srv.url)
            client.fetch()
            srv.phyphox.session = "neu"
            self.assertEqual(client.fetch(), [])
            self.assertTrue(client.restarted)
            self.assertIsNone(client.last_t)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.s = ww.Settings(buffer_reset_minutes=0)
        self.clock = FakeClock()
        self.client = FakeClient()
        self.platform = FakePlatform()
        self.logs = []
        self.c = ww.Controller(self.s, self.client, self.platform, log=self.logs.append, clock=self.clock)
        self.sensor_t = 0.0

    def run_for(self, seconds, signal="walk"):
        """Simuliert `seconds` Sekunden Betrieb: alle 50 ms neue Daten + ein Tick."""
        for _ in range(int(seconds / 0.05)):
            if signal == "walk":
                # Ausschnitt aus einem durchgehenden Geh-Signal
                chunk = [s for s in self._walk_cache if self.sensor_t <= s[0] < self.sensor_t + 0.05]
            elif signal == "idle":
                chunk = idle_signal(self.sensor_t, 0.05, seed=int(self.sensor_t * 100))
            else:
                chunk = []
            self.sensor_t += 0.05
            self.client.queue = chunk
            self.c.poll_once()
            self.clock.advance(0.05)
            self.c.tick()

    @property
    def _walk_cache(self):
        if not hasattr(self, "_cache"):
            self._cache = walking_signal(0, 600)
        return self._cache

    def test_press_while_walking_release_when_standing(self):
        self.run_for(1, "idle")
        self.assertEqual(self.platform.events, [])
        self.run_for(3, "walk")
        self.assertEqual(self.platform.events, ["down"])
        self.run_for(2, "idle")
        self.assertEqual(self.platform.events, ["down", "up"])

    def test_pause_key_toggles(self):
        self.run_for(2, "walk")
        self.assertEqual(self.platform.events, ["down"])
        self.platform.pause_down = True
        self.run_for(0.1, "walk")
        self.platform.pause_down = False
        self.run_for(1, "walk")
        self.assertEqual(self.platform.events, ["down", "up"])
        self.assertFalse(self.c.enabled)
        self.platform.pause_down = True
        self.run_for(0.1, "walk")
        self.platform.pause_down = False
        self.run_for(0.5, "walk")
        self.assertEqual(self.platform.events, ["down", "up", "down"])

    def test_only_when_wow_in_foreground(self):
        self.platform.title = "Discord"
        self.run_for(3, "walk")
        self.assertEqual(self.platform.events, [])
        self.platform.title = "World of Warcraft"
        self.run_for(0.2, "walk")
        self.assertEqual(self.platform.events, ["down"])
        self.platform.title = "Google Chrome"
        self.run_for(0.2, "walk")
        self.assertEqual(self.platform.events, ["down", "up"])

    def test_release_when_connection_lost_or_stale(self):
        self.run_for(2, "walk")
        self.run_for(1.5, "none")  # keine neuen Daten (Bildschirm aus)
        self.assertEqual(self.platform.events, ["down", "up"])
        self.run_for(2, "walk")
        self.client.fail = "weg"
        self.run_for(0.1, "walk")
        self.assertEqual(self.platform.events, ["down", "up", "down", "up"])
        self.assertTrue(any("Keine Verbindung" in line for line in self.logs))

    def test_user_released_key_is_respected_until_standing(self):
        self.run_for(2, "walk")
        self.platform.os_key_down = False  # du hast W selbst gedrückt und losgelassen
        self.run_for(2, "walk")
        self.assertEqual(self.platform.events, ["down"])  # kein erneutes Drücken (z.B. im Chat)
        self.assertTrue(self.c.suppressed)
        self.run_for(2, "idle")
        self.assertFalse(self.c.suppressed)
        self.run_for(2, "walk")
        self.assertEqual(self.platform.events, ["down", "down"])

    def test_calibration_never_presses(self):
        self.c.calibrate = True
        self.run_for(3, "walk")
        self.assertEqual(self.platform.events, [])
        self.assertTrue(any("Ausschlag" in line and "GEHEN" in line for line in self.logs))

    def test_shutdown_releases(self):
        self.run_for(2, "walk")
        self.c.shutdown()
        self.assertEqual(self.platform.events, ["down", "up"])

    def test_buffer_reset_waits_for_standing(self):
        self.s.buffer_reset_minutes = 0.05  # 3 s
        self.run_for(4, "walk")
        self.assertEqual(self.client.cleared, 0)
        self.run_for(2, "idle")
        self.assertEqual(self.client.cleared, 1)


class SettingsTests(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(ww.normalize_url("192.168.178.42"), "http://192.168.178.42:8080")
        self.assertEqual(ww.normalize_url(" http://192.168.0.5:8080/ "), "http://192.168.0.5:8080")
        self.assertEqual(ww.normalize_url("10.0.0.2:9000"), "http://10.0.0.2:9000")
        for bad in ("", "ftp://x", "http://", "1.2.3.4:abc"):
            with self.assertRaises(ValueError):
                ww.normalize_url(bad)

    def test_parse_key(self):
        self.assertEqual(ww.parse_key("w"), 0x57)
        self.assertEqual(ww.parse_key("W"), 0x57)
        self.assertEqual(ww.parse_key("mouse5"), 0x06)
        self.assertEqual(ww.parse_key("f8"), 0x77)
        self.assertEqual(ww.parse_key("numpad1"), 0x61)
        self.assertEqual(ww.parse_key("up"), 0x26)
        self.assertEqual(ww.parse_key("0x57"), 0x57)
        for bad in ("", "f25", "ä", "foo"):
            with self.assertRaises(ValueError):
                ww.parse_key(bad)

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wowwalker.json")
            s = ww.Settings(phyphox_url="http://1.2.3.4:8080", threshold=2.0, beep=False)
            ww.save_settings(s, path)
            loaded, existed = ww.load_settings(path)
            self.assertTrue(existed)
            self.assertEqual(loaded, s)

    def test_load_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wowwalker.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"threshold": "viel"}, f)
            with self.assertRaises(ValueError):
                ww.load_settings(path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"events_to_start": 2.5}, f)
            with self.assertRaises(ValueError):
                ww.load_settings(path)

    def test_missing_file_gives_defaults(self):
        s, existed = ww.load_settings("/nicht/vorhanden.json")
        self.assertFalse(existed)
        self.assertEqual(s, ww.Settings())

    def test_validate(self):
        self.assertEqual(ww.validate_settings(ww.Settings()), [])
        self.assertTrue(ww.validate_settings(ww.Settings(threshold=0)))


if __name__ == "__main__":
    unittest.main()
