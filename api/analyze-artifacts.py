"""
MNE artifact audit for a single-channel NeuroSky raw EEG capture.

This is the offline, research-grade cross-check for the six lightweight
heuristic filters that run live in the browser (see the Models tab / the
`Filters` object in index.html). It does NOT compute the theta/gamma ratio
or SEF95 phenotyping numbers — those stay in the client, on the same
pipeline that already scores every session. This endpoint's only job is
artifact filtering: telling you which windows of a recording MNE would
throw out, so you can compare that against what the live filters actually
threw out.

Why not ICA? ICA needs multiple channels to separate an eye-blink or
muscle component from the EEG signal. A NeuroSky headset gives you exactly
one forehead-referenced channel, so there's nothing for ICA to unmix. What
MNE *can* still do on a single channel, using the same published methods
it uses for clinical/research EEG:
  - `annotate_muscle_zscore`: z-scores a high-frequency-band envelope and
    flags segments where it's high — MNE's own version of the EMG filter
    the browser already runs, just off a real, published implementation.
  - `annotate_amplitude`: flags flat (disconnected) and clipped/very high
    amplitude segments — MNE's version of the browser's blink/contact
    filters.

Deployment (Vercel):
  Vercel's Python runtime expects a class named `handler` that extends
  `BaseHTTPRequestHandler` in this file. Vercel installs `requirements.txt`
  (at the project root) into this function automatically. If Vercel's
  Python function conventions have changed since this was written, check
  https://vercel.com/docs/functions/runtimes/python before deploying —
  this file's `analyze()` function is the part that matters and is
  runtime-agnostic.

Standalone use (no deploy needed):
  python api/analyze-artifacts.py neurodex-session-raw.json
"""
import json
import sys

import numpy as np
import mne

mne.set_log_level("ERROR")


