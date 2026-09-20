// web/juice.js — bubbles, confetti, counter, sound, shake. The whole "feel"
// layer, and where the wow-per-hour lives.
import * as THREE from 'three';
// canvas-confetti, gsap and zzfx load as plain <script> tags in index.html and
// live on window — no import, no ESM interop headache.

// canvas-confetti creates its OWN position:fixed z-index:100 canvas. It
// composites over WebGL with ZERO integration — no shared renderer, no
// depth-buffer fight, no resize handling. THIS is what collapses the
// hardest-looking integration problem in the project into one script tag, and
// why three.quarks / three-nebula (3-4 hours of batched-renderer wiring) are
// the wrong call for occlusion nobody can perceive on a projector at 10 feet.

// shapeFromText RASTERIZES at creation, so the scalar here MUST match the
// scalar in the burst or particles are blurry. Build ONCE at startup.
let BUBBLE = null;
let DROP = null;
export function initShapes() {
  try { BUBBLE = confetti.shapeFromText({ text: '🫧', scalar: 3 }); }
  catch (_) { BUBBLE = null; }
  // THE WATER DROPLET, baked the same way and for the same reason: the
  // rasterisation happens once at startup, so the scalar here must match the
  // scalar every emitter draws it at. Smaller than the bubble because a
  // droplet that reads the same size as foam is just more foam.
  try { DROP = confetti.shapeFromText({ text: '💧', scalar: 2 }); }
  catch (_) { DROP = null; }
  // If U+1FAE7 renders as tofu on the venue machine, pop() already falls
  // back to shapes:['circle'] with a light blue palette.
}

const _v = new THREE.Vector3();

export function popSplotch(rec, camera) {
  if (rec.gone) return;
  rec.gone = true;
  rec.sprite.getWorldPosition(_v).project(camera);        // -> NDC, -1..1
  // Same guard as the finale: confetti is a global from a classic script, and a
  // throw here skipped BOTH the sound and the hide below it.
  if (typeof confetti === 'function') confetti({
    particleCount: 42, spread: 72, scalar: 3,
    shapes: BUBBLE ? [BUBBLE] : ['circle'],
    colors: ['#9fdcff', '#d7f2ff', '#ffffff'],
    gravity: -0.35,        // NEGATIVE gravity => bubbles RISE. Literally the
                           // physics you want, in one option.
    decay: 0.94, ticks: 130,
    origin: { x: (_v.x + 1) / 2, y: 1 - (_v.y + 1) / 2 }, // NDC -> top-left
  });
  play('squish'); play('sparkle');
  // A VENDOR 404 USED TO LEAVE THE DIRT ON SCREEN. `onComplete` below is the only
  // thing that hides the sprite, so with gsap missing the splotch was marked gone
  // and stayed VISIBLE -- measured by blocking **/gsap.min.js: rec.gone
  // [true,false,false] while sprite.visible stayed [true,true,true]. The scale-away
  // is decoration; the hide is state, so the hide must not depend on the library.
  if (window.gsap) {
    gsap.to(rec.sprite.scale, {
      x: 0, y: 0, duration: 0.30, ease: 'back.in(3)',
      onComplete: () => { rec.sprite.visible = false; } });
    gsap.to(camera.position, { x: '+=0.04', duration: 0.05,
                               yoyo: true, repeat: 5 });   // screen shake
  } else {
    rec.sprite.visible = false;                            // no tween, same STATE
  }
}

/** WATER, falling, where the sponge is working.
 *
 *  The mode strip says SHOWER and nothing on screen was wet. Four arms rubbed
 *  a dry cartoon with a dry sponge, which is the one thing in this demo a
 *  judge already knows the look of without being told.
 *
 *  OPPOSITE OF THE FOAM IN EVERY NUMBER THAT MATTERS, so the two read as
 *  different substances rather than two settings of one effect:
 *
 *      foam   gravity -0.22  spread 38  ticks 55  rises, clusters, lingers
 *      water  gravity +1.10  spread 95  ticks 38  falls, throws wide, gone
 *
 *  Subordinate to the splotch pop in count, as the foam is: the pop is the
 *  money shot and neither of these may compete with it.
 *
 *  Rides the same 260ms stroke tick the foam does, so it inherits the stroke
 *  cap and the estop stop-path for free and claims no sensing of its own --
 *  see the note on sudsAt below, which applies here word for word.
 */
