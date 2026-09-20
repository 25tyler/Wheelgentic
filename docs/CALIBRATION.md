# Calibration — the 90-second venue drill

`homography.pkl` is **not in git**, deliberately. It is specific to one camera
position on one table. A committed one would silently ship a wrong transform.

## Run it

```bash
cd ~/Wheelgentic
rm -f homography.pkl
python py/calibrate.py          # click TL, TR, BR, BL — clockwise
```

**You do not need to delete `.homography-is-synthetic` by hand.** If a test
fixture generated the current transform, that sentinel file sits beside it and
every run prints `THIS IS A TEST FIXTURE, NOT A REAL CALIBRATION` in a banner.
`calibrate.py` retires it for you on a successful solve — deliberately, because
the card's `rm homography.pkl && python py/calibrate.py` leaves it behind, and
an operator who learns to ignore that banner has also learned to ignore it on
the day it is true. It is the banner standing between the sponge and a forearm
30cm off target.

**This procedure needs a real camera.** `calibrate()` and `verify()` open
`cv2.VideoCapture` directly and have no `CAM=fake` path (unlike
`tools/tune_dirt.py`, which does). There is no way to rehearse the clicking on
this laptop; `tests/test_calibration.py` covers the solve math, the
corner-order guard and the sentinel retirement instead.

## The two details that matter

**Tape the A4 sheet on top of a BOOK, at forearm height (~45mm).** Not flat on
the table. Calibrating at table height leaves ~16mm of parallax error; at
forearm height it is zero. Costs 30 seconds.

**Click clockwise from top-left: TL, TR, BR, BL.** Clicking TL,TR,BL,BR instead
raises no exception and produces a ~693mm error. The guard catches it — if you
see `CORNER ORDER WRONG`, just re-click.

## Then VERIFY — this is not optional

The script runs `verify()` automatically. Put a coin on the sheet, click it,
and **measure it with a tape**.

- Within 10mm → GO. The sponge compresses 10–15mm and absorbs this.
- Off by 20mm+ → you mis-clicked a corner. `rm homography.pkl` and redo.

**Why a tape measure and not more code:** a 4-point homography is exact by
construction, so its residual reads `0.000000` no matter where you clicked.
Measured: a corner off by 30px → 14mm true error, 60px → 27mm, 150px → 60mm,
residual `0.000000` every time. Diagonal ratios, midpoint separation and
triangle areas all drift smoothly and cannot discriminate. A fifth independent
observation is the only thing that can.

## When to redo it

- Someone bumps the tripod or the table (they will)
- You move the camera
- The arm scrubs consistently off-target
- `[arm] WARNING: target ... clamped 400mm` appears — that means the transform
  is wrong, not that the arm is confused

Tape the tripod down. Tape the arm down. Then it stays valid.

## Where the camera goes, and why the script now says so

The tape check above catches a mis-click that HAPPENED. It says nothing
about how much a mis-click would COST, and that depends entirely on
where you put the camera.

The script now prints one line for this after you click:

    click sensitivity: 0.35 mm per pixel (a 2px mis-click costs 1mm)

That is how far the answer moves for one pixel of error on one corner.
It is the number to act on, because it is the one you can change by
moving the tripod.

- under 5 mm/px, nothing is printed beyond the line itself. Good.
- 5 to 10, a NOTE. Squarer or closer would help.
- over 10, a loud banner. Move the camera and re-click.

Measured across 90 legitimate camera placements rendered through a
pinhole model, 0.5m to 3.5m and 0 to 70 degrees oblique: the median is
1.13 mm/px and the worst legitimate setup, 3.5m away at 70 degrees,
reads 5.18. Degenerate click sets start at 9.86. So the bands cannot
reject a real placement, and they do catch every degenerate one
measured.

Two ways to land in the loud band, both of which used to pass in
silence:

- **Camera nearly edge-on to the sheet.** 1px of hand tremor becomes
  80mm at
the sheet centre. The residual reads `0.000000`.
- **Camera so far back the sheet is small in frame.** 1px becomes 20mm.
  The
quad still looks a perfectly sensible SHAPE; it is the SCALE that is
wrong, which is why a shape-only check misses it.

**It warns, it never refuses.** A hard stop here could refuse to
calibrate at the venue, and a calibration you know is shaky still runs a demo where a
hard stop does not.

## Why this file and not scrub3d/handeye.py

`scrub3d/handeye.py` is the better instrument for the product: a full 3D
rigid transform by Kabsch, with a conditioning gate, a fifth-point check
and a cross-validation that makes four arms agree about one point in the
room. A person in a wheelchair is not a plane, so eventually that is the
right answer.

It is not the answer today, and the reason is input, not quality. It
needs 15 to 20 poses of the arm's OWN confirmed tool-point feedback.
**The CAN adapter is not attached and this arm has never moved**, so
there is nothing to feed it. It stays where it is, with its self-test
passing, until the arm can report a position.

What did cross over is the idea that makes it good. handeye refuses a
fit whose SAMPLES are badly spread however clean its residual looks,
because "the residual looks excellent exactly when the answer is worst".
A 4-point homography has that disease in a worse form: its residual is
zero by construction. That is GUARD 3 above. Its GO/HOVER bands crossed
over too, into `classify()`.

handeye's own `conditioning()` number does not port directly, and this
is worth writing down so nobody retries it: it is sigma_min/sigma_max on
a 3D cloud, and four clicked pixels are coplanar by definition, so it
reads 0.000 for a perfect click set and a hopeless one alike. The 2D
version is better but still rates the too-far-away case at 0.49,
healthy-looking, while that setup turns 1px into 20mm. Measuring the
millimetres directly is what works.
