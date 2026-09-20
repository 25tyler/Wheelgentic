# P4 item 2 — suds build-up as cleanliness rises

## Sizing, done BEFORE building (the anticipation lesson)

Framing is PER PROJECTOR SHAPE, not one number: `fit()` moves the camera with
the aspect ratio, so a world unit covers **385.4 px at 1920x1080, 256.9 at
1280x720, 211.2 at 1024x768**. (This said a flat 227, measured before the FOV
42->26 reframe and the aim-point move, and every figure below descended from
it.) A splotch sprite is `wid * 0.32` = 0.06991 world = **26.9 / 18.0 / 14.8
px**. Foam at 1.15x that is **31.0 / 20.7 / 17.0 px**.

For contrast, in the same pixels, all measured at 1080p only:
- stroke roll sponge travel: **41 px**
- anticipation, REVERTED as invisible: **7.6 px**

Foam sits between them. It differs from anticipation in one way: anticipation
was a POSITION delta competing against a 5.4x larger position delta in the same
frames, while foam is a COLOUR/TEXTURE change, so it is not fighting for the
same visual channel.

DO NOT READ THAT AS "SAFER". The splotch sprites are also colour-on-limb, and
they still failed to read: at 0.78 they formed ONE contiguous brown mass --
22,756 dirt pixels in a 260x205 union with ZERO column gaps -- and only a scale
re-solve to 0.32 broke them apart (avatar.js:517-523). Giving each splotch its
own silhouette first did NOT work, because at 29px spacing each outline falls
inside its neighbour. Being a colour change bought nothing on its own.

So the channel argument only says foam is not doomed the way anticipation was.
It says nothing about whether foam reads. Verify by eye, at three cleanliness
levels, against a control -- and expect to solve a size, not just place a
sprite.

## Attachment: rec.holder, NOT rec.sprite.getWorldPosition()

    REAL rec (avatar.js:534): { sprite, holder, part, t, gone, baseScale }
    STUB rec (main.js:336)  : { part, t, gone, baseScale, sprite }  <- NO holder

`holder` is a Group already parented to `node[part]` and positioned along the
limb's MEASURED axis (limbLocalBox). Adding a second sprite to it inherits
that placement for free.

The stub avatar (GLB 404 -> degraded boot) has no holder, so a
`rec.holder`-based build-up **no-ops silently**. A `getWorldPosition`-based one
would instead pile every foam sprite at the stub's hardcoded `(0, 1.5, 0)` --
fail WRONG rather than fail silent. Same class as reading the sponge's world
position to measure a shoulder rotation: right call, wrong source.

## Verification plan

1. Eye check at three cleanliness levels (0%, 50%, 100%) against a control.
2. The blocked-GLB path specifically. `test_degraded_boot.py:64` records its own
   near-miss: it once blocked `character-a.glb` after the model had been swapped
   to the mini pack, so it blocked NOTHING and passed while testing nothing.
   Any foam behaviour must be checked under `**/mini-character.glb` blocked.
3. Re-record the backup (web/ files are in the six-file digest), then gate.