export function sprayAt(worldPos, camera) {
  try {
    _v.copy(worldPos).project(camera);
    const o = { x: (_v.x + 1) / 2, y: 1 - (_v.y + 1) / 2 };
    confetti({
      particleCount: 7, spread: 95, scalar: 2,
      shapes: DROP ? [DROP] : ['circle'],
      colors: ['#bfe6ff', '#9fdcff', '#ffffff'],
      gravity: 1.10,             // falls, unlike the foam
      decay: 0.86, ticks: 38,    // ~0.6s: a spray, not a mist that hangs
      startVelocity: 17,
      origin: o,
    });
  } catch (_) {}   // water must never kill a frame; it fires 27x per scrub
}

/** A few suds where the sponge is touching RIGHT NOW.
 *
 *  WHY THIS EXISTS: between the arm arriving and the first splotch popping
 *  there are several seconds where a judge watches a sponge rub and nothing
 *  happens. The pop is the money shot; this is the working-right-now signal.
 *
 *  RIDES THE STROKE, NOT A CONTACT SENSOR -- and that distinction is the whole
 *  reason this is honest. `m.scrub && m.contact` looks like the right hook but
 *  is NOT: fire() is called only when a splotch pops, and it sets scrub=True in
 *  the same call, so that branch is true on exactly three moments per cycle --
 *  alongside popSplotch's own burst. Checked in py/scrubbot.py rather than
 *  assumed.
 *
 *  So this hangs off startScrubChoreography's 260ms stroke tick, which is what
 *  already MOVES the cartoon sponge. Foam there is a drawing of the rubbing,
 *  exactly as the stroke itself is; it claims no sensing. The COUNTER is the
 *  thing that only moves on real contact, and nothing here touches it.
 *  DEMO-SCRIPT.md:31-33 confirms the only sensing claim is about the counter,
 *  made at 1:32 AFTER the first pop -- through the 1:10-1:22 scrub window the
 *  presenter says "Watch" and then nothing.
 *
 *  Deliberately much smaller than popSplotch's burst: this fires up to 27 times
 *  per scrub and must read as "working", never competing with the pop.
 */
/** @param n how many particles. Four arms foaming at the single-arm count
 *  is four times the density this was tuned at -- a wall of white rather
 *  than suds -- so the callers share the budget. */
export function sudsAt(worldPos, camera, n = 5) {
  try {
    _v.copy(worldPos).project(camera);
    confetti({
      // scalar MUST be 3 to match BUBBLE's bake at :18 -- see the invariant at
      // :14. Drawn at 1.5 the pre-rasterized sprite resamples to a broken
      // outline that reads as lens scratches, not foam. Stay subordinate to
      // popSplotch through count and spread (5 vs 42, 38 vs 72), never scalar.
      particleCount: n, spread: 38, scalar: 3,
      shapes: BUBBLE ? [BUBBLE] : ['circle'],
      colors: ['#ffffff', '#d7f2ff', '#bfe6ff'],
      gravity: -0.22,        // rise, like popSplotch -- but gentler
      decay: 0.90, ticks: 55,        // ~0.9s, gone before the next stroke
      startVelocity: 12,
      origin: { x: (_v.x + 1) / 2, y: 1 - (_v.y + 1) / 2 },
    });
  } catch (_) {}   // foam must never kill a frame -- it fires 27x per scrub
}

// ---- THE COUNTER. back.out(2.2) is the overshoot bounce, in one line. ------
const hud = { pct: 0 };

