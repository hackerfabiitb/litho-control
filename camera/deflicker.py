"""The keep rule for the projector's flicker, shared by deflicker_live.py and
the UI server (ui/server.py).

Under the DLP projector, frame brightness is a continuous spread from black
up to "saw the full light" (see camera/README.md, "Why the camera sees
flicker"). A frame is kept when it is close to the top of the recent spread,
and no frame is held on screen longer than max_hold, whatever the rule says.
"""

import time
from collections import deque

import numpy as np

RECENT_FRAMES = 80        # ~2 s at the full-frame 41 fps
WARMUP_FRAMES = 10


def relative_cutoff(recent_means, keep_frac):
    """Mean-intensity cutoff: keep_frac x the 95th percentile of the recent
    frame means (0 until there are enough of them to judge)."""
    if len(recent_means) < WARMUP_FRAMES:
        return 0.0
    return keep_frac * float(np.percentile(recent_means, 95))


class KeepFilter:
    """Decides, frame by frame, whether to show a frame.

    decide(mean) -> (show, forced): `show` is True for a kept frame and for a
    forced one; `forced` marks a frame shown only because the previous one
    had been on screen for max_hold seconds.
    """

    def __init__(self, keep_frac=0.8, max_hold=0.5):
        self.keep_frac = keep_frac
        self.max_hold = max_hold
        self.recent = deque(maxlen=RECENT_FRAMES)
        self.last_shown = None
        self.cutoff = 0.0
        self.total = self.kept = self.forced = 0
        self.longest_hold = 0.0

    def decide(self, mean, now=None):
        now = time.perf_counter() if now is None else now
        self.recent.append(mean)
        self.cutoff = relative_cutoff(self.recent, self.keep_frac)
        self.total += 1
        keep = mean >= self.cutoff
        forced = (not keep and self.max_hold > 0 and self.last_shown is not None
                  and now - self.last_shown > self.max_hold)
        if keep or forced or self.last_shown is None:   # always show a first frame
            if self.last_shown is not None:
                self.longest_hold = max(self.longest_hold, now - self.last_shown)
            self.last_shown = now
            self.kept += keep
            self.forced += forced
            return True, forced
        return False, False

    def reset(self):
        """Relearn the recent brightness, e.g. after an exposure change."""
        self.recent.clear()
