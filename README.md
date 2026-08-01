# litho-deflicker

Drop black frames from a Basler acA1920-40um (USB3, mono) viewing a flickering
light source.

## Setup

**Prerequisite:** install the Basler **pylon SDK** first (it ships the USB3
driver and runtime that `pypylon` binds to). Without it `import pypylon` fails.

Create the venv and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Optionally activate it so you can just type `python`:

```powershell
.\.venv\Scripts\Activate.ps1
# if PowerShell blocks the script:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
```

Check that the camera is visible:

```powershell
.\.venv\Scripts\python.exe -c "from pypylon import pylon; print(pylon.TlFactory.GetInstance().EnumerateDevices()[0].GetModelName())"
# -> acA1920-40um
```

This folder already has a `.venv` set up with pypylon 26.7, opencv-python 5.0.0.93
and numpy 2.5.1.

Only one process can hold a USB3 Basler camera at a time — **close pylon Viewer
before running these scripts**, or you get
`Device is exclusively opened by another client`.

## 1. Measure the flicker

```powershell
.\.venv\Scripts\python.exe capture_frames.py --num 120 --outdir frames
```

Grabs 120 frames into RAM (so PNG encoding can't cause dropped frames), then writes:

- `frames/frame_00007_black_mean001.23.png` — every frame, labelled
  `black`/`blown`/`lit` with its mean intensity in the filename
- `frames/frame_stats.csv` — per-frame `mean`, `p99`, `max`, `sat_frac`,
  `is_black`, `is_blown`, timestamp

and prints a histogram of mean intensity, the cluster centres, the **fraction of
frames that are black and blown out**, and the longest consecutive dropped run.

Useful flags:

| flag | effect |
| --- | --- |
| `--exposure 5000` | pin exposure to 5000 µs and disable auto exposure |
| `--gain 6` | pin gain (dB) |
| `--fps 30` | cap the acquisition rate |
| `--save black` | write only the black frames (also `blown`, `dropped`, `none`) |

## Black and blown-out frames

A frame is kept when its mean intensity lands inside the band
`--threshold-low .. --threshold-high`: below the low cutoff it is a black flicker
frame, above the high cutoff it is blown out (fully white). Both default to auto
and both scripts take the same flags.

| flag | effect |
| --- | --- |
| `--threshold-low 107` | black cutoff (`--threshold` is kept as an alias) |
| `--threshold-high 206` | blown-out cutoff |
| `--no-high` | never drop bright frames, whatever the auto split says |

`low` is the 2-means split of the per-frame means. `high` is found by
re-splitting only the non-black frames, and is applied **only when the upper
cluster is genuinely saturated** — most of its pixels pegged within 2% of full
scale, while the lower cluster is not. Without that guard a run containing no
blown frames would have its brightest good frames discarded; with it, a merely
bright scene keeps every frame and the script reports "no saturated cluster
found".

Measured here at `--exposure 500`, all three populations show up cleanly:

```
  dark cluster centre   3.42
  lit cluster centre    159.03
  blown cluster centre  253.53
  suggested low  cutoff 107.00
  suggested high cutoff 206.28

  BLACK FRAMES: 29 / 40 = 72.5%
  BLOWN FRAMES:  6 / 40 = 15.0%
  USABLE:        5 / 40 = 12.5%
```

## Resolution / AOI

The pylon Viewer resolution box is just the GenICam `Width`/`Height`/`OffsetX`/
`OffsetY` nodes, so both scripts expose them (same flags on each):

| flag | effect |
| --- | --- |
| `--width 640 --height 480` | area of interest, centred by default |
| `--offset-x 320 --offset-y 200` | put the AOI somewhere other than the centre |
| `--binning 2` | combine 2x2 pixels — quarter the data, better SNR |
| `--binning-mode Sum` | brighten while binning (default `Average` keeps the intensity scale, which matters because the black-frame threshold *is* an intensity) |
| `--full` | full sensor, offsets zeroed, binning reset to 1 |

Measured on this camera (`acA1920-40um`):

| AOI | max frame rate |
| --- | --- |
| 1936x1216 (full) | 41.1 fps |
| 640x480 | 99.2 fps |

Reading out fewer rows is what buys the speed, so `--height` matters far more
than `--width`. A faster frame rate also means finer time resolution on the
flicker, which makes the black/lit split cleaner.

Two things worth knowing:

- The real sensor is **1936x1216**; 1920x1200 is the nominal spec figure. `--full`
  gives you the former.
- **The camera keeps these settings between runs** — they live in the camera, not
  the script. Run `--binning 2` once and every later run stays binned until you
  pass `--full` or `--binning 1`, or power-cycle the camera. The scripts warn when
  they find leftover binning. Values are clamped to the camera's range and snapped
  to its increment (width steps by 4), and any adjustment is printed.

Auto thresholding is a 1-D 2-means split of the per-frame mean intensity. If the
distribution isn't clearly bimodal the script says so — that usually means the
exposure is long enough to average over the flicker, so shorten it.

## 2. Live deflickered view

```powershell
.\.venv\Scripts\python.exe deflicker_live.py --scale 0.5
```

Scores each frame, drops the ones below threshold, and holds the last good frame
on screen so the view is steady instead of strobing. Without `--threshold` it
calibrates on the first 60 frames (`--calib`).

| key | action |
| --- | --- |
| `q` / `Esc` | quit |
| `r` | recalibrate (only the cutoffs you did not pin on the command line) |
| `[` / `]` | lower / raise the **low** cutoff by 1 |
| `{` / `}` | lower / raise the **high** cutoff by 1 (shift + `[` / `]`) |
| `space` | toggle deflicker off/on to compare against the raw flicker |
| `s` | save a snapshot PNG |

The overlay shows the current mean against the keep band, kept/total, recent drop
percentage, separate black and blown counts, and input vs output frame rate. A
dropped frame is labelled `DROPPED: BLACK` or `DROPPED: BLOWN`, and if the auto
split found only one brightness population the overlay says so rather than
silently throwing away half the good frames.

`--scale` sets the *initial* window size only. The window is freely resizable
and the image keeps its aspect ratio, letterboxed with black bars — note that
this is done in code, not by `cv2.WINDOW_KEEPRATIO`, which is `0` (a no-op) and
leaves the Windows HighGUI backend stretching the image to fill the window.

`--record out.avi` writes the deflickered (kept-only) stream to an MJPG file.

## Notes

- Grabbing uses `GrabStrategy_OneByOne` for the capture step (every frame counts)
  and `GrabStrategy_LatestImageOnly` for the live view (stay current, never lag).
- Dropping frames makes the output rate uneven — if a downstream consumer needs a
  constant rate, hold the last good frame instead of dropping (that is what the
  live display does visually).
- If the flicker is periodic and you would rather *avoid* black frames than drop
  them, the camera's hardware trigger (`TriggerMode=On`, `TriggerSource=Line1`)
  synced to the light source is the better fix; this project is the software
  fallback.