// EVERY METER ON SCREEN READS THE SAME NUMBER. #pct and #fill already render
// from hud.pct; the measured body beside the person needs to as well, or it
// drifts. Measured before this existed: the cells were painted from the raw
// popped-splotch count while the number came from a back.out(2.2) tween, so
// the body froze at exactly one third while the counter climbed past it to
// 100% -- and the tween's overshoot made the number itself go 38, 34, 33.
//
// A subscriber list rather than an exported mutable, so the tween stays the
// only writer and a second consumer cannot start setting the value.
const pctWatchers = [];
export function onCleanPct(fn) { if (typeof fn === 'function') pctWatchers.push(fn); }
function emitPct(p) { for (const fn of pctWatchers) { try { fn(p / 100); } catch (_) {} } }
// THE VITALS READOUT BORROWS #pct, AND setClean IS ITS ONLY OTHER WRITER.
// Vitals mode paints a live heart rate into #pct on its own 900ms timer, but
// the scrub cycle keeps calling setClean() and repainting a percentage over
// it. Measured on a full run of show: entering vitals after a completed cycle
// showed "100%" under a HEART RATE label, which is two readouts disagreeing
// about the one number an audience is watching.
//
// A flag rather than a mode import, because juice.js has no business knowing
// what a mode is -- it only needs to know whether something else owns the
// element right now.
let pctBorrowed = false;
export function borrowPct(on) {
  pctBorrowed = !!on;
  // AND STOP ANY TWEEN ALREADY WRITING THE ELEMENT. The flag is checked when
  // setClean is CALLED, but once gsap.to is running its onUpdate writes #pct
  // every frame for 0.8s with no check at all. So a splotch pop followed
  // within 800ms by pressing 9 left a percentage flickering over the heart
  // rate, under a HEART RATE label.
  //
  // Killing the tween is the root fix. Checking the flag inside onUpdate
  // instead would leave the tween running and silently dropping frames, so
  // hud.pct would land somewhere nobody rendered -- and the next reader of it
  // would inherit that. resetCounter already kills tweens for the same reason.
  if (pctBorrowed && window.gsap) gsap.killTweensOf(hud);
}

export function setClean(target, onComplete) {
  // Someone else owns the readout. Still run the callback so the finale and
  // every other consequence of reaching 100 still fire.
  if (pctBorrowed) {
    // SOMEONE ELSE OWNS THE READOUT, BUT THE CYCLE STILL FINISHED. Returning
    // early skipped finale() entirely, so reaching 100% while vitals had the
    // element gave the counter's consequences with none of its celebration:
    // no confetti, no fanfare, no green, on the demo's payoff beat. Then
    // leaving vitals printed a bare 100% for a cycle nobody saw complete.
    //
    // The number is not ours to paint right now; the celebration is.
    hud.pct = Math.min(100, Math.max(0, Number(target) || 0));
    emitPct(hud.pct);
    if (hud.pct >= 100) finale();
    if (onComplete) onComplete();
    return;
  }
  // THE COUNTER MUST NOT DEPEND ON A CDN FETCH. The tween's onUpdate below is the
  // ONLY writer of #pct and #fill in the whole of web/, so a missing gsap left the
  // projector reading 0% for the rest of the demo while the operator pressed
  // 1/2/3 -- the fallback RECOVERY-CARD.md promises in six separate rows,
  // including "works with the server dead". Measured by blocking the script.
  //
  // Paint it directly and fire the finale, then return: no bounce, right number.
  if (!window.gsap) {
    // `Number(target) || 0` turns undefined, NaN and '' into a literal 0, and
    // a literal 0 is what the coverage subscriber reads as "this is a reset,
    // wipe the body back to dirty". A stray call would undo the cleaning.
    // Keep the last good value instead: a missed update is invisible, a wipe
    // is not.
    const n = Number(target);
    const p = Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : hud.pct;
    hud.pct = p;
    document.getElementById('pct').textContent = Math.round(p) + '%';
    document.getElementById('fill').style.width = p + '%';
    emitPct(p);
    if (p >= 100) { finale(); onComplete?.(); }
    return;
  }
  gsap.to(hud, {
    pct: target, duration: 0.8, ease: 'back.out(2.2)',
    onUpdate: () => {
      // back.out OVERSHOOTS — peak ~115 on a 0->100 tween. Without this clamp
      // the projector visibly prints "115%" in front of judges and the bar
      // overflows its container. Clamp at the RENDER site, not in the tween,
      // so the number keeps its punch.
      const p = Math.min(100, Math.max(0, hud.pct));
      document.getElementById('pct').textContent = Math.round(p) + '%';
      document.getElementById('fill').style.width = p + '%';
      emitPct(p);
    },
    onComplete: () => { if (target >= 100) { finale(); onComplete?.(); } },
  });
}

export function resetCounter() {
  hud.pct = 0;
  if (window.gsap) gsap.killTweensOf(hud);     // nothing to kill without it
  document.getElementById('pct').textContent = '0%';
  document.getElementById('fill').style.width = '0%';
  emitPct(0);
  document.body.classList.remove('complete');
}

