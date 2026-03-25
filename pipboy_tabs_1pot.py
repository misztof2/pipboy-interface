#!/usr/bin/env python3
import time
import subprocess
import serial
from serial.serialutil import SerialException

# -------------------------
# CONFIG
# -------------------------
PHONE = "192.168.0.114:5555"
SERIAL_PORT = "/dev/ttyUSB0"
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

# Responsiveness
MIN_TAP_INTERVAL_SEC = 0.15

# Stability (anti-chatter)
STABLE_SAMPLES_REQUIRED = 3

# Smoothing (higher = faster response)
EMA_ALPHA = 0.55

# Ignore tiny jitter
MIN_ADC_CHANGE = 2

RECONNECT_DELAY_SEC = 0.5

# -------------------------
def adb_tap(x: int, y: int) -> None:
    subprocess.run(
        ["adb", "-s", PHONE, "shell", "input", "tap", str(x), str(y)],
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
            # Give Arduino time after reset
            time.sleep(1.0)
            ser.reset_input_buffer()
            print(f"[serial] connected: {SERIAL_PORT} @ {BAUDRATE}")
            return ser
        except SerialException as e:
            print(f"[serial] open failed: {e}. retrying in {RECONNECT_DELAY_SEC}s")
            time.sleep(RECONNECT_DELAY_SEC)

def _parse_first_int(s: str) -> int | None:
    """
    Accepts:
      - "123"
      - "123,456,789"  -> returns 123
      - "POT=123"      -> returns 123
    Rejects:
      - empty / invalid
    """
    s = s.strip()
    if not s:
        return None

    # If CSV, take the first field only (THIS IS THE FIX)
    if "," in s:
        s = s.split(",", 1)[0].strip()

    # Extract digits from the remaining field
    digits = "".join(ch for ch in s if ch.isdigit() or ch == "-")
    if digits in ("", "-"):
        return None
    try:
        return int(digits)
    except ValueError:
        return None

def read_int_line(ser) -> int | None:
    try:
        line = ser.readline()
        if not line:
            return None
        s = line.decode(errors="ignore").strip()
        v = _parse_first_int(s)
        if v is None:
            return None
        return clamp(v, POT_MIN, POT_MAX)
    except (SerialException, OSError) as e:
        raise SerialException(str(e))

def main() -> None:
    ser = open_serial()

    ema = None
    last_raw = None

    current_zone = None
    candidate_zone = None
    stable_count = 0

    last_tap_time = 0.0

    print("Pip-Boy 1-pot tabs controller (original behavior, CSV-safe parser)")
    print(f"ADB target: {PHONE}")
    print("Ctrl+C to exit\n")

    while True:
        try:
            raw = read_int_line(ser)
            if raw is None:
                continue

            # jitter filter
            if last_raw is not None and abs(raw - last_raw) < MIN_ADC_CHANGE:
                continue
            last_raw = raw

            # EMA smoothing
            if ema is None:
                ema = float(raw)
            else:
                ema = (1.0 - EMA_ALPHA) * ema + EMA_ALPHA * raw

            z = value_to_zone(int(ema))

            # debounce / stability
            if candidate_zone is None or z != candidate_zone:
                candidate_zone = z
                stable_count = 1
            else:
                stable_count += 1

            now = time.time()
            if stable_count >= STABLE_SAMPLES_REQUIRED:
                if z != current_zone and (now - last_tap_time) >= MIN_TAP_INTERVAL_SEC:
                    name, x, y = TABS[z]
                    adb_tap(x, y)
                    last_tap_time = now
                    current_zone = z
                    print(f"ZONE -> {z} ({name}) raw={raw} ema={int(ema)} tap=({x},{y})")
                stable_count = STABLE_SAMPLES_REQUIRED

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
