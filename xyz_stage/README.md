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
| limit switches | one per axis, normally open, to GND, with pull-ups (1 = open, 0 = pressed), on D9 / D10 / D11 (D12 is read too, unused). Which pin belongs to which axis is found by `homing.py`, not assumed: X → D9, on its − side. The far end of each axis is a fixed 12 mm from its switch |
| steps per mm | 400: 200-step motors, full step (no microstep jumpers), 0.5 mm lead screw. Measured on Y with a 400-step move, which turned the shaft exactly 2 revolutions |
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

The server finds the Uno and connects to it by itself; there's no port to
pick. Only one program can hold COM4 at a time. Before running
`find_arduino.py --probe` or flashing, click **Disconnect** in the UI, or
`POST /stage/disconnect`. **Connect** takes it back.

- **Projector and camera panes:** see [ui/README.md](../ui/README.md).
- **Keys:** arrow keys jog X/Y, PgUp/PgDn jog Z. See `litho-ui_README.md`
  for the rest.
- **Motor toggle:** "Motors released when idle (quiet)" by default; see
  [Driver power](#driver-power).

## Homing

```powershell
.\.venv\Scripts\python.exe xyz_stage\homing.py              # home all axes (asks about any axis not yet known)
.\.venv\Scripts\python.exe xyz_stage\homing.py --discover   # redo the questions, e.g. after rewiring
.\.venv\Scripts\python.exe xyz_stage\homing.py --axes Y Z   # only some axes
```

Run it in a terminal you can type into: discovery asks questions.

**Discovery**, the first time for an axis:
1. It asks for steps per mm (400 here).
2. It moves the axis +100 steps. If a switch closes, that switch belongs to
   this axis and + is towards it.
3. Otherwise you say whether it moved **t**owards or **a**way from its switch,
   or **r**epeat with a 400-step move if you couldn't tell.
4. It then seeks towards the switch; whichever pin closes is this axis's. If
   a switch sat pressed at the start and opens during the first move, that
   also identifies it.

The result is saved to `homing.json` before homing starts: pin, direction
towards the switch, steps/mm, travel. Once saved, homing runs without
questions, and stops if a different switch closes than the saved one (a
sign that the wiring or mechanics changed; rerun with `--discover`).

**Homing an axis:**
1. Fast seek to the switch (0.25 mm/s at 1.6 ms/step, 400 steps/mm).
2. Back off until the switch opens, then 100 steps more.
3. Slow approach at 6 ms/step.
4. That trigger point becomes **0**, with soft limits 0..12 mm on the side
   away from the switch (`HOMED` command, saved in EEPROM).
5. Back off until the switch opens, then park 50 steps further.

After homing, jogs can't leave the 12 mm range.

**Switch hysteresis:** the switches open noticeably behind where they close.
A fixed 100-step back-off wasn't always enough on X, so the script now backs
off in 50-step chunks until the switch opens, up to 2 mm, and records how far
that took (`hysteresis_steps`).

**Firmware commands used:**
- `SEEK X -500 [delay_us]`: move up to 500 steps, ignoring soft limits,
  stopping when a switch that was open at the start closes. The reply is
  `SEEK X <moved> HIT D9` / `NOHIT` / `ABORT`. **Any byte arriving mid-move
  aborts it**, so don't use the UI while homing runs.
- `HOMED X min max`: the current position becomes 0, with limits
  [min, max].

The script checks every reply strictly. If a `SEEK` reply arrives damaged,
it works the result out from `P` (position change) and `LS` (which switch is
now closed).

## Limit switches

The firmware **reports** the switches. Only `SEEK` (homing) stops at them; a
normal jog doesn't.

- **When it reports:** it sends `LS D9=1 D10=1 D11=1 D12=1` at boot, in reply
  to the `LS` command, and whenever a pin changes and stays changed for
  10 ms. Values are raw levels: 1 = open, 0 = pressed.
- **Between moves only:** moves block the firmware's main loop, so changes
  are only reported between moves.
- **Both ends share a pin:** the shield wires each axis's `-` and `+` headers
  in parallel, so one pin can't tell which end was hit. The direction of the
  move that triggered it can.

To watch them live while toggling by hand:

```powershell
.\.venv\Scripts\python.exe xyz_stage\limit_switches.py      # Ctrl+C to stop
```

It goes through `ui/server.py` if that's running, which owns COM4, and
opens the port itself otherwise.

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
so the position readout would go wrong. For that case use `E`: the **Hold**
button under "Motors between moves" in the UI's left column. **Release** is
`D`.

## Flash the firmware

If `ui/server.py` is running, release the port first: click **Disconnect**
in the UI, or run `curl -X POST localhost:8765/stage/disconnect`. Afterwards,
reconnect.

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

### 2026-09-26 — homing: X done, Y switch detached, Z not started — OPEN

**Steps/mm.** Microstepping isn't in the firmware; it's set by jumpers. A
400-step move on Y turned the shaft 2 full revolutions, so it's full step.
With the 0.5 mm lead that's 400 steps/mm.

**Garbled replies (fixed).** At first, about half of all `SEEK` replies
never reached the script.
- **Cause:** logging every line showed they were arriving with characters
  missing (`SEK Y 0 NOHIT`, `SEEKY 0 NOHIT`, `SEEK  0 NHIT`), always on the
  line printed right after the drivers were switched off. `LS` and `P`
  (no driver switching) were never damaged.
- **Board, not server:** it happened on a direct connection too (2 of 10), so
  the damage happens on the board, most likely the transient from disabling
  the DRV8825s.
- **Firmware fix:** `Serial.flush()` before switching the drivers, and 50 ms of
  silence after switching them off. After the fix, 40 of 40 direct and 15 of
  15 through the server came back clean.
- **Script safeguard:** it also rebuilds a damaged reply from `P` and `LS`. That
  fallback fired once since, during the long Y seek below.

**X: homed.**
- **First run:** discovery found D9 after seeking −2667 steps (you answered
  "away" for +). That run then stopped: D9 was still closed after a fixed
  100-step back-off.
- **Second run:** with back-off-until-open, X homed from the saved entry:
  zero at the D9 trigger, limits 0..4800, hysteresis 50 steps (0.12 mm),
  parked at 200.

**Y: its switch was detached.** You answered "towards" for +, so the seek ran
its full 6360-step limit (15.9 mm) with nothing to stop it, and stalled the
motor against the end for a few seconds. The position counter (13708) no
longer matches where Y really is. After you reattached the switch, Y was
moved 400 steps (1 mm) back: no switch was closed before or after, even
though Y had been at the end stop. So the reattached switch isn't reached
before the hard stop.

**Next:**
1. Mount Y's switch where the carriage presses it before the hard stop, and
   check it with `limit_switches.py` by hand.
2. Run `homing.py --axes Y Z`.

**Lesson:** a seek towards a switch has no protection if the switch is
missing; it only stops at its step limit (1.2 × travel + slack). A later
improvement could stop on a stall (e.g. with the camera).

### 2026-09-26 — limit switches checked by hand — WORKING

**Setup.** Added limit switch reporting to `xyz1` (see
[Limit switches](#limit-switches)) and flashed it, releasing COM4 through
`/stage/disconnect` for the upload. Then ran `limit_switches.py` while the
user pressed each switch.

**Result.** All three switches work:
- each changed only its own pin, 1 → 0 on press and back on release;
- X on D9 (3 presses), Y on D10 (6), Z on D11 (4);
- D12 never changed, so Z is wired to D11, the CNC Shield V3 default;
- no chatter within the 10 ms debounce.

Twice, a press and its release reached the script together (same
timestamp), meaning a tap of only a few tens of ms; both edges were still
caught.

**Not tested:** whether each axis has one switch or two (both ends share a
pin). Nothing acts on the switches yet; homing or stopping at them is still
to do.

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
