# THE PLAN — one application, the whole wheelchair vision

Written against `docs/sources/STANDING-PROMPT.md`. Re-read that
file first; it outranks this one.

---

## What the product is

An **AI wheelchair** with **four robotic arms** that:

1. **Showers** the person — the arms scrub a body the camera measured.
2. **Feeds** them — eating, drinking, dispensing pills.
3. **Senses vitals** — heart rate and pulse oximetry from the chair.
4. **Takes voice commands** — a personal agent, not a button panel.

The pitch underneath it: bathing is the first daily activity people lose,
74.5% of US residential care residents need help with it, caregivers are
injured at 5x the industry rate, and being undressed and scrubbed by another
person strips dignity. A machine that does it on demand gives that back.

## Where the two branches stand

| | `main` | `scrub3d` |
|---|---|---|
| Role | the frontend | the backend |
| Size | ~2,900 lines of `web/` | 36,457 lines, 1,141 files |
| Has | cartoon avatar mirroring a person, HUD, sound, confetti, 24 GLB models including **4 wheelchairs** | body scan from a real capture, 4-arm territory partition, live pose tracking, arm placement search, safety governor, torque contact, Sapiens segmentation, browser rig editor, dev console with live 3D |
| Shows on | the projector, three.js | Rerun (a desktop viewer, deliberately not the projector) |
| Person | a stylised cartoon | a measured body model |

**The gap that defines the work.** scrub3d measures a real person and drives
four arms safely, but shows it in a developer tool. `main` has the beautiful
projector page, but its world is one arm and a cartoon forearm. Neither alone
is the demo.

## The merge, in one sentence

**scrub3d's four-arm body model, territories and safety drive `main`'s
projector page, with the cartoon still standing in for the person.**

The cartoon stays. It is the privacy mechanism and the reason a room full of
judges can watch without watching a real body. What changes is that behind it
sits a real measured person, four real territories, and a real governor.

---

## STATUS, 2026-09-19

**All six are built.** What each turned into, and the one that changed shape:

P1 was planned as territories painted ON the cartoon. Four attempts looked
wrong in a screenshot, and the cause was not a number: the backend's body has
a real adult's proportions and the cartoon's head is the size of its torso, so
no single scale maps one onto the other. It stands BESIDE the person instead,
which is also the better demo -- the drawn person and the measured person side
by side.

Added beyond the plan, because the walkthrough found them missing: the safety
governor's verdicts on screen, a second body that proves the partition
re-solves, the room, per-arm reach, and sound on the new beats.

**Still not done, and not doable by a tool call:** all the hardware, the two
rules questions, and a rehearsal with a person reading the script aloud.

---

## Build order, highest visible gain first

### P0 — The projector page becomes the wheelchair scene ✅ DONE
The single biggest visual jump. Right now the frame is one character and one
arm on a plinth. It becomes: the cartoon **seated in a wheelchair**, four arms
mounted around the chair, each owning a region of the body, all four working
at once.

- Wheelchair GLBs are **already vendored** (`web/assets/wheelchair*.glb`, 4 of
  them). No modelling.
- Arm meshes are **already in scrub3d** (Waveshare's own STLs, MIT).
- Four arms instead of one is a loop over the existing `robotarm.js`.

### P1 — Territories, live ✅ DONE (beside the person, not on them)
Each arm owns a contiguous region of the person. Colour those regions on the
cartoon, and fill them in as each arm finishes. This is scrub3d's
`partition.py` output rendered on main's avatar — the "no preprogrammed
paths, every body is different" claim made visible.

### P2 — The four capabilities as real modes ✅ DONE
Shower is one mode. Add feeding, pills, vitals, voice. Each is a visible state
on screen with its own arm choreography. This is what turns a scrubbing demo
into the wheelchair product.

### P3 — Vitals panel ✅ DONE (live BPM + PQRST trace)
Heart rate and SpO2 on screen. The hardware list has MAX30102 sensors; until
they exist, drive the panel from a simulated feed so the visual is real and
the source swaps later.

### P4 — Voice control ✅ DONE (commands and answers)
Speak to it and it acts. Wake word, a few intents mapped to the modes above.

### P5 — The impressive extras ✅ DONE (governor verdicts, second body)
Whatever makes a judge lean forward: the body reconstruction shown as the arms
plan over it, coverage sweeping to 100%, a dashboard of what each arm decided
and why it refused.

---

## How the work gets done — the standing rules

- **Take, do not build.** For every asset, animation, effect and component:
  search GitHub, npm, asset libraries and the 292 ECC skills FIRST. Record
  what was searched. Hand-building is the failure case and has already cost
  this project two thrown-away characters.
- **Use ECC to its full potential.** `motion-foundations` / `motion-patterns`
  / `motion-advanced` for animation. `frontend-design-direction` and
  `design-system` for the visual language. `ui-demo` and
  `remotion-video-creation` for capture. `orch-build-mvp` for orchestration.
  68 agents to delegate to rather than doing everything inline.
- **Impressive beats correct.** A demonstration, not a product. Bugs are the
  lowest priority.
- **Commit often. Document as you go** in `docs/PROGRESS.md`.
- **Never stall.** No blockers exist; there is always a higher-ROI task.

## Known setup facts, measured

- scrub3d needs `scipy`, `rerun-sdk`, `trimesh`, `pyrealsense2`, `open3d`.
  Installed into `venv/`.
- `scrub3d/data/scan01` referenced by the README **does not exist**. The real
  data is two live recordings, `live_rec_20260916` and `live_rec_20260917f300`
  — lossless PNG frames of a real person, replayable with no camera.
- `live_body.py` imports `pyrealsense2` at module scope, so replay needs the
  SDK installed even without a camera.