export function finale() {
  play('fanfare');
  document.body.classList.add('complete');
  const end = performance.now() + 2600;
  // Confetti is a classic <script> global too: without it this threw on the FIRST
  // frame and took the rest of finale() with it, including the 'complete' class.
  // Guard the loop, not each call, so a missing library costs the burst and
  // nothing else.
  if (typeof confetti === 'function') {
    (function frame() {
      confetti({ particleCount: 7, angle: 60,  spread: 72, origin: { x: 0, y: 0.7 },
                 colors: ['#4ec9f5','#ffd93d','#ff6b9d','#6bcf7f'] });
      confetti({ particleCount: 7, angle: 120, spread: 72, origin: { x: 1, y: 0.7 },
                 colors: ['#4ec9f5','#ffd93d','#ff6b9d','#6bcf7f'] });
      if (performance.now() < end) requestAnimationFrame(frame);
    })();
  }
}

// ---- AUDIO. THE #1 DEMO-DAY FAILURE MODE IN THIS WHOLE TOPIC. -------------
// Browsers block ALL audio until a user GESTURE. An autonomous robot demo has
// no clicks. The entire demo is gated behind one keypress for this reason.
//
// ZzFX builds its OWN AudioContext at script-parse time and NEVER calls
// resume() anywhere in the package. Resuming some other library's context (or
// doing nothing) leaves the per-scrub sparkle SILENTLY DEAD — the sound most
// likely to fail on the projector machine.
let audioReady = false;
export function unlockAudio() {
  try { (window.zzfxX || window.ZZFX?.audioContext)?.resume(); } catch (_) {}
  try { zzfx(...[0.01, , 200, , , 0.01]); } catch (_) {}   // silent primer
  audioReady = true;
  console.log('audio unlocked');
}

const SFX = {
  sparkle: [1.2,,1200,.02,.2,.3,,1.8,,,600,.06,,,,,.1,.7,.05],
  squish:  [1.1,,180,.05,.12,.22,2,1.1,,,,,,.6,,.2,.05,.5,.04],
  ding:    [1.5,,900,.01,.15,.25,,2.2,,,400,.05,,,,,,.8,.04],
  // THE SCRUB BED. Same sound as `squish` at HALF VOLUME, and the difference
  // matters more than it looks. The scrub fires ~3.3 of these a second for the
  // whole cycle, and a splotch pop plays `squish` + `sparkle` on top of it.
  // MEASURED at equal volume: all three pops in a cycle landed within 2-3ms of
  // a bed squish, so the punchline of the demo was competing with its own
  // texture layer at the same loudness (1.1 vs sparkle's 1.2). A bed has to sit
  // UNDER the event it beds. Rate, tick and stop path are unchanged -- this is
  // only the mix.
  rub:     [.55,,180,.05,.12,.22,2,1.1,,,,,,.6,,.2,.05,.5,.04],
  // A LOW FALLING TONE FOR THE ESTOP. Deliberately not a pleasant sound and
  // deliberately the lowest note here: the estop is the one beat where the
  // audience should hear that something was STOPPED, not completed. 220Hz with
  // a negative pitch slide reads as a machine powering down.
  thunk:   [1.8,,220,.01,.18,.32,1,.7,-8,,,,,.3,,.1,.04,.6,.03],
  // A SHORT DRY CLICK for the skin/outfit keys. Menu feedback, nothing more --
  // short decay so holding a key down never turns into a tone.
  click:   [.8,,520,.01,.03,.06,,1.2,,,,,,,,,.02,.6,.01],
};
export function play(name) {
  if (!audioReady) return;
  if (name === 'fanfare') {
    [523, 659, 784, 1047].forEach((f, i) => setTimeout(() => {
      try { zzfx(...[1.4,,f,.02,.28,.4,,1.4,,,,,,.1,,,.06,.8,.03]); } catch(_){}
    }, i * 130));
    return;
  }
  // A NAME THAT IS NOT IN THE TABLE IS SILENTLY DEAD FOREVER, and that is the
  // worst shape a bug can take here. `zzfx(...undefined)` throws a TypeError,
  // the catch below swallows it to keep the frame alive, and the result is a
  // sound that never plays with nothing on the console to say why. The names
  // live in main.js and the table lives here, so any new beat crosses a file
  // boundary on a bare string with nothing checking the two agree.
  //
  // console.warn, not console.error: an unknown name must not redden a browser
  // test that collects console errors, because this is a developer's clue, not
  // a demo failure. The demo still loses only that one sound.
  if (!SFX[name]) { console.warn(`no sound named '${name}' in SFX`); return; }
  try { zzfx(...SFX[name]); } catch (_) {}   // never let audio kill a frame
}
