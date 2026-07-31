# litho-deflicker

Drop black frames from a Basler acA1920-40um (USB3, mono) viewing a flickering
light source.

## Setup

Already done in this folder — a venv with `pypylon`, `opencv-python`, `numpy`:

```powershell
.\.venv\Scripts\python.exe -c "from pypylon import pylon; print(pylon.TlFactory.GetInstance().EnumerateDevices()[0].GetModelName())"
```

Only one process can hold a USB3 Basler camera at a time — **close pylon Viewer
before running these scripts**, or you get
`Device is exclusively opened by another client`.

## 1. Measure the flicker

```powershell
.\.venv\Scripts\python.exe capture_frames.py --num 120 --outdir frames
```

Grabs 120 frames into RAM (so PNG encoding can't cause dropped frames), then writes:

- `frames/frame_00007_black_mean001.23.png` — every frame, labelled `black`/`lit`
  with its mean intensity in the filename
- `frames/frame_stats.csv` — per-frame `mean`, `p99`, `max`, `is_black`, timestamp

and prints a histogram of mean intensity, the two cluster centres, the
**fraction of frames that are black**, and the longest consecutive black run.

Useful flags:

| flag | effect |
| --- | --- |
| `--exposure 5000` | pin exposure to 5000 µs and disable auto exposure |
| `--gain 6` | pin gain (dB) |
| `--fps 30` | cap the acquisition rate |
| `--save black` | only write the black frames (or `none` for stats only) |
| `--threshold 12.5` | override the auto black/lit cutoff |

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
| `r` | recalibrate the threshold |
| `[` / `]` | lower / raise threshold by 1 |
| `space` | toggle deflicker off/on to compare against the raw flicker |
| `s` | save a snapshot PNG |

The overlay shows current mean vs threshold, kept/total, recent drop
percentage, and input vs output frame rate.

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
