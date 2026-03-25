#!/usr/bin/env python3
import time
import subprocess
import serial
from serial.serialutil import SerialException

# =========================
# CONFIG
# =========================
PHONE = "192.168.0.114:5555"

# !!! NIE ZMIENIAMY !!!
SERIAL_PORT = "/dev/ttyUSB0"

BAUDRATE = 115200
SERIAL_TIMEOUT_SEC = 0.05
RECONNECT_DELAY_SEC = 0.5

POT_MIN = 0
POT_MAX = 1023

# Zakładki (Twoje punkty)
TABS = [
    ("STAT",  390, 45),
    ("INV",   505, 45),
    ("DATA",  615, 45),
    ("MAP",   750, 45),
    ("RADIO", 875, 45),
]
ZONES = len(TABS)

# --- POT1 (tabs): tap-on-stop (bez przelatywania przez menu) ---
MOVE_THRESHOLD = 5           # wykrywa, że kręcisz (delta >= 5)
STOP_DELAY_SEC = 0.10        # po ilu ms bez ruchu klikamy finalną zakładkę
EMA_ALPHA_TABS = 0.85        # szybkie wygładzanie
MIN_TAP_INTERVAL_SEC = 0.08  # minimalny odstęp między tapami

# --- POT2 (scroll): swipe up/down w listach ---
SCROLL_CENTER = 512
SCROLL_DEADZONE = 70         # +/- tyle nic nie rób
SCROLL_INTERVAL = 0.12       # co ile powtarzać swipe jak trzymasz poza deadzone
SCROLL_X = 520               # gdzie robić swipe (środek list)
SCROLL_Y_MID = 520
SCROLL_DIST = 420
SCROLL_DURATION_MS = 130
EMA_ALPHA_SCROLL = 0.70      # trochę wygładzania, ale bez laga

# --- POT3 (map): pan left/right ---
MAP_CENTER = 512
MAP_DEADZONE = 85
MAP_INTERVAL = 0.14
MAP_X_MID = 520
MAP_Y_MID = 520
MAP_DIST = 380
MAP_DURATION_MS = 140
EMA_ALPHA_MAP = 0.70

DEBUG = True


# =========================
# ADB helpers
# =========================
def log(msg: str) -> None:
    if DEBUG:
        print(msg, flush=True)

