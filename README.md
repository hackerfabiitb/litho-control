# litho-control

Control for a maskless lithography setup: a DLP projector exposes the pattern,
a camera watches it, and an XYZ stage moves the sample.

| directory | hardware | status |
| --- | --- | --- |
| [`camera/`](camera/README.md) | Basler acA1920-40um (USB3, mono) | live view with DLP flicker removed; flicker measurement |
| [`projector/`](projector/README.md) | TI DLP471TPEVM over HDMI + USB | test patterns, camera-to-DMD calibration, DMD fault scan. DMD: 1920×1080 mirrors at 5.4 µm, shown as 3840×2160 by pixel shifting; on the wafer (10×, nominal 1070 µm field) 1 pattern px = 0.279 µm, 1 mirror = 0.557 µm |
| [`xyz_stage/`](xyz_stage/README.md) | Arduino Uno + CNC Shield V3, 3 × DRV8825, 3 steppers (COM4) | `xyz1` firmware (drivers powered only while moving) |
| [`patterns/`](patterns/README.md) | images to project, and the code that makes them | `calibrate_deflicker.png` (the UI's deflicker calibration), `white_4K.jpg` (the UI's White button), `checker_NNNNpx.png` (3840×2160, square size zero-padded to 4 digits, e.g. `checker_0100px.png`; made by `patterns/scripts/make_checkerboards.py [sizes…]`; load at 1:1 pixels). `lib/` + `scripts/`: pixel-perfect pattern generation from patterns-py (resolution tests, the dose test's `focus_markers` and `res_test`) |
| [`ui/`](ui/README.md) | all three | the control page: stage control (through the server), projector display at native resolution, and side-by-side PROJECTOR mirror and deflickered CAMERA panes, served by `ui/server.py` |

**To run everything:** `.\ui\start.ps1`. It opens the UI in its own Firefox window; Ctrl+C or closing the window stops everything.
**Dose test:** `ui\dose_test.py` exposes a row of fields along X at different times, with autofocus on blue markers before each ([ui/README.md](ui/README.md#dose-test)).
While a script runs (Calibrate deflicker, Fit transform, Autofocus, Scan, or a test script run through the server with `ui/script_client.py`), the UI's controls turn yellow and lock, and the Script pane under the serial log shows it with a Stop button ([ui/README.md](ui/README.md#scripts-script-mode)).

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
| 2026-10-07 | xyz_stage + ui | Even at 4 ms/step, single 154-step Y moves in a scan shifted the view 0–960 px (median 3.0 px/step against ~4.15). The outer ring barely moved in Y and slid ~190 px in X. Over a column the total is about right, so Y lags and catches up (stick-slip). The stitcher had also matched camera-fixed features (DMD fault band, dust) at zero shift: "HACKER FAB" stitched 2–3 times. [Stage](xyz_stage/README.md#2026-10-07--during-a-scan-154-step-y-moves-at-4-msstep-travel-0960-px--open), [stitching](ui/README.md#2026-10-07--stitching-fixed-for-scan-02-32-17-hacker-fab-twice-holes) | stitching fixed (camera-fixed mask, whole-frame pair search, robust solve): 02-32-17 re-stitched with the text once. Stage open: check Y for binding, then Vref |
| 2026-10-07 | xyz_stage | Y loses ~36 % of its steps at step delays under ~4 ms (incl. the 800 µs default), silently; X is fine from 400 µs; nothing moves at <= 200 µs. Found when a scan stitched "HACKER FAB" twice. [Details](xyz_stage/README.md#2026-10-07--y-loses-36--of-its-steps-faster-than-4-msstep-nothing-moves-at-20-µs--open) | open: re-zero Y; check Y's Vref and mechanics. The scan moves Y at >= 4000 µs; even so, single Y moves vary (see the row above) |
| 2026-10-07 | xyz_stage | Optical tracking shows real stage behaviour: X travels ~265 µm per 100 steps instead of 250 (or the field is ~6 % smaller than nominal), ~20 µm backlash on reversing, ~8 µm creep at rest with drivers released, and camera axes ~3.5° off the stage's. A Y move inside the soft limits hit a physical stop. [Details](xyz_stage/README.md#2026-10-07--optical-tracking-shows-backlash-a-6--scale-difference-and-creep-at-rest-y-hit-a-stop--open) | open: fix Y limits and re-zero Y; measure the step size against a projected scale; try Hold for the creep |
| 2026-10-07 | camera | Brightness vs exposure and bloom (`camera/linearity_probe.py`). Below one projector period (4.2 ms), deflickered brightness isn't proportional to exposure: the deflicker shows the brightest slices of the pulsed light, ~6× the average; above it, brightness is linear. White is ~50× the calibration image and saturates from ~200 µs. Optical glare: a **veil** (a white area lifts the whole frame by ~10 % of its brightness; 80–86 % of a square's light lands outside it) and an **18 % ghost** 439 px up (beamsplitter double reflection); not LED power. [Details](camera/README.md#2026-10-07--brightness-vs-exposure-bloom-on-white-a-ghost-image--explained-optics-open) | explained. Fixed: deflicker calibration was scaled linearly with exposure (cutoff 4× too low at 400 µs); it now applies at its own exposure only. Added: Fit transform (16:9 view of just the projected field). Open (optics): wedged/AR or pellicle beamsplitter for the ghost, baffles for the veil |
| 2026-10-03 | camera | Glare after reassembling the litho: a projector-light haze over the whole feed (dark checker squares at 59 % of bright, 81 % outside the image), rising towards the bottom-left corner, so likely flare from something lit just outside the field. The image is now rotated ~30° and fully in view. [Details](camera/README.md#2026-10-03--glare--haze-over-the-whole-feed-after-reassembling-the-litho--open) | open: extend the projector display and compare black / white / small square; shade suspects by hand; recalibrate |
| 2026-09-26 | projector | Stripes after the flex reseat: the bands **moved** (now input x 1408–1664 and 2944 onward; 2944+ lights up on black). They fail only when a row within a block is unbalanced (black, white, horizontal bars, sparse lines); bars and checkers with ~50 % on are clean everywhere, consistent with baseline wander on marginal high-speed lanes. No usable content fix. [Details](projector/README.md#2026-09-26--stripes-after-the-flex-reseat-moved-and-fail-on-unbalanced-rows--open) | open: fix is hardware (clean or replace the flex). Meanwhile expose only in x 0–1408 / 1664–2944 and block x ≥ 2944 optically. Optional: scan the DMD HSSI receiver settings (needs a go-ahead) |
| 2026-09-26 | projector | "Ready; Curtain" and no light again, this time after reseating the DMD flex. Decoded the GUI's System Status reply (`projector/evm_status.py`): **`DmdInitErr`**, the controller failed to start the DMD at boot; the LED driver reports no fault. [Details](projector/README.md#2026-09-26--ready-curtain-no-light-after-reseating-the-dmd-flex-dmd-init-error--fixed) | fixed: a second, careful reseat |
| 2026-09-26 | xyz_stage | Homing (`xyz_stage/homing.py`). X homed on D9 (− side, limits 0..12 mm). Y's switch was detached: the seek ran to its limit and stalled at the end stop, and after reattaching, the switch isn't reached before the stop. Z's switch is also out of reach. Also fixed: replies garbled after the drivers switched off (2 of 10; firmware now pauses 50 ms). Measured 400 steps/mm (full step). [Details](xyz_stage/README.md#2026-09-26--homing-x-on-its-switch-y-and-z-zeroed-by-hand--open) | X homes on its switch. Y and Z switches sit beyond their end stops, so both were zeroed by hand (`--set-zero`): Y 1 mm off its + stop with 11 mm travel, Z at its + end with 12 mm. Later runs trust their saved positions. Still to do: remount the Y and Z switches, then `homing.py --discover --axes Y Z` |
| 2026-09-26 | xyz_stage | Limit switches checked by hand with `xyz_stage/limit_switches.py`: D9, D10 and D11 all switch cleanly, normally open to GND (D12 unused). Which pin belongs to which axis is found by homing. [Details](xyz_stage/README.md#2026-09-26--limit-switches-checked-by-hand--working) | working |
| 2026-09-25 | xyz_stage | DRV8825s squeal whenever powered, idle included. The firmware now powers them only during moves, so idle is silent (user confirmed); a brief squeal remains during moves. [Details](xyz_stage/README.md#2026-09-25--drv8825-squeal--idle-fixed-during-moves-open-hardware) | idle fixed; for moves: lower Vref, try microstepping, or swap to TMC2209 |
| 2026-09-25 | camera + projector | Camera flicker explained. The light repeats at 240 Hz (4 × 60 Hz) with a 1.25 ms dark gap, so short exposures that land in it come out black (predicted 26.8 %, measured 26.3 % at 1000 µs). Frame rates of 240/n phase-lock and can sit dark for seconds (3.3 s at 30 fps). [Camera side](camera/README.md#why-the-camera-sees-flicker-measured-2026-09-25), [waveform](projector/README.md#2026-09-25--why-the-camera-sees-flicker-240-hz-illumination-with-a-dark-gap--explained) | worked around: `camera\run.ps1` now uses full res, 400 µs, `--keep-frac 0.8`, 0.5 s max hold, for 23 updates/s and a longest hold of 98 ms ([settings](camera/README.md#recommended-settings-live-view-of-the-projector)). The full fix is to cut the light ~10× and use a 4167/8333/16667 µs exposure (not yet tested) |
| 2026-09-25 | projector | No light at all. The GUI showed "Ready; Curtain" and all LEDs disabled, and Set wouldn't enable them. [Details](projector/README.md#2026-09-25--no-light-leds-off-and-wont-enable-gui-shows-ready-curtain--fixed) | fixed: a loose LED wire |
| 2026-09-25 | xyz_stage | The Uno was running a different sketch ("Variable Speed Controller"), not the `xyz1` firmware the UI needs. [Details](xyz_stage/README.md#2026-09-25--set-up-xyz1-flashed) | done: old flash backed up, `xyz1` flashed; the stored position is stale, so zero before use |
| 2026-09-25 | projector | Four 128-mirror-column bands at DMD mirror columns 832–960, 1216–1344, 1472–1600 and 1728+ show wrong data (stuck on, noise, stale content), depending on the image. The mirrors themselves work, which points to the controller-to-DMD flex or DMD seating. [Details](projector/README.md#2026-09-25--vertical-stripes-on-the-projected-image--open) | superseded by the 2026-09-26 entry above (the bands moved) |
| 2026-09-25 | projector | Not detected on USB | fixed: the projector was switched off |
