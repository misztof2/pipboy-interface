# Pip-Boy Raspberry Pi Interface

## Overview
This project recreates a Fallout Pip-Boy interface using:
- Raspberry Pi (video output to CRT)
- Android phone (Pip-Boy app)
- Arduino (potentiometer input)

The system allows physical control of the UI using analog knobs.

## Architecture
- Arduino reads potentiometers (A0, A1, A2)
- Data sent via Serial to Raspberry Pi
- Python script maps values → UI zones
- Raspberry Pi sends ADB touch events to Android device
- Screen mirrored via scrcpy → CRT monitor

## Features
- Instant tab switching (no iteration)
- Multi-potentiometer control:
  - Pot 1 → main tabs
  - Pot 2 → subtabs
  - Pot 3 → item selection
- Smooth filtering (EMA)
- Threshold-based input stabilization

## Hardware
- Raspberry Pi 4
- Arduino Uno
- 3x potentiometers (10kΩ)
- MCP3008 ADC (optional)
- CRT monitor + HDMI2AV adapter

## How to run

### 1. Connect Arduino
/dev/ttyUSB0

### 2. Connect phone via ADB
```bash
adb connect 192.168.X.X:5555

### 3. Run script
cd src
python3 pipboy_tabs_and_subtabs.py ---final version with 3 potenciometers. 

Demo

https://youtu.be/AjLkpR2ltNw

Notes
Designed for real-time interaction
No cloud dependencies
Fully local system