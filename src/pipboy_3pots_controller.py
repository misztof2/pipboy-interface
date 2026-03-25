#!/usr/bin/env python3
import time
import subprocess
import serial
from serial.serialutil import SerialException

# -------------------------
# CONFIG
# -------------------------
PHONE = "192.168.0.114:5555"
SERIAL_PORT = "/dev/ttyUSB0"   # !!! zostaje !!!
BAUDRATE = 115200

POT_MIN = 0
POT_MAX = 1023

TABS = [
    ("STAT",  390, 45),
    ("INV",   505, 45),
    ("DATA",  615, 45),
    ("MAP",   750, 45),
    ("RADIO", 875, 45),
]
ZONES = len(TABS)

# --- POT1 behavior (NIE ZMIENIAMY DZIAŁANIA) ---
MIN_TAP_INTERVAL_SEC = 0.15
STABLE_SAMPLES_REQUIRED = 3
EMA_ALPHA = 0.55
MIN_ADC_CHANGE = 2
RECONNECT_DELAY_SEC = 0.5

# --- POT2 scroll (lists) ---
SCROLL_CENTER = 512
SCROLL_DEADZONE = 70
SCROLL_INTERVAL_SEC = 0.12
SCROLL_X = 520
SCROLL_Y_MID = 520
SCROLL_DIST = 420
SCROLL_DURATION_MS = 130
SCROLL_EMA_ALPHA = 0.70

# --- POT3 map pan (MAP tab only) ---
MAP_CENTER = 512
MAP_DEADZONE = 85
MAP_INTERVAL_SEC = 0.14
MAP_X_MID = 520
MAP_Y_MID = 520
MAP_DIST = 380
MAP_DURATION_MS = 140
MAP_EMA_ALPHA = 0.70

DEBUG = True

# POT2/POT3 last values (filled when Arduino sends CSV)
last_pot2 = None
last_pot3 = None

# -------------------------
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

def open_serial():
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=1)
            time.sleep(1.0)
            ser.reset_input_buffer()
            print(f"[serial] connected: {SERIAL_PORT} @ {BAUDRATE}")
            return ser
        except SerialException as e:
            print(f"[serial] open failed: {e}. retrying in {RECONNECT_DELAY_SEC}s")
            time.sleep(RECONNECT_DELAY_SEC)

def read_int_line(ser) -> int | None:
    """
    POT1: zwraca int jak wcześniej.
    Dodatkowo: jeśli linia ma CSV "v0,v1,v2", zapisuje last_pot2/last_pot3.
    """
    global last_pot2, last_pot3

    try:
        line = ser.readline()
        if not line:
            return None
        s = line.decode(errors="ignore").strip()
        if not s:
            return None

        # --- NEW: accept CSV without changing POT1 behavior ---
        if "," in s:
            parts = s.split(",")
            if len(parts) >= 3:
                try:
                    d0 = "".join(ch for ch in parts[0] if ch.isdigit() or ch == "-")
                    d1 = "".join(ch for ch in parts[1] if ch.isdigit() or ch == "-")
                    d2 = "".join(ch for ch in parts[2] if ch.isdigit() or ch == "-")
                    if d1 not in ("", "-"):
                        last_pot2 = clamp(int(d1), POT_MIN, POT_MAX)
                    if d2 not in ("", "-"):
                        last_pot3 = clamp(int(d2), POT_MIN, POT_MAX)
                    if d0 in ("", "-"):
                        return None
                    v = int(d0)
                    return clamp(v, POT_MIN, POT_MAX)
                except ValueError:
                    return None

        # --- Original single-value behavior ---
        digits = "".join(ch for ch in s if ch.isdigit() or ch == "-")
        if digits in ("", "-"):
            return None
        v = int(digits)
        return clamp(v, POT_MIN, POT_MAX)

    except (SerialException, OSError) as e:
        raise SerialException(str(e))

