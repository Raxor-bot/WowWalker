#!/usr/bin/env python3
"""WowWalker - geh auf dem Walking Pad, und deine Figur in World of Warcraft geht mit.

Das iPhone steckt in der Hosentasche und misst mit der App "phyphox" die
Beschleunigung. Über den phyphox-Fernzugriff liest dieses Programm die Werte
per WLAN aus, erkennt, ob du gehst, und hält dann die Lauf-Taste (Standard: W)
gedrückt. Bleibst du stehen, wird die Taste losgelassen.

Benötigt nur Python 3.8+ (keine zusätzlichen Pakete). Tastendrücke gibt es nur
unter Windows; auf anderen Systemen läuft das Programm im Trockenlauf.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass, fields
from typing import Callable, Deque, List, Optional, Tuple

VERSION = "0.1.0"
IS_WINDOWS = sys.platform == "win32"
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wowwalker.json")
PHYPHOX_DEFAULT_PORT = 8080

Sample = Tuple[float, float, float, float]  # (t, x, y, z)


# --------------------------------------------------------------------------- #
# Einstellungen
# --------------------------------------------------------------------------- #

@dataclass
class Settings:
    """Alle Einstellungen. Sie stehen auch in der Datei wowwalker.json."""

    phyphox_url: str = ""                 # Adresse aus phyphox, z.B. http://192.168.178.42:8080
    move_key: str = "w"                   # Taste, die beim Gehen gedrückt gehalten wird
    pause_key: str = "mouse5"             # Pause/Weiter (z.B. mouse4, mouse5, f8, pause)
    window_title: str = "World of Warcraft"  # Taste nur drücken, wenn dieses Fenster vorne ist ("" = immer)
    threshold: float = 1.5                # m/s²: ab diesem Ausschlag zählt es als Bewegung
    min_event_gap: float = 0.3            # s: höchstens ein Bewegungs-Ausschlag pro Zeitraum
    events_to_start: int = 3              # so viele Ausschläge ...
    start_window: float = 2.0             # ... innerhalb so vieler Sekunden = du gehst los
    stop_after: float = 1.0               # s ohne Ausschlag = du stehst
    stale_after: float = 1.0              # s ohne neue Daten = Verbindung weg, Taste loslassen
    poll_interval: float = 0.05           # s zwischen zwei Abfragen beim iPhone
    buffer_reset_minutes: float = 10.0    # phyphox-Speicher regelmäßig leeren (0 = nie)
    autostart: bool = True                # phyphox-Messung automatisch starten
    beep: bool = True                     # Piepton beim Pausieren/Fortsetzen


def _coerce(value, default):
    """Wandelt einen Wert aus der JSON-Datei in den Typ des Standardwerts um."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "ja", "1", "false", "nein", "0"):
            return value.strip().lower() in ("true", "ja", "1")
        raise ValueError("erwartet true oder false")
    if isinstance(default, int):
        if isinstance(value, bool) or not float(value).is_integer():
            raise ValueError("erwartet eine ganze Zahl")
        return int(value)
    if isinstance(default, float):
        if isinstance(value, bool):
            raise ValueError("erwartet eine Zahl")
        return float(value)
    return str(value)


def load_settings(path: str) -> Tuple[Settings, bool]:
    """Lädt die Einstellungen. Gibt (Einstellungen, Datei_vorhanden) zurück."""
    settings = Settings()
    if not os.path.exists(path):
        return settings, False
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: erwartet ein JSON-Objekt")
    defaults = Settings()
    known = {f.name for f in fields(Settings)}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if key not in known:
            print(f"Hinweis: unbekannte Einstellung '{key}' in {path} wird ignoriert.")
            continue
        try:
            setattr(settings, key, _coerce(value, getattr(defaults, key)))
        except (TypeError, ValueError) as e:
            raise ValueError(f"{path}: Einstellung '{key}' = {value!r} ist ungültig ({e})") from None
    return settings, True


def save_settings(settings: Settings, path: str) -> None:
    data = {"_info": "Erklärung aller Werte: siehe README.md (Abschnitt Einstellungen)"}
    data.update(asdict(settings))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def validate_settings(s: Settings) -> List[str]:
    problems = []
    if s.threshold <= 0:
        problems.append("threshold muss größer als 0 sein")
    if s.events_to_start < 1:
        problems.append("events_to_start muss mindestens 1 sein")
    for name in ("min_event_gap", "start_window", "stop_after", "stale_after", "poll_interval"):
        if getattr(s, name) <= 0:
            problems.append(f"{name} muss größer als 0 sein")
    if s.buffer_reset_minutes < 0:
        problems.append("buffer_reset_minutes darf nicht negativ sein")
    return problems


