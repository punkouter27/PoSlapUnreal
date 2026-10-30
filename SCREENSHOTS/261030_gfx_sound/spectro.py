"""Spectrograms of the broadcast sounds, before (the earlier WAVs, a folder given on the command line) and after
(Content/Art/Exports/Audio). training/.venv/Scripts/python.exe SCREENSHOTS/261030_gfx_sound/spectro.py <old wav dir>"""
import sys, wave
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
NEW = HERE.parents[1] / "Content" / "Art" / "Exports" / "Audio"
OLD = Path(sys.argv[1])
NAMES = ["Slap", "Thud", "Squeak0", "Breath", "CrowdReact", "Bell"]


def load(p):
    with wave.open(str(p)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767, w.getframerate()


fig, ax = plt.subplots(len(NAMES), 2, figsize=(10, 2.1 * len(NAMES)), constrained_layout=True)
for r, n in enumerate(NAMES):
    for c, (tag, d) in enumerate((("before", OLD), ("after", NEW))):
        x, sr = load(d / f"{n}.wav")
        a = ax[r, c]
        a.specgram(x, NFFT=1024, Fs=sr, noverlap=896, cmap="magma", vmin=-120)
        a.set_ylim(0, 12000)
        a.set_title(f"{n} - {tag}", fontsize=10)
        a.set_ylabel("Hz", fontsize=8)
        a.tick_params(labelsize=7)
fig.savefig(HERE / "spectrograms.png", dpi=90)
print("spectrograms.png")