def adb_tap(x: int, y: int) -> None:
    subprocess.run(
        ["adb", "-s", PHONE, "shell", "input", "tap", str(x), str(y)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

def adb_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
    subprocess.run(
        ["adb", "-s", PHONE, "shell", "input", "touchscreen", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v

def value_to_zone(v: int) -> int:
    span = POT_MAX - POT_MIN + 1
    idx = int((v - POT_MIN) * ZONES / span)
    if idx < 0:
        return 0
    if idx >= ZONES:
        return ZONES - 1
    return idx


# =========================
# Serial parsing
# =========================
def open_serial():
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=SERIAL_TIMEOUT_SEC)
            time.sleep(1.0)  # Arduino reset po otwarciu portu
            ser.reset_input_buffer()
            log(f"[serial] connected: {SERIAL_PORT} @ {BAUDRATE}")
            return ser
        except SerialException as e:
            log(f"[serial] open failed: {e}. retrying in {RECONNECT_DELAY_SEC}s")
            time.sleep(RECONNECT_DELAY_SEC)

def parse_line(line: bytes):
    """
    Obsługuje:
    - "123" (stare) -> (v0, None, None)
    - "123,456,789" -> (v0, v1, v2)
    """
    if not line:
        return None
    s = line.decode(errors="ignore").strip()
    if not s:
        return None

    if "," in s:
        parts = s.split(",")
        if len(parts) < 3:
            return None
        try:
            v0 = int("".join(ch for ch in parts[0] if ch.isdigit() or ch == "-"))
            v1 = int("".join(ch for ch in parts[1] if ch.isdigit() or ch == "-"))
            v2 = int("".join(ch for ch in parts[2] if ch.isdigit() or ch == "-"))
        except ValueError:
            return None
        return (clamp(v0, POT_MIN, POT_MAX),
                clamp(v1, POT_MIN, POT_MAX),
                clamp(v2, POT_MIN, POT_MAX))

    # fallback: jedna liczba
    digits = "".join(ch for ch in s if ch.isdigit() or ch == "-")
    if digits in ("", "-"):
        return None
    v0 = int(digits)
    return (clamp(v0, POT_MIN, POT_MAX), None, None)


# =========================
# Main logic (state machine)
# =========================
def main() -> None:
    ser = open_serial()

    # UI state
    current_tab = None  # nazwa: "STAT"/"INV"/...

    # POT1 state
    ema_tabs = None
    last_raw_tabs = None
    desired_zone = None
    moving_tabs = False
    last_move_time = 0.0
    last_tap_time = 0.0

    # POT2 state
    ema_scroll = None
    last_scroll_action = 0.0

    # POT3 state
    ema_map = None
    last_map_action = 0.0

    log("Pip-Boy controller: POT1=tabs(tap-on-stop), POT2=scroll(lists), POT3=map(pan)")
    log(f"ADB target: {PHONE}")
    log("Ctrl+C to exit\n")

    while True:
        try:
            vals = parse_line(ser.readline())
            now = time.monotonic()

            # Jeśli brak danych w tej iteracji: nadal możemy wykryć "stop" POT1 i kliknąć finalną zakładkę
            if vals is None:
                if moving_tabs and (now - last_move_time) >= STOP_DELAY_SEC:
                    moving_tabs = False
                    if desired_zone is not None:
                        tab_name, x, y = TABS[desired_zone]
                        if tab_name != current_tab and (now - last_tap_time) >= MIN_TAP_INTERVAL_SEC:
                            adb_tap(x, y)
                            last_tap_time = now
                            current_tab = tab_name
                            log(f"[tabs] TAP -> {tab_name} zone={desired_zone}")
                continue

            v0, v1, v2 = vals

            # -------------------------
            # POT1 (tabs) - zawsze aktywny
            # -------------------------
            raw = v0
            if last_raw_tabs is None:
                last_raw_tabs = raw
                last_move_time = now
            else:
                if abs(raw - last_raw_tabs) >= MOVE_THRESHOLD:
                    moving_tabs = True
                    last_move_time = now
                last_raw_tabs = raw

            if ema_tabs is None:
                ema_tabs = float(raw)
            else:
                ema_tabs = (1.0 - EMA_ALPHA_TABS) * ema_tabs + EMA_ALPHA_TABS * raw

            desired_zone = value_to_zone(int(ema_tabs))

            # jeśli user przestał kręcić -> klik w finalną zakładkę
            if moving_tabs and (now - last_move_time) >= STOP_DELAY_SEC:
                moving_tabs = False
                tab_name, x, y = TABS[desired_zone]
                if tab_name != current_tab and (now - last_tap_time) >= MIN_TAP_INTERVAL_SEC:
                    adb_tap(x, y)
                    last_tap_time = now
                    current_tab = tab_name
                    log(f"[tabs] TAP -> {tab_name} zone={desired_zone}")

            # -------------------------
            # POT2 (scroll) - tylko w listach
            # STAT / INV / DATA / RADIO
            # -------------------------
            if v1 is not None and current_tab in ("STAT", "INV", "DATA", "RADIO"):
                if ema_scroll is None:
                    ema_scroll = float(v1)
                else:
                    ema_scroll = (1.0 - EMA_ALPHA_SCROLL) * ema_scroll + EMA_ALPHA_SCROLL * v1

                delta = int(ema_scroll) - SCROLL_CENTER
                if abs(delta) > SCROLL_DEADZONE and (now - last_scroll_action) >= SCROLL_INTERVAL:
                    # delta > 0 => scroll down -> swipe up (palec w górę)
                    if delta > 0:
                        x = SCROLL_X
                        y1 = SCROLL_Y_MID + (SCROLL_DIST // 2)
                        y2 = SCROLL_Y_MID - (SCROLL_DIST // 2)
                        adb_swipe(x, y1, x, y2, SCROLL_DURATION_MS)
                        log(f"[scroll] {current_tab}: swipe UP (delta={delta})")
                    else:
                        x = SCROLL_X
                        y1 = SCROLL_Y_MID - (SCROLL_DIST // 2)
                        y2 = SCROLL_Y_MID + (SCROLL_DIST // 2)
                        adb_swipe(x, y1, x, y2, SCROLL_DURATION_MS)
                        log(f"[scroll] {current_tab}: swipe DOWN (delta={delta})")

                    last_scroll_action = now

            # -------------------------
            # POT3 (map) - tylko w MAP
            # -------------------------
            if v2 is not None and current_tab == "MAP":
                if ema_map is None:
                    ema_map = float(v2)
                else:
                    ema_map = (1.0 - EMA_ALPHA_MAP) * ema_map + EMA_ALPHA_MAP * v2

                delta = int(ema_map) - MAP_CENTER
                if abs(delta) > MAP_DEADZONE and (now - last_map_action) >= MAP_INTERVAL:
                    # delta > 0 => pan right (swipe right)
                    if delta > 0:
                        x1 = MAP_X_MID - (MAP_DIST // 2)
                        x2 = MAP_X_MID + (MAP_DIST // 2)
                        y = MAP_Y_MID
                        adb_swipe(x1, y, x2, y, MAP_DURATION_MS)
                        log(f"[map] PAN RIGHT (delta={delta})")
                    else:
                        x1 = MAP_X_MID + (MAP_DIST // 2)
                        x2 = MAP_X_MID - (MAP_DIST // 2)
                        y = MAP_Y_MID
                        adb_swipe(x1, y, x2, y, MAP_DURATION_MS)
                        log(f"[map] PAN LEFT (delta={delta})")

                    last_map_action = now

        except SerialException as e:
            log(f"[serial] read failed: {e}. reconnecting...")
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(RECONNECT_DELAY_SEC)
            ser = open_serial()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExit.")