def normalize_url(text: str) -> str:
    """Macht aus '192.168.178.42' oder 'http://192.168.178.42:8080/' eine einheitliche Adresse."""
    t = text.strip()
    if not t:
        raise ValueError("leere Adresse")
    if "://" not in t:
        t = "http://" + t
    parts = urllib.parse.urlsplit(t)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"'{text}' ist keine gültige Adresse")
    port = parts.port  # löst ValueError bei ungültigem Port aus
    host = parts.hostname
    if ":" in host:  # IPv6
        host = f"[{host}]"
    return f"{parts.scheme}://{host}:{port or PHYPHOX_DEFAULT_PORT}"


# --------------------------------------------------------------------------- #
# Bewegungserkennung
# --------------------------------------------------------------------------- #

class WalkDetector:
    """Erkennt aus der linearen Beschleunigung (ohne g), ob du gehst.

    Jeder Ausschlag über `threshold` ist ein Bewegungs-Ereignis (höchstens eins
    pro `min_event_gap`). Kommen `events_to_start` Ereignisse innerhalb von
    `start_window` Sekunden, gehst du. Kommt `stop_after` Sekunden lang keins
    mehr, stehst du. Alle Zeiten sind Messzeiten des iPhones.
    """

    def __init__(self, settings: Settings):
        self.s = settings
        self.reset()

    def reset(self) -> None:
        self.events: Deque[float] = deque()
        self.last_event_t: Optional[float] = None
        self.latest_t: Optional[float] = None
        self.walking = False
        self._display_peak = 0.0

    def add_sample(self, t: float, x: float, y: float, z: float) -> None:
        if self.latest_t is not None and t < self.latest_t:
            self.reset()  # Zeit läuft rückwärts: neue Messung auf dem iPhone
        self.latest_t = t
        magnitude = math.sqrt(x * x + y * y + z * z)
        self._display_peak = max(self._display_peak, magnitude)
        if magnitude >= self.s.threshold and (
            self.last_event_t is None or t - self.last_event_t >= self.s.min_event_gap
        ):
            self.last_event_t = t
            self.events.append(t)
        keep_after = t - max(self.s.start_window, self.s.stop_after)
        while self.events and self.events[0] < keep_after:
            self.events.popleft()

    def add_samples(self, samples: List[Sample]) -> None:
        for sample in samples:
            self.add_sample(*sample)
        self._update_state()

    def _update_state(self) -> None:
        if self.latest_t is None or self.last_event_t is None:
            self.walking = False
            return
        now = self.latest_t
        recent_enough = now - self.last_event_t <= self.s.stop_after
        if self.walking:
            self.walking = recent_enough
        else:
            in_window = sum(1 for e in self.events if e >= now - self.s.start_window)
            self.walking = recent_enough and in_window >= self.s.events_to_start

    def take_display_peak(self) -> float:
        """Größter Ausschlag seit dem letzten Aufruf (für die Kalibrier-Anzeige)."""
        peak, self._display_peak = self._display_peak, 0.0
        return peak


# --------------------------------------------------------------------------- #
# phyphox-Fernzugriff
# --------------------------------------------------------------------------- #

class PhyphoxError(Exception):
    pass


WRONG_EXPERIMENT = (
    "In phyphox ist das falsche Experiment offen. Bitte 'Beschleunigung (ohne g)' öffnen "
    "und dort den Fernzugriff erlauben."
)


