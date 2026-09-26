# litho-control

Control for a maskless lithography setup: a DLP projector exposes the pattern,
a camera watches it, and an XYZ stage moves the sample.

| directory | hardware | status |
| --- | --- | --- |
| [`camera/`](camera/README.md) | Basler acA1920-40um (USB3, mono) | live view with DLP flicker removed; flicker measurement |
| [`projector/`](projector/README.md) | TI DLP471TPEVM over HDMI + USB | test patterns, camera-to-DMD calibration, DMD fault scan |
| [`xyz_stage/`](xyz_stage/README.md) | Arduino Uno + CNC Shield V3, 3 × DRV8825, 3 steppers (COM4) | `xyz1` firmware (drivers powered only while moving) |
| [`ui/`](ui/README.md) | all three | the control page: stage control (Web Serial), projector display at native resolution, and side-by-side PROJECTOR mirror and deflickered CAMERA panes, served by `ui/server.py` |

**To run everything:** `.\ui\start.ps1`, then open `http://localhost:8765`.

## Setup

Windows 11, Python 3.12.

1. Install the Basler **pylon SDK**. It ships the USB3 driver and runtime that
   `pypylon` binds to; without it, `import pypylon` fails.
2. Install TI's **DLP EVM GUI** for the projector's USB control interface.
3. Install the **Arduino IDE** (`winget install ArduinoSA.IDE.stable --scope user`)
   and its AVR core. Only needed to flash the stage firmware; see
   [`xyz_stage/`](xyz_stage/README.md#flash-the-firmware).
4. Create the venv at the repo root and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Optionally, activate the venv (`.\.venv\Scripts\Activate.ps1`; if PowerShell
blocks it, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy
RemoteSigned`). Every command in these READMEs runs from the repo root.

## Hardware check

```powershell
# projector on USB (must be powered on — it does not enumerate otherwise)
Get-PnpDevice -PresentOnly | Where-Object InstanceId -match 'VID_0451&PID_7540'
# projector on HDMI
Get-PnpDevice -Class Monitor -PresentOnly
# stage Arduino (expects COM4; --probe also checks the firmware)
.\.venv\Scripts\python.exe xyz_stage\find_arduino.py --probe
# camera
.\.venv\Scripts\python.exe -c "from pypylon import pylon; print(pylon.TlFactory.GetInstance().EnumerateDevices()[0].GetModelName())"
```

## Debugging log

Newest first. The details are in each directory's README.

| date | area | issue | status |
| --- | --- | --- | --- |
| 2026-09-26 | xyz_stage | Homing (`xyz_stage/homing.py`). X homed on D9 (− side, limits 0..12 mm). Y's switch was detached: the seek ran to its limit and stalled at the end stop, and after reattaching, the switch isn't reached before the stop. Also fixed: replies garbled after the drivers switched off (2 of 10; firmware now pauses 50 ms). Measured 400 steps/mm (full step). [Details](xyz_stage/README.md#2026-09-26--homing-x-done-y-switch-detached-z-not-started--open) | X done; Y needs its switch remounted, then `homing.py --axes Y Z` |
| 2026-09-26 | xyz_stage | Limit switches checked by hand with `xyz_stage/limit_switches.py`: D9, D10 and D11 all switch cleanly, normally open to GND (D12 unused). Which pin belongs to which axis is found by homing. [Details](xyz_stage/README.md#2026-09-26--limit-switches-checked-by-hand--working) | working |
| 2026-09-25 | xyz_stage | DRV8825s squeal whenever powered, idle included. The firmware now powers them only during moves, so idle is silent (user confirmed); a brief squeal remains during moves. [Details](xyz_stage/README.md#2026-09-25--drv8825-squeal--idle-fixed-during-moves-open-hardware) | idle fixed; for moves: lower Vref, try microstepping, or swap to TMC2209 |
| 2026-09-25 | camera + projector | Camera flicker explained. The light repeats at 240 Hz (4 × 60 Hz) with a 1.25 ms dark gap, so short exposures that land in it come out black (predicted 26.8 %, measured 26.3 % at 1000 µs). Frame rates of 240/n phase-lock and can sit dark for seconds (3.3 s at 30 fps). [Camera side](camera/README.md#why-the-camera-sees-flicker-measured-2026-09-25), [waveform](projector/README.md#2026-09-25--why-the-camera-sees-flicker-240-hz-illumination-with-a-dark-gap--explained) | worked around: `camera\run.ps1` now uses full res, 400 µs, `--keep-frac 0.8`, 0.5 s max hold, for 23 updates/s and a longest hold of 98 ms ([settings](camera/README.md#recommended-settings-live-view-of-the-projector)). The full fix is to cut the light ~10× and use a 4167/8333/16667 µs exposure (not yet tested) |
| 2026-09-25 | projector | No light at all. The GUI showed "Ready; Curtain" and all LEDs disabled, and Set wouldn't enable them. [Details](projector/README.md#2026-09-25--no-light-leds-off-and-wont-enable-gui-shows-ready-curtain--fixed) | fixed: a loose LED wire |
| 2026-09-25 | xyz_stage | The Uno was running a different sketch ("Variable Speed Controller"), not the `xyz1` firmware the UI needs. [Details](xyz_stage/README.md#2026-09-25--set-up-xyz1-flashed) | done: old flash backed up, `xyz1` flashed; the stored position is stale, so zero before use |
| 2026-09-25 | projector | Four 128-mirror-column bands at DMD mirror columns 832–960, 1216–1344, 1472–1600 and 1728+ show wrong data (stuck on, noise, stale content), depending on the image. The mirrors themselves work, which points to the controller-to-DMD flex or DMD seating. [Details](projector/README.md#2026-09-25--vertical-stripes-on-the-projected-image--open) | open: reseat the flex, then run the GUI internal test patterns |
| 2026-09-25 | projector | Not detected on USB | fixed: the projector was switched off |
