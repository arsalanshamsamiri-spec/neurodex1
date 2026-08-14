# NeuroDex

A thalamocortical dysrhythmia (TCD) phenotyping pilot, run as NeuroSky
neurofeedback training with a collection-game layer on top (Ions, chests,
a 51-specimen Dex).

The core idea: certain thalamic circuits may get stuck bursting in slow
theta instead of their normal fast rhythm, and that slow rhythm leaks into
whichever cortical area the circuit feeds — auditory cortex for tinnitus,
somatosensory cortex for pain, prefrontal/limbic circuits for depression.
The app reads that signature (a theta-vs-beta+gamma ratio, and separately
SEF95) off a NeuroSky headset, scores it against your own resting
baseline, and trains against it. See the in-app Research tab for the full
pilot design.

## What's in this repo

```
index.html                  the whole frontend — single file, hash-routed, no build step
api/contact.js               Vercel Node function backing the contact form
api/analyze-artifacts.py     Vercel Python function running an MNE artifact audit
api/requirements.txt         Python deps for the MNE function (kept in api/, not the project root — see note below)
vercel.json                  disables framework auto-detection + function memory/timeout config
package.json                 lets Vercel identify api/contact.js as a Node function
```

> **Why `requirements.txt` lives in `api/`, not the project root:** if Vercel finds a
> `requirements.txt` at the project root, it assumes the *entire project* is a Python
> app and goes looking for a Python entrypoint (`app.py`, `index.py`, `main.py`,
> `wsgi.py`, `asgi.py`). This project doesn't have one — `index.html` isn't it — so
> that detection just breaks the deploy instead. Keeping `requirements.txt` next to
> `analyze-artifacts.py` in `api/` still gets it installed for that one function,
> without tripping root-level framework detection. `vercel.json` also sets
> `"framework": null` as a second guard against the same misdetection.

## Deploying to Vercel

1. Push this folder to a GitHub/GitLab/Bitbucket repo (or run `vercel` from
   inside it directly with the Vercel CLI — no build step is needed).
2. Import the repo in the Vercel dashboard, or run `vercel deploy`.
   Framework preset: **Other**. No build command, no output directory
   override needed — `index.html` is served as-is from the root.
3. Vercel will pick up `api/requirements.txt` automatically and install it
   for `api/analyze-artifacts.py`. First deploy may take a bit longer than
   usual because of MNE's dependency size; that's expected.
4. Wire up `api/contact.js` to an actual email/notification service (see
   the TODO comment in that file) before relying on the contact form.

Locally, `vercel dev` will serve both functions on `localhost` alongside
the static file, so the "Analyze with MNE" button and the contact form
work the same as in production.

## The MNE artifact audit

The six artifact filters on the **Models** tab run live, in the browser,
in milliseconds — they're fast heuristics, not a diagnostic pipeline.
`api/analyze-artifacts.py` is a slower, independent second opinion: it
runs an exported session back through
[MNE-Python's](https://mne.tools) published artifact-detection routines
so you can see how well the live filters agree with a research-grade
pipeline. It does **not** compute the theta/gamma ratio or SEF95 — those
stay in the client, on the same pipeline that scores every session.

Because a NeuroSky headset is a single dry forehead channel, ICA-based
artifact removal (which needs multiple channels to unmix a component)
isn't usable here. Instead the endpoint uses:

- `mne.preprocessing.annotate_muscle_zscore` for EMG/muscle bursts
- `mne.preprocessing.annotate_amplitude` (flat-only) for a disconnected
  or dead channel
- a manual sliding-window peak-to-peak check for blinks and motion —
  MNE's `annotate_amplitude` fires on *consecutive-sample* jumps rather
  than peak-to-peak within a window, which turns out to miss ordinary
  blinks (they rise and fall over ~100–300ms, not between two adjacent
  samples), so a conventional windowed-PTP check is used instead, the
  same statistic tools like autoreject or a manual `mne.Epochs` reject
  dict use.

To use it:

1. In the app, go to **Progress → Settings** and turn on **Record raw for
   MNE audit**. This keeps the full-resolution waveform for your *next*
   session in memory only — it's not saved to your profile and adds
   nothing to Export save.
2. Run one session.
3. Back on **Progress**, use **Analyze with MNE** to POST it to
   `/api/analyze-artifacts`, or **Download raw** to save it and run
   the same analysis offline:

   ```
   pip install -r api/requirements.txt
   python api/analyze-artifacts.py neurodex-session-raw.json
   ```

The response reports a reject fraction, the flagged bad segments, and
time attributed to muscle vs. amplitude artifacts — compare it against
the live reject rate on the Models tab for the same session.

**Caveat noted in the endpoint's own output:** raw NeuroSky samples
aren't calibrated volts, so the amplitude thresholds use a rough 1e-6
scaling factor to land in a plausible EEG microvolt range. That's a
normalization, not a calibration — treat the amplitude-based numbers as
directional, not absolute.

## Not a medical device

Nothing here diagnoses or treats tinnitus, chronic pain, or depression.
Every result from this pilot is exploratory self-experimentation (n=1),
against a theory that is itself still debated in the literature.