class PhyphoxClient:
    """Liest neue Messwerte des Experiments 'Beschleunigung (ohne g)'.

    Nutzt die Abfrage /get?acc_time=T&accX=T|acc_time&..., die nur Werte
    liefert, die neuer als Zeitpunkt T sind.
    """

    TIME, X, Y, Z = "acc_time", "accX", "accY", "accZ"

    def __init__(self, base_url: str, timeout: float = 1.0, autostart: bool = True,
                 clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url
        self.timeout = timeout
        self.autostart = autostart
        self.clock = clock
        self.last_t: Optional[float] = None  # None = Startpunkt noch nicht bekannt
        self.session: Optional[str] = None
        self.measuring = False
        self.restarted = False  # True, wenn die Messung neu begonnen hat (Erkennung zurücksetzen)
        self._last_new_data = clock()
        self._last_start_attempt = -1e9
        # Kein Proxy: das iPhone ist im lokalen Netz.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _get_json(self, path: str, params: Optional[List[Tuple[str, str]]] = None) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with self._opener.open(url, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raise PhyphoxError(f"phyphox antwortet mit HTTP-Fehler {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise PhyphoxError(f"keine Verbindung zu {self.base_url} ({reason})") from None
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise PhyphoxError("unerwartete Antwort - ist das wirklich die phyphox-Adresse?") from None
        if not isinstance(data, dict):
            raise PhyphoxError("unerwartete Antwort von phyphox")
        return data

    def _read_status(self, data: dict) -> bool:
        """Liest den Status. Gibt False zurück, wenn ein anderes Experiment/eine neue Sitzung läuft."""
        status = data.get("status") or {}
        self.measuring = bool(status.get("measuring"))
        session = status.get("session")
        if self.session is not None and session != self.session:
            self.session = session
            self.last_t = None
            self.restarted = True
            return False
        self.session = session
        return True

    @staticmethod
    def _buffer(data: dict, name: str) -> list:
        buffers = data.get("buffer")
        if not isinstance(buffers, dict) or name not in buffers:
            raise PhyphoxError(WRONG_EXPERIMENT)
        values = buffers[name].get("buffer")
        return values if isinstance(values, list) else []

    def latest_time(self) -> Optional[float]:
        """Zeitpunkt des neuesten Messwerts (None, wenn noch nichts gemessen wurde)."""
        data = self._get_json("/get", [(self.TIME, "")])
        values = self._buffer(data, self.TIME)
        self._read_status(data)
        return values[0] if values and isinstance(values[0], (int, float)) else None

    def sync(self) -> None:
        """Ab jetzt nur noch neue Werte holen."""
        latest = self.latest_time()
        # Zeiten beginnen bei 0; -1 bedeutet "alles", falls noch nichts gemessen wurde.
        self.last_t = latest if latest is not None else -1.0
        self._last_new_data = self.clock()

    def fetch(self) -> List[Sample]:
        """Holt alle neuen Messwerte seit dem letzten Aufruf."""
        if self.last_t is None:
            self.sync()
        threshold = repr(float(self.last_t))
        params = [(self.TIME, threshold)] + [
            (name, f"{threshold}|{self.TIME}") for name in (self.X, self.Y, self.Z)
        ]
        data = self._get_json("/get", params)
        ts, xs, ys, zs = (self._buffer(data, n) for n in (self.TIME, self.X, self.Y, self.Z))
        if not self._read_status(data):
            return []  # neue Sitzung: beim nächsten Mal neu synchronisieren
        now = self.clock()

        samples: List[Sample] = []
        for t, x, y, z in zip(ts, xs, ys, zs):
            if None in (t, x, y, z):
                continue
            samples.append((float(t), float(x), float(y), float(z)))

        if samples:
            self.last_t = max(self.last_t, samples[-1][0])
            self._last_new_data = now
        elif self.measuring and now - self._last_new_data > 1.5:
            # Misst, liefert aber nichts Neues: wurde die Messung auf dem iPhone gelöscht?
            latest = self.latest_time()
            if latest is None or latest < self.last_t:
                self.last_t = latest if latest is not None else -1.0
                self.restarted = True
            self._last_new_data = now

        if not self.measuring and self.autostart and now - self._last_start_attempt > 3.0:
            self._last_start_attempt = now
            self.control("start")
        return samples

    def control(self, cmd: str) -> None:
        self._get_json("/control", [("cmd", cmd)])

    def clear(self) -> None:
        """Löscht die Messwerte auf dem iPhone, damit phyphox nicht immer langsamer wird."""
        self.control("clear")
        self.last_t = None
        self.restarted = True
        self._last_start_attempt = -1e9  # falls die Messung dadurch stoppt: sofort neu starten


# --------------------------------------------------------------------------- #
# Tastatur / Windows
# --------------------------------------------------------------------------- #

MOUSE_VKS = {"mouse1": 0x01, "mouse2": 0x02, "mouse3": 0x04, "mouse4": 0x05, "mouse5": 0x06}
NAMED_VKS = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11, "strg": 0x11,
    "alt": 0x12, "pause": 0x13, "capslock": 0x14, "space": 0x20, "leertaste": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "einfg": 0x2D, "delete": 0x2E, "entf": 0x2E,
    "numlock": 0x90, "scrolllock": 0x91, "rollen": 0x91,
}
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E}


