// web/coverage.js — the territories filling in as the arms clean them.
//
// WHAT WAS MISSING. The scan shows which arm owns which patch of the person,
// and the counter shows how clean they are, but nothing connected the two: an
// audience could not see the machine WORKING THROUGH a body. The coverage
// sweep is the single most legible way to show four agents dividing a job and
// finishing it, and it was the one piece of the backend's story with no
// picture.
//
// scrub3d's own developer console draws exactly this (`viz.py` paints visited
// cells at a different colour from owned-but-unvisited ones). This is that
// idea on the projector, which is where the audience is.
//
// HOW IT WORKS. Each arm sweeps its own territory in a spatial order rather
// than at random: cells sort by distance from that arm's base, so the sweep
// reads as an arm working outward from where it stands, the way a person
// wiping a surface does. Random order reads as static noise resolving, which
// says nothing about the machine.
//
// IT DRIVES THE EXISTING COLOUR BUFFER. No second cloud, no extra draw call
// -- the same 5824 points brighten in place. A cleaned cell goes from its
// arm's colour at low intensity to full white-hot, which is the same visual
// grammar as the dirt splotches popping.

// THE CLEANED COLOUR LIVES IN territories.js, which owns the point cloud both
// this module and focus() write. Two copies of three floats is how the sweep
// and the dimming pass end up disagreeing about what "clean" looks like.
import { CLEAN_RGB as CLEAN } from './territories.js';

