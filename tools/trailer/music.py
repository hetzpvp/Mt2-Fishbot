#!/usr/bin/env python3
"""Lo-fi beat for the trailer, synthesized with numpy (no samples, no copyright).

    python3 tools/trailer/music.py out.wav [--bars 51] [--sections 0:intro,2:a,13:b,25:c,40:outro]

84 BPM, swung boom-bap drums, FM electric piano chords, round bass, a soft mallet
motif in the later sections, vinyl crackle. Laid back on purpose: no risers or
drops. A section change only lets the drums breathe for half a bar. The tape wow,
dusty filtering and final level are applied by ffmpeg in render().
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
BPM = 84.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT
SWING = 0.60          # offbeat 8th at 60 % of the beat
rng = np.random.default_rng(7)


def mtof(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


# ---------------------------------------------------------------- filters (FFT, static)
def fft_filter(x, lo=None, hi=None, order=2):
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    if hi:
        g /= np.sqrt(1 + (f / hi) ** (2 * order))
    if lo:
        g /= np.sqrt(1 + (lo / np.maximum(f, 1e-3)) ** (2 * order))
    return np.fft.irfft(X * g, n)


def convolve(x, ir):
    n = len(x) + len(ir) - 1
    N = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(ir, N), N)[: len(x)]


def reverb_ir(seconds=2.2, decay=3.2, seed=1):
    r = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    ir_l = r.standard_normal(len(t)) * np.exp(-decay * t)
    ir_r = r.standard_normal(len(t)) * np.exp(-decay * t)
    ir_l, ir_r = fft_filter(ir_l, lo=200, hi=5000), fft_filter(ir_r, lo=200, hi=5000)
    pre = int(0.018 * SR)
    ir_l[:pre] = 0
    ir_r[:pre] = 0
    s = np.sqrt(np.sum(ir_l ** 2))
    return ir_l / s, ir_r / s


# ---------------------------------------------------------------- instruments
def ep_note(freq, dur, vel):
    """FM electric piano: bell-ish attack that mellows, a short tine click."""
    n = int((dur + 1.2) * SR)
    t = np.arange(n) / SR
    idx = 1.4 * np.exp(-t * 5.0) + 0.25
    y = np.sin(2 * np.pi * freq * t + idx * np.sin(2 * np.pi * freq * t))
    y += 0.10 * np.sin(2 * np.pi * freq * 7.0 * t) * np.exp(-t * 30)
    y += 0.18 * np.sin(2 * np.pi * freq * 2.0 * t) * np.exp(-t * 3)
    env = np.exp(-t * 0.9) * np.minimum(1, t / 0.004)
    rel = np.clip((dur + 0.25 - t) / 0.25, 0, 1)          # let go at the end of the chord
    return y * env * rel * vel


def bass_note(freq, dur, vel):
    n = int((dur + 0.2) * SR)
    t = np.arange(n) / SR
    y = np.sin(2 * np.pi * freq * t) + 0.22 * np.sin(4 * np.pi * freq * t) + 0.08 * np.sin(6 * np.pi * freq * t)
    env = np.minimum(1, t / 0.012) * np.exp(-t * 1.2) * np.clip((dur + 0.08 - t) / 0.08, 0, 1)
    return np.tanh(1.4 * y) * env * vel


def mallet_note(freq, vel):
    t = np.arange(int(1.6 * SR)) / SR
    y = np.sin(2 * np.pi * freq * t) * np.exp(-t * 2.4)
    y += 0.35 * np.sin(2 * np.pi * freq * 4.0 * t) * np.exp(-t * 9)
    y += 0.12 * np.sin(2 * np.pi * freq * 9.8 * t) * np.exp(-t * 22)
    return y * np.minimum(1, t / 0.002) * vel


def kick(vel):
    t = np.arange(int(0.45 * SR)) / SR
    f = 46 + 70 * np.exp(-t * 28)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7.5)
    y += 0.25 * rng.standard_normal(len(t)) * np.exp(-t * 300)
    return np.tanh(1.8 * y) * vel


_NOISE = rng.standard_normal(SR)
_SNARE_N = fft_filter(_NOISE, lo=900, hi=5200)
_HAT_N = fft_filter(_NOISE, lo=6500, hi=10500, order=3)
_RIM_N = fft_filter(_NOISE, lo=1800, hi=4000)


def snare(vel):
    t = np.arange(int(0.32 * SR)) / SR
    y = 0.9 * _SNARE_N[: len(t)] / np.std(_SNARE_N) * np.exp(-t * 16)
    y += 0.55 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 28)
    return y * vel * 0.55


def rim(vel):
    t = np.arange(int(0.08 * SR)) / SR
    y = _RIM_N[: len(t)] / np.std(_RIM_N) * np.exp(-t * 70) + 0.6 * np.sin(2 * np.pi * 820 * t) * np.exp(-t * 90)
    return y * vel * 0.35


def hat(vel, open_=False):
    d = 0.22 if open_ else 0.05
    t = np.arange(int(d * SR)) / SR
    o = rng.integers(0, SR - len(t))
    y = _HAT_N[o:o + len(t)] / np.std(_HAT_N) * np.exp(-t * (14 if open_ else 75))
    return y * vel * 0.22


# ---------------------------------------------------------------- harmony
# rootless voicings (F major / D minor), one chord per bar
PROG = {
    "a": [("Fmaj7", 41, [57, 60, 64, 65]), ("Em7", 40, [55, 59, 62, 64]),
          ("Dm9", 38, [57, 60, 64, 65]), ("Am9", 45, [55, 59, 60, 64])],
    "b": [("Bbmaj7", 46, [57, 62, 65, 69]), ("Am7", 45, [55, 60, 64, 67]),
          ("Gm9", 43, [57, 58, 62, 65]), ("C9", 48, [58, 62, 64, 67])],
    "c": [("Dm9", 38, [57, 60, 64, 65]), ("G13", 43, [53, 59, 64, 69]),
          ("Cmaj9", 48, [55, 59, 62, 64]), ("A7b13", 45, [55, 61, 65, 70])],
    "outro": [("Fmaj9", 41, [57, 60, 64, 67]), ("G6/9", 43, [57, 59, 64, 67]),
              ("Em7", 40, [55, 59, 62, 64]), ("Am9", 45, [55, 59, 60, 64])],
}
PROG["intro"] = PROG["a"]
MOTIF = {   # (beat, midi) per 2 bars; D minor pentatonic-ish, sparse
    "b": [(0.5, 77), (1.5, 76), (2.0, 72), (5.0, 74), (6.5, 72)],
    "c": [(0.0, 81), (1.0, 77), (2.5, 76), (4.5, 74), (5.5, 77), (7.0, 76)],
    "outro": [(0.5, 76), (1.0, 79), (2.0, 81), (4.5, 79), (6.0, 76)],
}


def swing_time(bar_start, step16):
    """Time of a 16th step (0..15) with 8th-note swing on the offbeats."""
    beat, sub = divmod(step16, 4)
    pos = {0: 0.0, 1: SWING * 0.5, 2: SWING, 3: SWING + (1 - SWING) * 0.5}[sub]
    return bar_start + (beat + pos) * BEAT


def parse_sections(spec, bars):
    secs = []
    for part in spec.split(","):
        b, k = part.split(":")
        secs.append((int(b), k))
    secs.sort()
    out = []
    for i, (b, k) in enumerate(secs):
        e = secs[i + 1][0] if i + 1 < len(secs) else bars
        out.append((b, e, k))
    return out


def compose(bars, sections):
    total = bars * BAR + 3.0
    n = int(total * SR)
    keys = np.zeros((2, n))
    bass = np.zeros(n)
    drums = np.zeros((2, n))
    mallet = np.zeros((2, n))

    def add(buf, y, t, pan=0.0):
        i = int(t * SR)
        if i >= n:
            return
        y = y[: n - i]
        if buf.ndim == 1:
            buf[i:i + len(y)] += y
        else:
            buf[0, i:i + len(y)] += y * np.sqrt(0.5 * (1 - pan))
            buf[1, i:i + len(y)] += y * np.sqrt(0.5 * (1 + pan))

    for (b0, b1, kind) in sections:
        prog = PROG[kind]
        for bar in range(b0, b1):
            t0 = bar * BAR
            rel = bar - b0
            name, root, voicing = prog[rel % 4]
            last_bar_of_section = bar == b1 - 1
            first = rel == 0
            # keys: chord on 1 (strummed), soft re-hit on the "and" of 3 every other bar
            hits = [(0.0, 3.4 * BEAT, 0.55)]
            if rel % 2 == 1:
                hits = [(0.0, 2.4 * BEAT, 0.5), (2.0 + SWING, 1.3 * BEAT, 0.3)]
            for (bt, dur, vel) in hits:
                for j, m in enumerate(voicing):
                    jitter = rng.uniform(-0.008, 0.012)
                    add(keys, ep_note(mtof(m), dur, vel * rng.uniform(0.85, 1.0)),
                        t0 + bt * BEAT + j * 0.014 + jitter, pan=-0.35 + 0.23 * j)
            if kind == "intro":
                continue
            # bass: root on 1, then a pickup on the and of 2 / 4
            add(bass, bass_note(mtof(root), 1.4 * BEAT, 0.8), t0 + 0.01)
            add(bass, bass_note(mtof(root + (7 if rel % 2 else 12)), 0.45 * BEAT, 0.5), swing_time(t0, 6) + 0.01)
            add(bass, bass_note(mtof(root), 0.8 * BEAT, 0.6), swing_time(t0, 10) + 0.01)
            # drums (the last half bar of a section breathes: only the kick on 1)
            breathe = last_bar_of_section and b1 != bars
            late = 0.018           # laid back snare
            for step in range(16):
                t = swing_time(t0, step) + rng.uniform(-0.004, 0.006)
                if breathe and step >= 8:
                    continue
                if step in (0, 10) or (step == 7 and rel % 2 == 1) or (step == 3 and kind == "c" and rel % 4 == 2):
                    add(drums, kick(0.95 if step == 0 else 0.75), t)
                if step in (4, 12):
                    add(drums, snare(0.9 * rng.uniform(0.92, 1.0)), t + late, pan=0.05)
                if step == 15 and rel % 4 == 3:
                    add(drums, rim(0.5), t, pan=0.2)
                if step % 2 == 0:
                    v = 0.75 if step % 4 == 0 else 0.5
                    if kind == "b" and step % 4 == 2:
                        v = 0.62
                    add(drums, hat(v * rng.uniform(0.8, 1.0), open_=(kind == "c" and step == 14 and rel % 2 == 1)), t, pan=0.3)
                elif kind in ("b", "c") and step in (7, 15) and rng.random() < 0.6:
                    add(drums, hat(0.25), t, pan=0.3)
            if first and b0 > 0:
                add(drums, hat(0.4, open_=True), t0 - 0.5 * BEAT, pan=0.3)
            # mallet motif
            motif = MOTIF.get(kind)
            if motif and rel % 2 == 0 and not (kind != "outro" and rel == 0):
                for (bt, m) in motif:
                    add(mallet, mallet_note(mtof(m), 0.35 * rng.uniform(0.85, 1.0)), t0 + bt * BEAT + 0.01,
                        pan=rng.uniform(-0.3, 0.3))

    # crackle + hiss over everything
    crackle = np.zeros(n)
    pops = rng.random(n) < (7.0 / SR)
    crackle[pops] = rng.standard_normal(pops.sum()) * rng.uniform(0.05, 0.4, pops.sum())
    crackle = fft_filter(crackle, lo=1500, hi=9000)
    hiss = fft_filter(rng.standard_normal(n), lo=2000, hi=8000) * 0.004
    return keys, bass, drums, mallet, crackle + hiss, total


def mix(bars, sections):
    keys, bass, drums, mallet, noise, total = compose(bars, sections)
    irl, irr = reverb_ir()
    keys = np.stack([fft_filter(c, lo=90, hi=3200) for c in keys])
    mallet = np.stack([fft_filter(c, lo=200, hi=5000) for c in mallet])
    drums = np.stack([fft_filter(c, lo=35, hi=7500) for c in drums])
    drums = np.tanh(drums * 1.6) / 1.6
    bass = fft_filter(bass, hi=420)
    # light duck of keys + bass on the kick would need the kick track; the drums' own level does it
    send = 0.28 * (keys + 0.8 * mallet) + 0.10 * drums
    wet = np.stack([convolve(send[0], irl), convolve(send[1], irr)])
    out = 0.55 * keys + 0.40 * mallet + 0.75 * drums + 0.55 * bass[None, :] + 0.5 * wet + noise[None, :] * 1.0
    # gentle fade in over the first bar, fade out over the last two bars
    n = out.shape[1]
    t = np.arange(n) / SR
    fade = np.clip(t / (0.6 * BAR), 0, 1) * np.clip((bars * BAR + 1.5 - t) / (2 * BAR), 0, 1)
    out *= fade
    out = np.tanh(out * 1.2)
    out /= np.max(np.abs(out)) + 1e-9
    return out * 0.89, total


def write_wav(path, stereo):
    data = (np.clip(stereo.T, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def render(out, bars, sections, raw=None):
    out = Path(out)
    raw = Path(raw or out.with_suffix(".dry.wav"))
    stereo, total = mix(bars, sections)
    write_wav(raw, stereo)
    # tape: slow wow + faster flutter, a little dusty top, level for the mix
    af = "vibrato=f=0.55:d=0.035,vibrato=f=6.5:d=0.006,lowpass=f=11000,highpass=f=30,loudnorm=I=-15:TP=-1.5:LRA=9"
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw), "-af", af,
                        "-ar", str(SR), str(out)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr)
    return out, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--bars", type=int, default=51)
    ap.add_argument("--sections", default="0:intro,2:a,13:b,25:c,40:outro")
    a = ap.parse_args()
    out, total = render(a.out, a.bars, parse_sections(a.sections, a.bars))
    print(f"{out}  {a.bars} bars  {a.bars * BAR:.2f} s (bar {BAR:.4f} s)")


if __name__ == "__main__":
    main()
