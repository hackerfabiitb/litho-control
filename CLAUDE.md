# Instructions for Claude

## Workflow
- **Commit after every prompt.** When a request is done, commit all of the
  work on it with a descriptive message. Don't push unless asked.
- **Keep the READMEs current.** Whenever a change affects how something is
  used, set up or measured, update the matching README in the same commit:
  - the top-level `README.md`
  - `camera/README.md`
  - `projector/README.md`
  - `xyz_stage/README.md`
  - `ui/README.md`
- **Record debugging results.** Add each hardware investigation to the
  "Debugging log" of the relevant directory's README. Include the date,
  symptom, method, measured results, interpretation, next steps and status.
  Add a one-line row to the table in the top-level README. When a later
  session changes the status, update both. Save small evidence images under
  `<dir>/docs/`.

## Layout
- `camera/`: Basler camera, flicker removal, live view (`camera\run.ps1`).
- `projector/`: DLP471TPEVM: test patterns, calibration, DMD diagnostics.
  Reuse `display.py` and `capture.py` rather than writing new pattern or
  capture code.
- `xyz_stage/`: Arduino Uno stepper stage. Firmware in `firmware/xyz1/`. Use
  `find_arduino.find_arduino()` to get the port rather than hard-coding it.
- `ui/`: the control page (`index.html`, from litho-ui) and `server.py`. The
  server serves the page, projects images through `projector/display.py`,
  mirrors the projector's screen, and streams the camera through the keep
  rule in `camera/deflicker.py`. Start it with `ui\start.ps1`. Put new
  camera, projector or stage features in the UI through the server rather
  than as separate scripts.
- One venv at the repo root: `.venv\Scripts\python.exe`. Run scripts from the
  repo root.

## Hardware gotchas
- The Basler camera allows **one client at a time**. The live viewer
  (`deflicker_live.py`), `ui/server.py` or pylon Viewer must be closed before
  any other script opens the camera. If you have to stop one of them, tell
  the user.
- The projector is `\\.\DISPLAY5`, 3840×2160 at 150 % scaling. Patterns need
  a per-monitor-DPI-aware process, which `projector/display.py` sets up.
- The projector must be powered on to appear on USB (`VID_0451&PID_7540`).
- Camera settings (binning, AOI, frame-rate cap) persist in the camera between
  runs. `open_camera` clears the frame-rate cap when no fps is given; any
  script that sets a cap should clear it on exit, as `flicker.py sweep` does.
- The projector's light repeats at 240 Hz (4 × 60 Hz) with a 1.25 ms dark gap
  per 4.167 ms. Camera frame rates of 240/n phase-lock to it, and short
  exposures sample it. See camera/README "Why the camera sees flicker" before
  changing exposure or fps. With the current LED level, anything over about
  1 ms exposure saturates.
- The stage Arduino is a genuine Uno on COM4. COM3 is an unrelated CH343
  adapter. Opening the port resets the Uno. Only one program can hold COM4 at
  a time, and `ui/server.py` holds it while running. To use the stage from a
  script, go through the server (`POST /stage/send`) instead of opening COM4.
  To flash, `POST /stage/disconnect` first and `/stage/connect` after. Never
  send the Uno anything in the ~1.5 s after opening the port: the bootloader
  reads it as commands. Wait for `READY`.
- Don't send motion commands to the stage without the user's go-ahead: no
  axis has soft limits yet, and the stored position is not trustworthy.
- The stage's DRV8825 drivers are powered only during moves (the default, `D`);
  `E` holds them powered at idle, which squeals. Keep that behaviour in any
  firmware change. When the user allows a test move, keep it tiny (they chose
  ±10 steps) and return to the start position.
- Limit switches: one per axis on D9/D10/D11 (normally open to GND, pull-ups;
  1 = open, 0 = pressed). Don't assume which pin is which axis: that's in
  `xyz_stage/homing.json`, found by `homing.py`. The far end of each axis is
  a fixed 12 mm away; 400 steps/mm (full step, 0.5 mm lead).
- Only `SEEK` (homing) stops at switches. It aborts on any byte received, so
  send nothing to the stage while homing runs. A seek towards a missing or
  detached switch runs until its step limit and stalls at the end stop.
- The line printed right after the drivers switch off used to lose
  characters. The firmware now flushes and pauses 50 ms around driver
  switching; keep that. Parse replies strictly, never loosely.
- Flash with the IDE's bundled arduino-cli (path in `xyz_stage/README.md`).
  Back up the existing flash with avrdude before overwriting a sketch.
