# Screen-space verification

Code review cannot catch splotch placement. The bug below passed every syntax
check, every console-error check, and every DOM probe — and put dirt on the
character's shorts.

`window.__wheelgentic` exposes `{scene, camera, avatar, recs, renderer}` so a
Playwright test can project a splotch's world position to screen pixels and
assert it lands inside the limb's screen box.

```
arm-left occupies screen x[715,760] y[209,390]
  splotch t=0.22 at (727,286)  ON ARM: True
  splotch t=0.50 at (726,316)  ON ARM: True
  splotch t=0.78 at (726,347)  ON ARM: True
```

Before the fix all three read `x=688` against an arm spanning `x=[692,755]` —
4px short, every time. Re-run this assertion after ANY change to camera
framing, `avatar.root.position`, the A-pose angle, or `addSplotch`.
