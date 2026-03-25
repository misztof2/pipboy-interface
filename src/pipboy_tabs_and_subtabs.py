#!/usr/bin/env python3
import time
import subprocess
import serial
from serial.serialutil import SerialException

PHONE = "192.168.0.114:5555"
SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 115200

POT_MIN = 0
POT_MAX = 1023

# =========================
# MAIN TABS (POT1)
# =========================
TABS = [
    ("STAT",  390, 45),
    ("INV",   505, 45),
    ("DATA",  615, 45),
    ("MAP",   750, 45),
    ("RADIO", 875, 45),
]
MAIN_ZONES = len(TABS)

# POT1: jump-on-stop config
POT1_MOVE_THRESHOLD = 5     # wykrycie ruchu gałki
POT1_STOP_DELAY_SEC = 0.08  # po tylu sekundach bez ruchu: 1 tap docelowy
POT1_EMA_ALPHA = 0.85       # szybkie wygładzanie bez laga
POT1_MIN_ADC_CHANGE = 2     # ignoruj mikro-jitter
POT1_MIN_TAP_INTERVAL_SEC = 0.05  # minimalny odstęp (zostawiam minimalny, żeby ADB nie zabić)

# =========================
# SUBTABS (POT2) - NIE RUSZAMY LOGIKI
# =========================
SUBTABS = {
    "STAT": {"count": 3, "right": (464, 100), "left": (300, 100)},
    "INV":  {"count": 7, "right": (605, 100), "left": (415, 100)},
    "DATA": {"count": 3, "right": (750, 100), "left": (480, 100)},
}

POT2_MOVE_THRESHOLD = 5  # wykrycie ruchu

# =========================
# POT3: values inside subtabs (STAT only)
# =========================
POT3_X = 500
SPECIAL_Y = [200, 260, 320, 380, 450, 510, 570]  # 7 pozycji
PERKS_Y   = [200, 260, 320, 380, 450]            # 5 pozycji

# POT3: jump-on-stop config
POT3_MOVE_THRESHOLD = 5
POT3_STOP_DELAY_SEC = 0.06
POT3_MIN_TAP_INTERVAL_SEC = 0.03  # minimalny
POT3_MIN_ADC_CHANGE = 2

RECONNECT_DELAY_SEC = 0.5

# =========================

