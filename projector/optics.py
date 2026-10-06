"""Nominal numbers for the projector and the exposure optics.

DMD (TI DLP471TP datasheet, July 2023, section 6.10): 1920 x 1080
micromirrors at 5.4 um pitch, active area 10.368 x 5.832 mm. The 3840 x 2160
input is not a native grid: a 4-position actuator (XPR) moves the image so
that each mirror shows four pixels per frame, i.e. 4K input pixels sit half a
mirror apart but each is drawn by a whole mirror.

Exposure field: the image goes through a 10x objective; from experience the
field on the wafer is about 1070 um wide (nominal, not measured here).
"""

DMD_MIRRORS = (1920, 1080)
MIRROR_PITCH_UM = 5.4                       # at the DMD
INPUT_PX = (3840, 2160)                     # what Windows / HDMI sends

FIELD_WIDTH_UM = 1070.0                     # on the wafer, nominal
FIELD_HEIGHT_UM = FIELD_WIDTH_UM * INPUT_PX[1] / INPUT_PX[0]       # 601.875
UM_PER_INPUT_PX = FIELD_WIDTH_UM / INPUT_PX[0]                     # 0.2786
UM_PER_MIRROR = FIELD_WIDTH_UM / DMD_MIRRORS[0]                    # 0.5573
DEMAGNIFICATION = DMD_MIRRORS[0] * MIRROR_PITCH_UM / FIELD_WIDTH_UM  # 9.69