def parse_key(name: str) -> int:
    """Wandelt einen Tastennamen ('w', 'mouse5', 'f8', 'numpad1', '0x57') in einen Windows-Tastencode."""
    key = name.strip().lower()
    if not key:
        raise ValueError("leerer Tastenname")
    if key in MOUSE_VKS:
        return MOUSE_VKS[key]
    if key in NAMED_VKS:
        return NAMED_VKS[key]
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    if key.startswith("numpad") and key[6:].isdigit() and 0 <= int(key[6:]) <= 9:
        return 0x60 + int(key[6:])
    if key.startswith("0x"):
        code = int(key, 16)
        if 0 < code < 0xFF:
            return code
    raise ValueError(f"unbekannte Taste '{name}'")


class DryRunPlatform:
    """Drückt keine Tasten, sondern schreibt nur auf, was passieren würde."""

    dry_run = True

    def __init__(self, settings: Settings, log: Callable[[str], None]):
        self.title = settings.window_title or "Trockenlauf"
        self.log = log

    def press(self) -> bool:
        self.log("(Trockenlauf) Taste würde jetzt gedrückt")
        return True

    def release(self) -> None:
        self.log("(Trockenlauf) Taste würde jetzt losgelassen")

    def prepare_console(self) -> None:
        pass

    def move_key_down(self) -> Optional[bool]:
        return None  # unbekannt

    def pause_key_down(self) -> bool:
        return False

    def foreground_title(self) -> str:
        return self.title

    def beep(self, high: bool) -> None:
        pass