def adb_tap(x: int, y: int) -> None:
    subprocess.run(
        ["adb", "-s", PHONE, "shell", "input", "tap", str(x), str(y)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v

def value_to_zone(v: int, zones: int) -> int:
    span = POT_MAX - POT_MIN + 1
    idx = int((v - POT_MIN) * zones / span)
    if idx < 0:
        return 0
    if idx >= zones:
        return zones - 1
    return idx

def open_serial():
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.2)
            time.sleep(1.0)
            ser.reset_input_buffer()
            print(f"[serial] connected: {SERIAL_PORT}")
            return ser
        except SerialException as e:
            print(f"[serial] open failed: {e}")
            time.sleep(RECONNECT_DELAY_SEC)

def parse_line(line: bytes):
    """
    Accepts:
      - "v0" -> (v0, None, None)
      - "v0,v1" -> (v0, v1, None)
      - "v0,v1,v2" -> (v0, v1, v2)
    """
    if not line:
        return None, None, None
    s = line.decode(errors="ignore").strip()
    if not s:
        return None, None, None

    if "," in s:
        parts = s.split(",")
        try:
            v0 = int(parts[0])
            v1 = int(parts[1]) if len(parts) >= 2 and parts[1] != "" else None
            v2 = int(parts[2]) if len(parts) >= 3 and parts[2] != "" else None
            v0 = clamp(v0, POT_MIN, POT_MAX)
            v1 = clamp(v1, POT_MIN, POT_MAX) if v1 is not None else None
            v2 = clamp(v2, POT_MIN, POT_MAX) if v2 is not None else None
            return v0, v1, v2
        except Exception:
            return None, None, None

    try:
        v0 = int(s)
        return clamp(v0, POT_MIN, POT_MAX), None, None
    except Exception:
        return None, None, None


def main():
    ser = open_serial()

    # =========================
    # POT1 state (jump-on-stop)
    # =========================
    ema1 = None
    last_pot1_raw = None
    moving1 = False
    last_move1 = 0.0
    pending_main_zone = None
    current_main_zone = None
    last_main_tap = 0.0

    # =========================
    # POT2 state (UNCHANGED)
    # =========================
    last_sub_raw = None
    current_sub_zone = 0  # 0..count-1, reset on main change

    # =========================
    # POT3 state (jump-on-stop)
    # =========================
    last_pot3_raw = None
    moving3 = False
    last_move3 = 0.0
    pending_pot3_zone = None
    current_pot3_zone = None
    last_pot3_tap = 0.0

    print("Running controller: POT1 jump-on-stop, POT2 unchanged, POT3 jump-on-stop")

    while True:
        try:
            v0, v1, v2 = parse_line(ser.readline())
            now_m = time.monotonic()
            now_w = time.time()

            # -------------------------
            # POT1 (MAIN TABS) - jump-on-stop
            # -------------------------
            if v0 is not None:
                # jitter ignore
                if last_pot1_raw is not None and abs(v0 - last_pot1_raw) < POT1_MIN_ADC_CHANGE:
                    pass
                else:
                    # detect motion
                    if last_pot1_raw is not None and abs(v0 - last_pot1_raw) >= POT1_MOVE_THRESHOLD:
                        moving1 = True
                        last_move1 = now_m

                    last_pot1_raw = v0

                    # EMA smoothing
                    if ema1 is None:
                        ema1 = float(v0)
                    else:
                        ema1 = (1.0 - POT1_EMA_ALPHA) * ema1 + POT1_EMA_ALPHA * v0

                    pending_main_zone = value_to_zone(int(ema1), MAIN_ZONES)

            # If user stopped moving POT1 -> single tap to final zone
            if moving1 and (now_m - last_move1) >= POT1_STOP_DELAY_SEC and pending_main_zone is not None:
                moving1 = False
                if pending_main_zone != current_main_zone and (now_w - last_main_tap) >= POT1_MIN_TAP_INTERVAL_SEC:
                    name, x, y = TABS[pending_main_zone]
                    adb_tap(x, y)
                    last_main_tap = now_w
                    current_main_zone = pending_main_zone

                    # reset subtab + pot3 selection on main change
                    current_sub_zone = 0
                    current_pot3_zone = None
                    pending_pot3_zone = None

                    print("MAIN ->", name)

            # Need known main tab to route POT2/POT3
            if current_main_zone is None:
                continue
            main_name = TABS[current_main_zone][0]

            # -------------------------
            # POT2 (SUBTABS) - UNCHANGED behavior
            # -------------------------
            if v1 is not None and main_name in SUBTABS:
                if last_sub_raw is not None and abs(v1 - last_sub_raw) < POT2_MOVE_THRESHOLD:
                    pass
                else:
                    last_sub_raw = v1

                    sub_conf = SUBTABS[main_name]
                    zones = sub_conf["count"]
                    new_zone = value_to_zone(v1, zones)

                    if new_zone != current_sub_zone:
                        if new_zone > current_sub_zone:
                            adb_tap(*sub_conf["right"])
                        else:
                            adb_tap(*sub_conf["left"])

                        current_sub_zone = new_zone

                        # reset POT3 selection when subtab changes
                        current_pot3_zone = None
                        pending_pot3_zone = None
                        moving3 = False

                        print("SUB ->", main_name, current_sub_zone)

            # -------------------------
            # POT3 (VALUES) - jump-on-stop, only STAT + SPECIAL/PERKS
            # -------------------------
            if v2 is None:
                continue

            if main_name != "STAT":
                continue

            # STAT subtab mapping:
            # 0 = STATUS, 1 = SPECIAL, 2 = PERKS
            if current_sub_zone == 1:
                y_list = SPECIAL_Y
            elif current_sub_zone == 2:
                y_list = PERKS_Y
            else:
                continue

            # jitter ignore
            if last_pot3_raw is not None and abs(v2 - last_pot3_raw) < POT3_MIN_ADC_CHANGE:
                pass
            else:
                # detect motion
                if last_pot3_raw is not None and abs(v2 - last_pot3_raw) >= POT3_MOVE_THRESHOLD:
                    moving3 = True
                    last_move3 = now_m

                last_pot3_raw = v2
                pending_pot3_zone = value_to_zone(v2, len(y_list))

            # If user stopped moving POT3 -> single tap to final position
            if moving3 and (now_m - last_move3) >= POT3_STOP_DELAY_SEC and pending_pot3_zone is not None:
                moving3 = False
                if pending_pot3_zone != current_pot3_zone and (now_w - last_pot3_tap) >= POT3_MIN_TAP_INTERVAL_SEC:
                    adb_tap(POT3_X, y_list[pending_pot3_zone])
                    last_pot3_tap = now_w
                    current_pot3_zone = pending_pot3_zone
                    # print("POT3 ->", "SPECIAL" if current_sub_zone == 1 else "PERKS", current_pot3_zone)

        except SerialException:
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(RECONNECT_DELAY_SEC)
            ser = open_serial()


if __name__ == "__main__":
    main()
