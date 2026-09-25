# Instructions for Claude

## Workflow
- **Commit after every prompt.** When a request is done, commit all of the
  work on it with a descriptive message. Don't push unless asked.
- **Keep the READMEs current.** Whenever a change affects how something is
  used, set up or measured, update the matching README in the same commit:
  - the top-level `README.md`
  - `camera/README.md`
  - `projector/README.md`
  - `stage/README.md`, once it exists
- **Record debugging results.** Add each hardware investigation to the
  "Debugging log" of the relevant directory's README. Include the date,
  symptom, method, measured results, interpretation, next steps and status.
  Add a one-line row to the table in the top-level README. When a later
  session changes the status, update both. Save small evidence images under
  `<dir>/docs/`.

## Layout
- `camera/`: Basler camera, flicker removal, live view (`camera\run.ps1`).
- `projector/`: DLP471TEEVM: test patterns, calibration, DMD diagnostics.
  Reuse `display.py` and `capture.py` rather than writing new pattern or
  capture code.
- `stage/`: XYZ stage (not started).
- One venv at the repo root: `.venv\Scripts\python.exe`. Run scripts from the
  repo root.

## Hardware gotchas
- The Basler camera allows **one client at a time**. The live viewer
  (`deflicker_live.py`) or pylon Viewer must be closed before any other script
  opens the camera. If you have to stop the viewer, tell the user.
- The projector is `\\.\DISPLAY5`, 3840×2160 at 150 % scaling. Patterns need
  a per-monitor-DPI-aware process, which `projector/display.py` sets up.
- The projector must be powered on to appear on USB (`VID_0451&PID_7540`).
- Camera settings (binning, AOI) persist in the camera between runs.