if IS_WINDOWS:
    import ctypes
    import winsound
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _INPUT_KEYBOARD = 1
    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_SCANCODE = 0x0008
    _MAPVK_VK_TO_VSC = 0
    _ULONG_PTR = wintypes.WPARAM

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = (("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR))

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR))

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = (("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD))

    class _INPUTUNION(ctypes.Union):
        _fields_ = (("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT))

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = (("type", wintypes.DWORD), ("u", _INPUTUNION))

    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetForegroundWindow.argtypes = ()
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _HANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    _kernel32.SetConsoleCtrlHandler.argtypes = (_HANDLER_ROUTINE, wintypes.BOOL)
    _kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    _kernel32.GetStdHandle.argtypes = (wintypes.DWORD,)
    _kernel32.GetStdHandle.restype = wintypes.HANDLE
    _kernel32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetConsoleMode.restype = wintypes.BOOL
    _kernel32.SetConsoleMode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.SetConsoleMode.restype = wintypes.BOOL
    _STD_INPUT_HANDLE = 0xFFFFFFF6  # (DWORD)-10
    _ENABLE_QUICK_EDIT_MODE = 0x0040
    _ENABLE_EXTENDED_FLAGS = 0x0080

    class WindowsPlatform:
        """Echte Tastendrücke über SendInput (mit Scancode, wie eine echte Tastatur)."""

        dry_run = False

        def __init__(self, settings: Settings, log: Callable[[str], None]):
            self.move_vk = parse_key(settings.move_key)
            if self.move_vk in MOUSE_VKS.values():
                raise ValueError("Die Lauf-Taste (move_key) muss eine Tastatur-Taste sein.")
            self.pause_vk = parse_key(settings.pause_key)
            self.move_scan = _user32.MapVirtualKeyW(self.move_vk, _MAPVK_VK_TO_VSC)
            if not self.move_scan:
                raise ValueError(f"Für die Taste '{settings.move_key}' gibt es keinen Scancode.")
            self.extended = self.move_vk in EXTENDED_VKS
            self.log = log
            self._lock = threading.Lock()
            self._down = False
            # Beim Schließen des Konsolenfensters die Taste noch loslassen.
            self._handler = _HANDLER_ROUTINE(self._on_console_event)
            _kernel32.SetConsoleCtrlHandler(self._handler, True)

        def prepare_console(self) -> None:
            """Schaltet den QuickEdit-Modus ab: Ein Klick ins Konsolenfenster würde sonst das
            Programm anhalten, während die Taste vielleicht gerade gedrückt ist."""
            handle = _kernel32.GetStdHandle(_STD_INPUT_HANDLE)
            mode = wintypes.DWORD()
            if handle and _kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = (mode.value | _ENABLE_EXTENDED_FLAGS) & ~_ENABLE_QUICK_EDIT_MODE
                _kernel32.SetConsoleMode(handle, new_mode)

        def _on_console_event(self, event: int) -> bool:
            if event in (2, 5, 6):  # Fenster geschlossen, Abmelden, Herunterfahren
                self.release()
            return False  # Standardbehandlung weiterlaufen lassen

        def _send(self, key_up: bool) -> bool:
            flags = _KEYEVENTF_SCANCODE
            if self.extended:
                flags |= _KEYEVENTF_EXTENDEDKEY
            if key_up:
                flags |= _KEYEVENTF_KEYUP
            inp = _INPUT()
            inp.type = _INPUT_KEYBOARD
            inp.ki = _KEYBDINPUT(wVk=0, wScan=self.move_scan, dwFlags=flags, time=0, dwExtraInfo=0)
            return _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1

        def press(self) -> bool:
            with self._lock:
                ok = self._send(key_up=False)
                self._down = True
                return ok

        def release(self) -> None:
            with self._lock:
                if self._down:
                    self._send(key_up=True)
                    self._down = False

        def move_key_down(self) -> Optional[bool]:
            return bool(_user32.GetAsyncKeyState(self.move_vk) & 0x8000)

        def pause_key_down(self) -> bool:
            return bool(_user32.GetAsyncKeyState(self.pause_vk) & 0x8000)

        def foreground_title(self) -> str:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return ""
            length = _user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value

        def beep(self, high: bool) -> None:
            threading.Thread(target=winsound.Beep, args=(880 if high else 440, 120), daemon=True).start()


# --------------------------------------------------------------------------- #
# Steuerung
# --------------------------------------------------------------------------- #

class Controller:
    """Verbindet Erkennung und Tastatur: hält die Taste, solange du gehst."""

    def __init__(self, settings: Settings, client: PhyphoxClient, platform, calibrate: bool = False,
                 log: Callable[[str], None] = print, clock: Callable[[], float] = time.monotonic):
        self.s = settings
        self.client = client
        self.platform = platform
        self.calibrate = calibrate
        self.log = log
        self.clock = clock
        self.detector = WalkDetector(settings)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

        self.enabled = True          # per Pause-Taste umschaltbar
        self.holding = False         # Taste von uns gedrückt
        self.suppressed = False      # du hast die Taste selbst losgelassen -> erst nach Stehen wieder
        self._hold_since = 0.0
        self._pause_was_down = False
        self._last_state: Optional[str] = None
        self._last_calib = 0.0

        self.connected = False
        self.last_error = ""
        self.last_data = -1e9
        self._last_buffer_reset = clock()

    # --- Datenabfrage (läuft im Hintergrund-Thread) ------------------------- #

    def poll_once(self) -> bool:
        """Eine Abfrage beim iPhone. Gibt False bei Verbindungsfehler zurück."""
        try:
            self._maybe_reset_buffer()
            samples = self.client.fetch()
        except PhyphoxError as e:
            with self.lock:
                self.connected = False
                self.last_error = str(e)
            return False
        now = self.clock()
        with self.lock:
            self.connected = True
            self.last_error = ""
            if self.client.restarted:
                self.client.restarted = False
                self.detector.reset()
            if samples:
                self.last_data = now
            self.detector.add_samples(samples)
        return True

    def _maybe_reset_buffer(self) -> None:
        interval = self.s.buffer_reset_minutes * 60
        if interval <= 0:
            return
        elapsed = self.clock() - self._last_buffer_reset
        # Bevorzugt beim Stehen leeren; spätestens nach dreifacher Zeit auch beim Gehen.
        if elapsed >= interval and (not self.detector.walking or elapsed >= 3 * interval):
            self._last_buffer_reset = self.clock()
            self.client.clear()

    def reader_loop(self) -> None:
        while not self.stop_event.is_set():
            ok = self.poll_once()
            self.stop_event.wait(self.s.poll_interval if ok else 1.0)

    # --- Tastensteuerung (Hauptschleife) ------------------------------------ #

    def _toggle_pause(self) -> None:
        self.enabled = not self.enabled
        if self.s.beep:
            self.platform.beep(high=self.enabled)
        self.log("WowWalker AKTIV" if self.enabled else "WowWalker PAUSIERT")

    SUPPRESSED_STATE = ("Gehen, aber du hast die Lauf-Taste selbst losgelassen - "
                        "kurz stehen bleiben oder 2x Pause-Taste drücken")

    def _evaluate(self, now: float) -> Tuple[bool, str]:
        """Soll die Taste gedrückt sein (ohne Berücksichtigung von `suppressed`)?

        Gibt (ja/nein, Zustandsbeschreibung) zurück.
        """
        with self.lock:
            connected, error = self.connected, self.last_error
            fresh = now - self.last_data <= self.s.stale_after
            walking = self.detector.walking
            measuring = self.client.measuring
        if not connected:
            if not error:
                return False, "Warte auf die ersten Messwerte ..."
            return False, f"Keine Verbindung zum iPhone: {error}"
        if not measuring:
            if self.s.autostart:
                return False, "Die Messung in phyphox läuft nicht - wird gestartet ..."
            return False, "Die Messung in phyphox läuft nicht (in phyphox auf Start tippen)"
        if not fresh:
            return False, "Keine neuen Messwerte (iPhone-Bildschirm aus oder phyphox im Hintergrund?)"
        if self.calibrate:
            return False, "GEHEN" if walking else "stehen"
        if not self.enabled:
            return False, "Pausiert"
        if not walking:
            return False, "Stehen - Taste losgelassen"
        if self.s.window_title:
            title = self.platform.foreground_title()
            if self.s.window_title.lower() not in title.lower():
                return False, f"Gehen, aber WoW ist nicht im Vordergrund (aktiv: '{title or '-'}')"
        return True, "Gehen - Taste gedrückt"

    def tick(self) -> None:
        now = self.clock()
        pause_down = self.platform.pause_key_down()
        if pause_down and not self._pause_was_down and not self.calibrate:
            self._toggle_pause()
        self._pause_was_down = pause_down

        wanted, state = self._evaluate(now)
        if not wanted:
            self.suppressed = False  # Stehen, Pause, Fensterwechsel ... -> danach wieder normal
        elif self.suppressed:
            state = self.SUPPRESSED_STATE
        should_hold = wanted and not self.suppressed

        if self.holding and not should_hold:
            self.platform.release()
            self.holding = False
        elif should_hold and not self.holding:
            if not self.platform.press():
                self.log("Warnung: Windows hat den Tastendruck blockiert (läuft WoW als Administrator?)")
            self.holding = True
            self._hold_since = now
        elif self.holding and now - self._hold_since > 0.3 and self.platform.move_key_down() is False:
            # Du hast die Taste selbst gedrückt und losgelassen (z.B. im Chat): nicht dagegen ankämpfen.
            self.holding = False
            self.suppressed = True
            state = self.SUPPRESSED_STATE

        if state != self._last_state:
            self._last_state = state
            if not self.calibrate or not state.startswith(("GEHEN", "stehen")):
                self.log(state)
        if self.calibrate and now - self._last_calib >= 0.5:
            self._last_calib = now
            self._print_calibration(state)

    def _print_calibration(self, state: str) -> None:
        with self.lock:
            peak = self.detector.take_display_peak()
            fresh = self.connected and self.clock() - self.last_data <= self.s.stale_after
        if not fresh:
            return
        width, scale = 40, self.s.threshold * 3
        filled = min(width, int(round(peak / scale * width)))
        marker = min(width - 1, int(round(self.s.threshold / scale * width)))
        bar = "".join("#" if i < filled else ("|" if i == marker else " ") for i in range(width))
        self.log(f"Ausschlag {peak:6.2f} m/s²  [{bar}]  {state}")

    def shutdown(self) -> None:
        self.stop_event.set()
        if self.holding:
            self.platform.release()
            self.holding = False

    def run(self) -> None:
        reader = threading.Thread(target=self.reader_loop, name="phyphox", daemon=True)
        reader.start()
        try:
            while not self.stop_event.is_set():
                self.tick()
                time.sleep(0.02)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
            reader.join(timeout=2.0)
            self.log("Beendet. Die Lauf-Taste ist losgelassen.")


# --------------------------------------------------------------------------- #
# Start
# --------------------------------------------------------------------------- #

def log_line(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def connect(settings: Settings, force_ask: bool) -> PhyphoxClient:
    """Fragt bei Bedarf nach der Adresse und prüft die Verbindung."""
    url = "" if force_ask else settings.phyphox_url
    while True:
        if not url:
            print()
            print("Öffne auf dem iPhone in phyphox 'Beschleunigung (ohne g)', tippe oben rechts auf")
            print("die drei Punkte und wähle 'Fernzugriff erlauben'. Unten erscheint eine Adresse.")
            url = ask("Adresse aus phyphox (z.B. http://192.168.178.42:8080): ")
            if not url.strip():
                raise SystemExit("Keine Adresse eingegeben - Ende.")
        try:
            url = normalize_url(url)
        except ValueError as e:
            print(f"Ungültige Adresse: {e}")
            url = ""
            continue
        client = PhyphoxClient(url, autostart=settings.autostart)
        print(f"Verbinde mit {url} ...")
        try:
            client.sync()
            return client
        except PhyphoxError as e:
            print(f"Fehler: {e}")
            print("Tipps: Gleiches WLAN? phyphox geöffnet und Fernzugriff an? iPhone-Bildschirm an?")
            answer = ask("Neue Adresse eingeben, oder Enter für nochmal versuchen (q = Ende): ").strip()
            if answer.lower() == "q":
                raise SystemExit(1)
            if answer:
                url = answer


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="WowWalker: Geh auf dem Walking Pad, und deine WoW-Figur geht mit.")
    parser.add_argument("--url", help="phyphox-Adresse, z.B. http://192.168.178.42:8080")
    parser.add_argument("--kalibrieren", "--calibrate", action="store_true", dest="calibrate",
                        help="nur Messwerte anzeigen, keine Tasten drücken (zum Einstellen der Schwelle)")
    parser.add_argument("--trockenlauf", "--dry-run", action="store_true", dest="dry_run",
                        help="keine echten Tastendrücke, nur anzeigen")
    parser.add_argument("--neue-adresse", action="store_true", dest="ask_url",
                        help="nach einer neuen phyphox-Adresse fragen")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Pfad zur Einstellungsdatei")
    parser.add_argument("--version", action="version", version=f"WowWalker {VERSION}")
    args = parser.parse_args(argv)

    print(f"WowWalker {VERSION}")
    try:
        settings, existed = load_settings(args.config)
    except (OSError, ValueError) as e:
        print(f"Die Einstellungsdatei ist fehlerhaft: {e}")
        return 1
    problems = validate_settings(settings)
    if problems:
        print("Ungültige Einstellungen: " + "; ".join(problems))
        return 1
    if args.url:
        settings.phyphox_url = args.url

    try:
        if args.dry_run or not IS_WINDOWS:
            if not IS_WINDOWS and not args.dry_run:
                print("Hinweis: Echte Tastendrücke gibt es nur unter Windows - Trockenlauf.")
            platform = DryRunPlatform(settings, log_line)
        else:
            platform = WindowsPlatform(settings, log_line)
    except ValueError as e:
        print(f"Einstellungsfehler: {e}")
        return 1

    client = connect(settings, force_ask=args.ask_url)
    if client.base_url != settings.phyphox_url or not existed:
        settings.phyphox_url = client.base_url
        try:
            save_settings(settings, args.config)
            print(f"Einstellungen gespeichert in {args.config}")
        except OSError as e:
            print(f"Hinweis: Einstellungen konnten nicht gespeichert werden ({e})")

    print()
    print(f"Verbunden mit {client.base_url}.")
    if args.calibrate:
        print("KALIBRIER-MODUS: Es werden keine Tasten gedrückt.")
        print(f"Der Strich '|' markiert die Schwelle ({settings.threshold} m/s²). Beim Gehen sollten die")
        print("Balken deutlich darüber liegen, beim Stehen auf den Seitenleisten deutlich darunter.")
    else:
        print(f"Lauf-Taste: {settings.move_key.upper()}   Pause/Weiter: {settings.pause_key}")
        if settings.window_title:
            print(f"Die Taste wird nur gedrückt, wenn '{settings.window_title}' im Vordergrund ist.")
        print("Tipp: Vor dem Chatten in WoW die Pause-Taste drücken.")
    print("Beenden: Strg+C oder dieses Fenster schließen.")
    print()

    platform.prepare_console()
    Controller(settings, client, platform, calibrate=args.calibrate, log=log_line).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