export function makeCoverage(territories, data) {
  if (!territories || !data) return null;

  const geo = territories.points.geometry;
  const col = geo.getAttribute('color');
  const owner = data.cells.owner;
  const pos = data.cells.pos;
  const n = owner.length;

  // THE CONTROLLER'S OWN ORDER WHEN THE BAKE CARRIES ONE.
  //
  // `cells.cover_order` is what scrub3d/control.py actually does: a geodesic
  // field over the surface, reach margin as a term, and a traverse when the
  // local neighbourhood is done. tools/export_body.py runs it and ships the
  // order the sponge arrives in.
  //
  // The fallback below is a RADIAL FAN -- cells sorted by distance from the
  // arm's base -- and it is not what an arm does. Measured against the real
  // order on body A, the two rank-correlate -0.62 to -0.99: the controller
  // works INWARD and the fan works outward, so the fallback animates very
  // nearly the reverse of the truth. It stays only so an older baked body
  // still moves instead of freezing.
  const arms = (data.arms || []).map((a) => a.pos || [0, 0, 0]);
  const real = data.cells.cover_order;
  let queues;
  if (Array.isArray(real) && real.some((s) => s && s.length)) {
    // Already in arrival order, so the sort below must NOT run on these --
    // sorting them by distance would throw away the very thing they carry.
    queues = [0, 1, 2, 3].map((a) => (real[a] || []).map((i) => [0, i]));
  } else {
    queues = [[], [], [], []];
    for (let i = 0; i < n; i++) {
      const a = owner[i];
      if (a < 0 || a > 3) continue;         // unreachable cells are never swept
      const base = arms[a] || [0, 0, 0];
      const p = pos[i];
      const d = (p[0] - base[0]) ** 2 + (p[1] - base[1]) ** 2 + (p[2] - base[2]) ** 2;
      queues[a].push([d, i]);
    }
    queues.forEach((q) => q.sort((x, y) => x[0] - y[0]));
  }

  // Remember every original colour so a reset is exact rather than
  // recomputed -- recomputing would have to re-derive the class-based
  // intensity and would drift from whatever territories.js decided.
  const original = new Float32Array(col.array);

  let heads = [0, 0, 0, 0];

  // TELL territories.focus() WHICH CELLS IT MUST NOT REPAINT. Both functions
  // write the same colour buffer: this one marks a cell scrubbed, focus()
  // repaints every cell to dim an unfocused region. Whichever ran last won,
  // and focus() runs on every phase change -- which is every splotch pop.
  // Measured per animation frame: the body climbed to 265 cells and snapped
  // back to 0 three times in a single scrub.
  //
  // A cell's position in its arm's queue is below that arm's head exactly
  // when the sweep has painted it, so the predicate is a lookup rather than a
  // second copy of the state.
  const rank = new Int32Array(n).fill(-1);
  for (let a = 0; a < 4; a++)
    queues[a].forEach(([, idx], k) => { rank[idx] = k; });
  territories.setCleanTest?.((i) => {
    const a = owner[i];
    return a >= 0 && a <= 3 && rank[i] >= 0 && rank[i] < heads[a];
  });
  let raf = null;
  let running = false;
  let onProgress = null;

  function cleanedCount() {
    return heads.reduce((s, h, a) => s + Math.min(h, queues[a].length), 0);
  }
  function totalCount() {
    return queues.reduce((s, q) => s + q.length, 0);
  }

  // PACED TO THE SCRUB, NOT TO THE FRAME RATE. Two cells per arm per frame
  // sweeps all 699 in 1.5 seconds -- it flashes past and an audience sees a
  // colour change rather than a machine working. The scrub cycle runs about
  // 17s, so the sweep should fill most of it: 699 / (4 arms x 12s x 60fps)
  // is 0.243 cells per arm per frame.
  //
  // A fractional rate needs an accumulator rather than a loop count, or it
  // truncates to zero every frame and nothing ever moves.
  // PER-ARM RATES, SO THEY FINISH TOGETHER. A single shared rate looks
  // wrong for a reason that only shows up in the arithmetic: the arms own
  // very different amounts (273 / 135 / 127 / 164 cells on body A), so at one
  // rate arm 2 finishes at 8.5s and arm 0 at 18.2s, leaving a ten second tail
  // where three arms sit still and one keeps going. That reads as three
  // stalls, not as a team finishing a job.
  //
  // Scaling each arm's rate to its own share makes them land together, which
  // is also what real arms dividing a body would do -- the whole point of the
  // partition is balancing the work.
  const SWEEP_SECONDS = 13;
  const rates = queues.map((q) => q.length / (SWEEP_SECONDS * 60) || 0);
  const accs = [0, 0, 0, 0];

  function step() {
    if (!running) return;
    let any = false;
    // All four advance together, each at its own pace, so they finish at the
    // same moment rather than one at a time.
    for (let a = 0; a < 4; a++) {
      accs[a] += rates[a];
      const take = Math.floor(accs[a]);
      accs[a] -= take;
      for (let k = 0; k < take; k++) {
        const q = queues[a];
        if (heads[a] >= q.length) break;
        const i = q[heads[a]][1];
        col.setXYZ(i, CLEAN[0], CLEAN[1], CLEAN[2]);
        heads[a] += 1;
        any = true;
      }
    }
    // Every arm finished: stop, but do not report "nothing happened" on the
    // frames between whole cells, or the sweep halts on its first fraction.
    if (!any && heads.every((h, a) => h >= queues[a].length)) {
      running = false; raf = null;
      if (onProgress) onProgress(cleanedCount(), totalCount());
      return;
    }
    col.needsUpdate = true;
    if (onProgress) onProgress(cleanedCount(), totalCount());
    raf = requestAnimationFrame(step);
  }

  return {
    /** Start the sweep. `cb(done, total)` fires each frame so the HUD can
     *  read the same numbers the cells are showing. */
    start(cb) {
      onProgress = cb || null;
      if (running) return;
      running = true;
      raf = requestAnimationFrame(step);
    },
    stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    },
    /** Paint every arm's territory up to `frac` of the way through it.
     *
     *  THE SWEEP RIDES THE COUNTER RATHER THAN A CLOCK. Measured on a real
     *  CAM=fake cycle: the scrub finishes in 10s, the free-running sweep is
     *  paced for 13s, so it reached 127 of 699 cells and then the cycle's
     *  own reset wiped it. An audience saw the HUD say 100% while the body
     *  beside it was 18% clean -- two meters contradicting each other on the
     *  beat the whole demo is built around.
     *
     *  Driving the cells off the same fraction the counter reads means they
     *  can never disagree, which is the reason the suds level already works
     *  this way (see avatar.setSuds in main.js). It also removes the guess:
     *  there is no correct value for SWEEP_SECONDS, because the cycle's
     *  length depends on how fast the splotches pop.
     *
     *  Idempotent, so calling it every frame at the same fraction is free,
     *  and monotonic within a cycle: it only ever paints forward, because
     *  cells going back to dirty mid-scrub reads as the machine undoing its
     *  own work. reset() is the only way back.
     */
    seek(frac) {
      const f = Math.max(0, Math.min(1, frac || 0));
      let touched = false;
      for (let a = 0; a < 4; a++) {
        const q = queues[a];
        const want = Math.round(f * q.length);
        while (heads[a] < want) {
          col.setXYZ(q[heads[a]][1], CLEAN[0], CLEAN[1], CLEAN[2]);
          heads[a] += 1;
          touched = true;
        }
      }
      if (touched) col.needsUpdate = true;
      return touched;
    },
    /** Put every cell back to the colour territories.js gave it. */
    reset() {
      this.stop();
      heads = [0, 0, 0, 0];
      accs.fill(0);
      col.array.set(original);
      col.needsUpdate = true;
    },
    get total() { return totalCount(); },
    get done() { return cleanedCount(); },
  };
}
