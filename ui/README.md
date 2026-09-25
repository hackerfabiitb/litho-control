# ui — control UI and server

One page runs the whole setup: stage control, projector display and a
deflickered camera feed. The page and the live feeds come from one Python
process, `server.py`.

```powershell
.\ui\start.ps1
```

That starts the server in its own window and opens `http://localhost:8765` in
Chrome, or Edge if Chrome is missing. Close the server window to stop it and
release the camera.

Then:
- **Stage:** click **Connect** at the top right and choose **COM4**.
- **Projector:** click **Projector** to load an image and project it.

## Layout

| area | shows |
| --- | --- |
| **PROJECTOR** pane | a screen capture of the projector's display, 1280 px wide, about 5 fps. It shows what the projector is really showing, including the desktop or anything that pops up over the pattern |
| **CAMERA** pane | the deflickered camera feed at full resolution, 1936×1216. The header shows fps in → shown, the frame mean against the keep cutoff, and how many frames were forced |
| controls under the panes | exposure (µs), keep ≥ (fraction of the recent top), max hold (s); **Apply** changes them live |
| rest of the page | stage jog, calibration and focus, from litho-ui (see [xyz_stage](../xyz_stage/README.md)) |

## Why a Python server, not a JavaScript one

- **The camera:** it's only reachable through pypylon, so Python is needed
  either way. A Node server would just add a second process.
- **The stage:** the page is served from `http://localhost`, which the browser
  treats as a secure context, so Web Serial still works.
- **The projector:** the server draws images on the projector itself, in a
  full-screen OpenCV window that is DPI-aware and runs at the native
  3840×2160 (`projector/display.py`). The litho-ui page tried a browser popup
  (`projector.html`, which never existed). A browser on the 150 %-scaled
  projector display would resample the image, which is not acceptable for
  masks.

## Projecting

In the **Projector** dialog:
- **Load an image** (PNG, JPG, BMP or TIFF), then click **Project Image**.
- **Fit modes:**
  - **1:1 pixels** (default): one image pixel per projector input pixel,
    centred and cropped if the image is too large. This is the mode for masks.
  - **Contain / Cover / Stretch:** scale the image. Enlarging uses
    nearest-neighbour, so binary masks stay binary.
- **Blank:** black, window kept.
- **Close:** removes the window; the projector shows the desktop again, and
  desktop light would expose resist.

The window only opens with the first Project or Blank. Starting the server
doesn't change what the projector shows.

## HTTP API

| endpoint | |
| --- | --- |
| `GET /` | the page (`index.html`) |
| `GET /stream` | deflickered camera, MJPEG |
| `GET /projector/stream` | projector screen mirror, MJPEG |
| `GET /config`, `POST /config` | camera settings and stats; POST any of `exposure_us`, `keep_frac`, `max_hold` |
| `GET /focus_score` | Laplacian variance of the latest shown camera frame |
| `GET /projector/status` | `state` (`closed` / `blank` / `image`), image name and size, fit |
| `POST /projector/image?fit=native&name=x.png` | body = image file |
| `POST /projector/fit` | `{"fit": "native" / "contain" / "cover" / "stretch"}` |
| `POST /projector/blank`, `POST /projector/close` | |

Projector calls return once the change is on screen.

## Things to know

- **The server holds the camera** while it runs. Stop it before running
  `camera\run.ps1`, `capture_frames.py` or the `projector\` scripts, and the
  other way round.
- **Camera defaults** match `camera\run.ps1`: full sensor, 400 µs,
  keep ≥ 0.8 × recent top, 0.5 s max hold. The same rule is used
  (`camera/deflicker.py`). Override them with
  `server.py --exposure --keep-frac --max-hold`.
- **Only one server at a time**, because it uses port 8765.

## Log

### 2026-09-25 — built

- **Replaces** litho-ui's file-based page, `camera_server.py` and `start.bat`.
  The page moved here from `xyz_stage/stage_controller.html`.
- **Tested with the server running:**
  - the camera ran at 41 fps in, 21–24 shown, with a longest hold of 98 ms;
  - `grid` projected at 1:1 appeared correctly in both panes;
  - fit changes, blank and close all worked;
  - a bad upload was rejected with 400;
  - a live exposure change worked;
  - a headless Edge screenshot of the page showed both panes live.
- **Not yet tested by hand:** stage control from the new URL.