def _merge_intervals(intervals):
    """Collapse overlapping/adjacent [start, end] pairs into one list."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda p: p[0])
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def analyze(fs, samples):
    """
    fs: sampling rate in Hz (256 for practice mode / the ASIC fallback,
        512 for NeuroSky raw wave).
    samples: flat list of raw single-channel samples, arbitrary units
             (NeuroSky ThinkGear raw wave units), most recent last.

    Returns a JSON-serializable dict describing what MNE flagged.
    """
    fs = float(fs)
    arr = np.asarray(samples, dtype=float)
    if arr.size < fs * 2:
        raise ValueError("Need at least ~2 seconds of samples to analyze")

    # NeuroSky raw units aren't calibrated volts. Scale into a plausible
    # EEG microvolt range so MNE's default amplitude thresholds (written
    # for volts) are in the right ballpark; this is a rough normalization,
    # not a calibration, and is noted in the response.
    scale = 1e-6
    data = (arr * scale).reshape(1, -1)

    info = mne.create_info(ch_names=["Fp1"], sfreq=fs, ch_types=["eeg"])
    raw = mne.io.RawArray(data, info, verbose=False)

    nyquist = fs / 2.0
    high = min(45.0, nyquist - 1.0)
    if high > 2.0:
        raw.filter(l_freq=1.0, h_freq=high, verbose=False, fir_design="firwin")
    line_freqs = [f for f in (50.0, 60.0) if f < nyquist - 1.0]
    if line_freqs:
        raw.notch_filter(freqs=line_freqs, verbose=False)

    total_dur = float(raw.times[-1])
    muscle_segs, flat_segs, blink_segs = [], [], []
    notes = []

    # --- muscle / EMG-band artifact ---------------------------------
    try:
        from mne.preprocessing import annotate_muscle_zscore

        band_hi = min(120.0, nyquist - 2.0)
        band_lo = min(95.0, band_hi - 10.0)
        if band_lo > 5.0:
            muscle_annot, _scores = annotate_muscle_zscore(
                raw,
                ch_type="eeg",
                threshold=4.0,
                min_length_good=0.2,
                filter_freq=(band_lo, band_hi),
            )
            muscle_segs = [
                [float(o), float(o + d)]
                for o, d in zip(muscle_annot.onset, muscle_annot.duration)
            ]
        else:
            notes.append(
                "Sampling rate too low for the muscle-band detector "
                "(needs headroom above ~105 Hz); skipped."
            )
    except Exception as e:  # pragma: no cover - defensive, keeps endpoint alive
        notes.append("Muscle-artifact detector failed: %s" % e)

    # --- flat / disconnected channel -----------------------------
    # `annotate_amplitude`'s `peak` threshold fires on the *sample-to-sample*
    # jump, not on peak-to-peak within a window, so it's a good match for a
    # hard-clipped or disconnected line but not for a normal blink, which
    # rises and falls over ~100-300ms rather than between two adjacent
    # samples. `flat` uses the same consecutive-sample logic and genuinely
    # does catch a disconnected/dead channel, so it's kept; blinks are
    # handled separately below.
    try:
        from mne.preprocessing import annotate_amplitude

        flat_annot, _bad_chs = annotate_amplitude(
            raw, peak=None, flat=1e-8, bad_percent=5, min_duration=0.05
        )
        flat_segs = [
            [float(o), float(o + d)]
            for o, d in zip(flat_annot.onset, flat_annot.duration)
        ]
    except Exception as e:  # pragma: no cover
        notes.append("Flat-channel detector failed: %s" % e)

    # --- blink / motion (windowed peak-to-peak) ----------------------
    # This is the MNE-adjacent, published approach for the blink/motion
    # case `annotate_amplitude` doesn't cover: peak-to-peak amplitude
    # inside a sliding window, the same statistic conventional epoch-based
    # EEG artifact rejection (e.g. autoreject, or a manual `reject` dict in
    # `mne.Epochs`) uses. Window and threshold are tuned for a single
    # forehead channel, not multi-channel clinical EEG.
    try:
        win_s = 0.25
        win = max(4, int(round(win_s * fs)))
        step = max(1, win // 4)
        sig = raw.get_data()[0]
        ptp_thresh = 150e-6
        raw_hits = []
        i = 0
        while i + win <= sig.size:
            seg = sig[i : i + win]
            if (seg.max() - seg.min()) > ptp_thresh:
                raw_hits.append([i / fs, i / fs + win_s])
            i += step
        blink_segs = _merge_intervals(raw_hits)  # dedupe overlapping windows first
    except Exception as e:  # pragma: no cover
        notes.append("Blink/motion PTP detector failed: %s" % e)

    muscle_seconds = sum(b - a for a, b in _merge_intervals(muscle_segs))
    amplitude_seconds = sum(
        b - a for a, b in _merge_intervals(flat_segs + blink_segs)
    )
    merged = _merge_intervals(muscle_segs + flat_segs + blink_segs)
    bad_time = sum(b - a for a, b in merged)

    return {
        "duration_seconds": total_dur,
        "reject_fraction": (bad_time / total_dur) if total_dur > 0 else 0.0,
        "bad_segments": merged,
        "muscle_seconds": muscle_seconds,
        "amplitude_seconds": amplitude_seconds,
        "notes": notes
        + [
            "Amplitude thresholds assume raw samples are roughly EEG-scale "
            "after a 1e-6 scaling factor, not a true device calibration."
        ],
    }


# ---------------------------------------------------------------------
# Vercel entry point
# ---------------------------------------------------------------------
try:
    from http.server import BaseHTTPRequestHandler

    class handler(BaseHTTPRequestHandler):  # noqa: N801 (Vercel's required name)
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                payload = json.loads(body or b"{}")
                result = analyze(payload.get("fs", 256), payload.get("samples", []))
                out = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
            except Exception as e:
                out = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(400)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

except ImportError:  # pragma: no cover
    pass


# ---------------------------------------------------------------------
# CLI entry point: python api/analyze-artifacts.py session-raw.json
# ---------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python analyze-artifacts.py <session-raw.json>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        payload = json.load(f)
    result = analyze(payload["fs"], payload["samples"])
    print(json.dumps(result, indent=2))
