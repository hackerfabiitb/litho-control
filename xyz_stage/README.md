# xyz_stage — Arduino stepper stage

An Arduino Uno with a stepper motor shield drives three steppers (X, Y, Z).
The firmware and browser UI come from the older
[hackerfabiitb/litho-ui](https://github.com/hackerfabiitb/litho-ui) repo
(copied at commit `bda24e5`). The UI now lives in [`ui/`](../ui/README.md),
served by `ui/server.py` together with the projector and camera panes.

| file | what it is | origin |
| --- | --- | --- |
| `firmware/xyz1/xyz1.ino` | stage firmware: serial protocol, soft limits, EEPROM position memory | litho-ui, changed (driver power) |
| `litho-ui_README.md` | original README: full serial protocol, EEPROM layout, UI features | litho-ui |
| `XYZ_Stage_Controller_Manual.docx` | original user manual | litho-ui |
| `find_arduino.py` | finds the Arduino's COM port, and can check which firmware is on it | new |
| `firmware/backup/` | the Uno's flash before `xyz1` was loaded | new |

litho-ui's `stage_controller.html` became `ui/index.html`.
`camera_server.py`, `start.bat` and the interim `start_ui.ps1` were replaced
by `ui/server.py` and `ui/start.ps1`; they remain in git history.

## Hardware

| item | detail |
| --- | --- |
| board | Arduino Uno (genuine, USB `2341:0043`) on **COM4** |
| other serial port | COM3 is a CH343 USB-serial adapter (`1A86:55D3`) — not the stage |
| shield | Arduino CNC Shield V3 |
| drivers | 3 × DRV8825 (all share enable pin 8, active LOW). Current limit: I = 2 × Vref |
| pins (as used by `xyz1.ino`) | X step 3 / dir 6, Y step 2 / dir 5, Z step 4 / dir 7, enable 8 |
| baud | 115200 |

The pins follow the common CNC Shield V3 layout, with X and Y swapped: the
firmware's X uses the shield's Y slot and vice versa.

## Find the port

```powershell
.\.venv\Scripts\python.exe xyz_stage\find_arduino.py           # -> Arduino: COM4
.\.venv\Scripts\python.exe xyz_stage\find_arduino.py --probe   # also print the boot banner
```

`--probe` opens the port, which **resets the Uno**, then sends only `P` (a
position query). Nothing moves. From Python, use
`from find_arduino import find_arduino`.

## Run the UI

```powershell
.\ui\start.ps1
```

Then click **Connect** and choose **COM4**. Web Serial only lets a person
pick the port, so no script can connect for you. Only one program can hold
COM4 at a time: disconnect in the UI before running `find_arduino.py
--probe` or flashing.

- **Projector and camera panes:** see [ui/README.md](../ui/README.md).
- **Keys:** arrow keys jog X/Y, PgUp/PgDn jog Z. See `litho-ui_README.md`
  for the rest.
- **Motor toggle:** "Motors released when idle (quiet)" by default; see
  [Driver power](#driver-power).

## Driver power

The DRV8825s squeal whenever they are powered, standing still included. So
the firmware only powers them during a move. It switches them on 2 ms before
the first step and off 20 ms after the last one. This differs from the
original litho-ui firmware, which powered them from boot onwards.

| command | effect |
| --- | --- |
| `D` (default at boot) | release the drivers when idle: quiet, but no holding torque |
| `E` | keep the drivers powered when idle: holding torque, squeals |

Moves always power the drivers, whichever mode is set. Under the original
firmware, `D` let moves count steps with the drivers off, so the position
readout drifted away from the real position.

Watch for an axis that moves by itself when unpowered, for example Z sinking
under gravity or a stage being bumped. The firmware can't see that movement,
so the position readout would go wrong. Use `E` (the UI toggle) for that case.

## Flash the firmware

The Arduino IDE 2.3.10 is installed per user via winget
(`ArduinoSA.IDE.stable`), with the `arduino:avr` 1.8.8 core. Its bundled
`arduino-cli` works from a terminal:

```powershell
$cli = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\ArduinoSA.IDE.stable_Microsoft.Winget.Source_8wekyb3d8bbwe\resources\app\lib\backend\resources\arduino-cli.exe"
& $cli compile --fqbn arduino:avr:uno xyz_stage\firmware\xyz1
& $cli upload  --fqbn arduino:avr:uno -p COM4 xyz_stage\firmware\xyz1
```

Or open `firmware\xyz1\xyz1.ino` in the Arduino IDE and upload it to the
Uno on COM4.

### Restore the previous sketch

Before `xyz1` was loaded, the Uno ran a different sketch that printed
"Variable Speed Controller Active". It took commands such as `S 200` for
steps/s and `X 500` to move. Its source isn't in this repo, but the flash
image is:

```powershell
$avr  = "$env:LOCALAPPDATA\Arduino15\packages\arduino\tools\avrdude\8.0.0-arduino1\bin\avrdude.exe"
$conf = "$env:LOCALAPPDATA\Arduino15\packages\arduino\tools\avrdude\8.0.0-arduino1\etc\avrdude.conf"
& $avr -C $conf -p atmega328p -c arduino -P COM4 -b 115200 -U flash:w:xyz_stage/firmware/backup/uno_before_xyz1_flash.hex:i
```

The EEPROM couldn't be backed up. The Uno's bootloader returns flash when
asked for EEPROM.

## Log

### 2026-09-25 — DRV8825 squeal — idle FIXED, during moves OPEN (hardware)

**Symptom.** A high-pitched squeal from the drivers, both while moving and at
standstill.

**Cause.** The squeal is the DRV8825s' current regulation (chopping) running
at an audible frequency. It lasts as long as the drivers are powered, and the
firmware powered them from boot onwards.

**Change.** The firmware now powers the drivers only during moves; see
[Driver power](#driver-power). The UI toggle is relabelled to match.

**Result.** Tested by the user by ear:
- **Idle:** squeal gone.
- **Jogging X +10 / −10 steps** (drivers on for about 40 ms per move): a
  brief squeal per move remains. X returned to −17084.

**The squeal during moves needs hardware changes.** Firmware can't change the
chopping. In order of effort:
1. **Lower each driver's current limit.** Set Vref on the DRV8825 pot to the
   motor's rated current ÷ 2, or lower if the stage has torque to spare. The
   pots are often left near maximum.
2. **Microstepping jumpers** (M0–M2 under each driver on the shield). They
   change the current waveform and make motion smoother; the effect on the
   squeal varies.
3. **Swap to TMC2208/TMC2209 drivers** in standalone mode. They drop into the
   same sockets (STEP/DIR/EN) and their StealthChop mode is designed to be
   silent. Check the pinout and the enable polarity for the specific module.

### 2026-09-25 — set up; `xyz1` flashed

- **Previous state:** the Uno was running the "Variable Speed Controller"
  sketch, not `xyz1`. Its flash is backed up (see above), and `xyz1` was
  flashed over it.
- **Boot banner after flashing:**
  `POS -17154 6848 8895`, `LIMITS 0 0 0 0 0 0`, `UNCALIBRATED`,
  `FOCUS 260`, `READY`. The EEPROM already held `xyz1` state, so the board
  ran `xyz1` at some earlier point.
- **Stored position is stale:** the other sketch ran in between and may have
  moved the motors, so that position likely doesn't match where the stage
  really is. Zero the axes (or recalibrate the endpoints) before relying on
  absolute positions.
- **No soft limits:** no axis is calibrated, so any move is allowed. Keep
  jog sizes small until the endpoints are marked.
- **Motor currents:** the original `xyz1` powered the motors (holding
  current) from boot. That has since changed; see the squeal entry above.