def main() -> None:
    ser = open_serial()

    # ===== POT1 state (unchanged logic) =====
    ema = None
    last_raw = None

    current_zone = None
    candidate_zone = None
    stable_count = 0

    last_tap_time = 0.0

    # ===== POT2 scroll state =====
    scroll_ema = None
    last_scroll_time = 0.0

    # ===== POT3 map state =====
    map_ema = None
    last_map_time = 0.0

    print("Pip-Boy 3-pot controller: POT1 tabs (original), POT2 scroll, POT3 map-pan")
    print(f"ADB target: {PHONE}")
    print("Ctrl+C to exit\n")

    while True:
        try:
            raw = read_int_line(ser)
            if raw is None:
                continue

            # ========= POT1 (ORIGINAL BEHAVIOR) =========
            if last_raw is not None and abs(raw - last_raw) < MIN_ADC_CHANGE:
                # NOTE: this also reduces spam; unchanged
                continue
            last_raw = raw

            if ema is None:
                ema = float(raw)
            else:
                ema = (1.0 - EMA_ALPHA) * ema + EMA_ALPHA * raw

            z = value_to_zone(int(ema))

            if candidate_zone is None or z != candidate_zone:
                candidate_zone = z
                stable_count = 1
            else:
                stable_count += 1

            now_wall = time.time()
            if stable_count >= STABLE_SAMPLES_REQUIRED:
                if z != current_zone and (now_wall - last_tap_time) >= MIN_TAP_INTERVAL_SEC:
                    name, x, y = TABS[z]
                    adb_tap(x, y)
                    last_tap_time = now_wall
                    current_zone = z
                    print(f"ZONE -> {z} ({name}) raw={raw} ema={int(ema)} tap=({x},{y})")
                stable_count = STABLE_SAMPLES_REQUIRED

            # ========= CONTEXT (tab) =========
            # current_zone is the current tab index
            tab_name = TABS[current_zone][0] if current_zone is not None else None

            now = time.monotonic()

            # ========= POT2 scroll =========
            # Only in list tabs: STAT/INV/DATA/RADIO
            if tab_name in ("STAT", "INV", "DATA", "RADIO") and last_pot2 is not None:
                if scroll_ema is None:
                    scroll_ema = float(last_pot2)
                else:
                    scroll_ema = (1.0 - SCROLL_EMA_ALPHA) * scroll_ema + SCROLL_EMA_ALPHA * last_pot2

                delta = int(scroll_ema) - SCROLL_CENTER
                if abs(delta) > SCROLL_DEADZONE and (now - last_scroll_time) >= SCROLL_INTERVAL_SEC:
                    if delta > 0:
                        # scroll down -> swipe up
                        x = SCROLL_X
                        y1 = SCROLL_Y_MID + (SCROLL_DIST // 2)
                        y2 = SCROLL_Y_MID - (SCROLL_DIST // 2)
                        adb_swipe(x, y1, x, y2, SCROLL_DURATION_MS)
                    else:
                        # scroll up -> swipe down
                        x = SCROLL_X
                        y1 = SCROLL_Y_MID - (SCROLL_DIST // 2)
                        y2 = SCROLL_Y_MID + (SCROLL_DIST // 2)
                        adb_swipe(x, y1, x, y2, SCROLL_DURATION_MS)

                    last_scroll_time = now

            # ========= POT3 map pan =========
            if tab_name == "MAP" and last_pot3 is not None:
                if map_ema is None:
                    map_ema = float(last_pot3)
                else:
                    map_ema = (1.0 - MAP_EMA_ALPHA) * map_ema + MAP_EMA_ALPHA * last_pot3

                delta = int(map_ema) - MAP_CENTER
                if abs(delta) > MAP_DEADZONE and (now - last_map_time) >= MAP_INTERVAL_SEC:
                    if delta > 0:
                        # pan right
                        x1 = MAP_X_MID - (MAP_DIST // 2)
                        x2 = MAP_X_MID + (MAP_DIST // 2)
                        y = MAP_Y_MID
                        adb_swipe(x1, y, x2, y, MAP_DURATION_MS)
                    else:
                        # pan left
                        x1 = MAP_X_MID + (MAP_DIST // 2)
                        x2 = MAP_X_MID - (MAP_DIST // 2)
                        y = MAP_Y_MID
                        adb_swipe(x1, y, x2, y, MAP_DURATION_MS)

                    last_map_time = now

        except SerialException as e:
            print(f"[serial] read failed: {e}. reconnecting...")
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
