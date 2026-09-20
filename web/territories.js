// web/territories.js — the backend's four-arm territory split, on screen.
//
// WHAT THIS IS. `scrub3d` measures a person from a depth camera and solves
// which of the four arms owns which patch of their body. That is the
// project's real technical claim -- "no preprogrammed actions, every body
// type is different so paths are generated from the person's actual 3D
// model" -- and until now it existed only in a developer tool nobody watching
// the demo would ever see.
//
// The data is baked by tools/export_body.py: 1792 surface patches, each with
// a position, a normal, an area, and the arm that owns it. See that file for
// why it is baked rather than streamed.
//
// WHY POINTS AND NOT A MESH. The obvious render is the 13 anatomical meshes
// tinted per territory. It is the wrong one here: a solid second body sitting
// inside the cartoon would either z-fight with it or replace it, and the
// cartoon is the privacy mechanism -- putting a reconstruction of a real
// body on a wall in front of a room is the exact thing it exists to prevent.
//
// Points read as data ABOUT the person rather than as the person. A cloud of
// coloured dots hovering over the cartoon says "the machine has measured
// this" without ever drawing a body. It is also one draw call for all 1792.
import * as THREE from 'three';

// One colour per arm, matched to the four mode colours already in the HUD so
// the strip and the body agree. Unreachable cells are deliberately dim rather
// than hidden: a judge should see that the machine KNOWS what it cannot
// reach, which is a safety claim, not a gap.
const ARM_COLOURS = [
  new THREE.Color(0x4ec9f5),   // cyan
  new THREE.Color(0xffd94a),   // yellow
  new THREE.Color(0x6bcf7f),   // green
  new THREE.Color(0xff8b6b),   // coral
];
// Brighter than the background it sits against. At 0x2b3446 the
// unreachable cells were nearly the same value as the floor and the body's
// unowned bulk simply vanished.
const UNREACHED = new THREE.Color(0x66748f);

// THE COLOUR OF A SCRUBBED CELL, DEFINED ONCE. coverage.js paints it and
// focus() has to preserve it, so two modules need the same three numbers --
// and a copy in each is how they drift. Exported from here because this module
// owns the point cloud both of them write.
//
// A cool near-white rather than pure white: measured as luminance against each
// arm's colour, cyan, green and coral all move 0.30 or more but yellow moves
// only 0.16, so a cleaned yellow cell looked almost exactly like a dirty one
// and one arm in four appeared to do nothing. This is brighter than all four
// and bluer than three, so the change reads on every colour.
export const CLEAN_RGB = [0.85, 0.95, 1.0];

export async function makeTerritories(scene, opts = {}) {
  // Installed by the coverage sweep via setCleanTest. Null until then, so
  // focus() behaves exactly as it did before the sweep existed.
  let isClean = null;
  let data;
  const file = opts.file || 'body.json';
  try {
    const res = await fetch(`./assets/${file}`);
    if (!res.ok) throw new Error(`${file} ${res.status}`);
    data = await res.json();
  } catch (e) {
    // A missing bake must not cost the demo. Everything else on screen is
    // unaffected; this overlay simply never appears.
    console.warn('territories: no body.json, skipping overlay', e);
    return null;
  }

  const { pos, owner, klass } = data.cells;
  const n = pos.length;

  const geo = new THREE.BufferGeometry();
  const P = new Float32Array(n * 3);
  const C = new Float32Array(n * 3);
  // Per-point size: core cells are what an arm can definitely reach, so they
  // read largest. Fringe and contested are smaller, unreachable smallest.
  const S = new Float32Array(n);

  // The bake is in metres with Y up and the floor at y=0, but it is sized to
  // a REAL adult (about 1.75m) while the cartoon is a stylised figure seated
  // at a different scale. Fit the cloud to the cartoon rather than the other
  // way round -- the cartoon is what the audience is looking at.
  const scale = opts.scale ?? 1.0;
  const offset = opts.offset ?? new THREE.Vector3(0, 0, 0);

  // CENTRE IT ON ITS OWN FOOTPRINT FIRST. The bake's origin is the floor
  // under a seated person in scrub3d's world, not the middle of the body, so
  // placing it by offset alone put it wherever that happened to land. Centre
  // x and z, and drop y so the lowest cell sits ON the offset -- then the
  // caller's offset means "stand it here", which is what a caller expects.
  let cx = 0, cz = 0, lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) {
    cx += pos[i][0]; cz += pos[i][2];
    if (pos[i][1] < lo) lo = pos[i][1];
    if (pos[i][1] > hi) hi = pos[i][1];
  }
  cx /= n; cz /= n;

  for (let i = 0; i < n; i++) {
    P[i * 3 + 0] = (pos[i][0] - cx) * scale + offset.x;
    P[i * 3 + 1] = (pos[i][1] - lo) * scale + offset.y;
    P[i * 3 + 2] = (pos[i][2] - cz) * scale + offset.z;

    const a = owner[i];
    const col = a >= 0 ? ARM_COLOURS[a % 4] : UNREACHED;
    C[i * 3 + 0] = col.r; C[i * 3 + 1] = col.g; C[i * 3 + 2] = col.b;

    const k = klass[i];
    // UNREACHABLE CELLS MUST STILL DRAW THE BODY. 1093 of 1792 cells are
    // unreachable from a four-arm ring -- that is 61% and it is most of the
    // torso. At 0.35 they rendered so small that the body's bulk disappeared
    // and only the reachable limb bands showed: the cloud read as four
    // coloured tubes floating with no person between them.
    //
    // The floor is now 0.8. Size carries "how well can an arm work here";
    // COLOUR carries "who owns it", and unreachable is its own dim slate. The
    // silhouette is the job of every cell equally.
    S[i] = k === 'core' ? 1.0 : k === 'fringe' ? 0.92
         : k === 'contested' ? 0.86 : 0.8;
  }

  geo.setAttribute('position', new THREE.BufferAttribute(P, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(C, 3));
  geo.setAttribute('aSize', new THREE.BufferAttribute(S, 1));

  // A shader rather than PointsMaterial because PointsMaterial has ONE size
  // for every point, and the whole readability of this overlay is that core
  // cells look different from unreachable ones.
  const mat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,          // so it never punches a hole in the cartoon
    // NORMAL, NOT ADDITIVE. Additive looked right in principle -- a
    // measurement should glow -- but 1792 dots on a 0.42m body overlap
    // heavily, and additive sums every overlap toward white. Measured: the
    // cloud rendered as two solid white shapes on the thighs with no colour
    // left, so the ONE thing the overlay exists to show (which arm owns
    // which patch) was the first thing lost.
    blending: THREE.NormalBlending,
    uniforms: {
      uBase: { value: opts.pointSize ?? 7.0 },
      uViewH: { value: (typeof window !== 'undefined' ? window.innerHeight : 1080) },
      uFade: { value: 0.0 },    // 0 hidden, 1 fully visible; animated in
    },
    vertexShader: `
      attribute float aSize;
      varying vec3 vColor;
      varying float vSize;
      uniform float uBase;
      uniform float uViewH;
      void main() {
        vColor = color;
        vSize  = aSize;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        // PERSPECTIVE-CORRECT, AND SCALED FOR THE ACTUAL VIEWPORT. The first
        // version was uBase * aSize * (1.0/-mv.z) * 3.0, which at this
        // camera distance (~6.2 units) works out to about 3.5 pixels before
        // the round-disc discard eats the edges -- invisible on a projector.
        // Measured: the cloud was present, in the right place, fully faded
        // in, and painting nothing a screenshot could see.
        //
        // uViewH carries the drawing-buffer height so the size is in real
        // pixels rather than in whatever units the old magic 3.0 implied.
        // 0.016 -> 0.030. At 0.016 a 1792-point cloud over a 0.44m body
        // rendered as scattered dots with the body visible between them, so
        // it read as noise rather than as a measured surface. Doubling it
        // makes neighbouring cells just touch, which is what turns a point
        // cloud into a skin.
        gl_PointSize = uBase * aSize * (uViewH / -mv.z) * 0.030;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: `
      varying vec3 vColor;
      varying float vSize;
      uniform float uFade;
      void main() {
        // Round, soft-edged dots. A square point reads as a rendering
        // artefact; a disc reads as a measurement.
        vec2 d = gl_PointCoord - vec2(0.5);
        float r = length(d);
        if (r > 0.5) discard;
        float a = smoothstep(0.5, 0.15, r) * uFade;
        gl_FragColor = vec4(vColor, a * (0.72 + 0.28 * vSize));
      }`,
    vertexColors: true,
  });

  const pts = new THREE.Points(geo, mat);
  pts.frustumCulled = false;
  pts.visible = false;
  // DRAW IT OVER THE CARTOON, NOT INSIDE IT. The cloud sits on the body's
  // own surface, which is exactly where the character's opaque mesh is, so
  // with normal depth testing every point is occluded by the body it
  // describes. Measured: fade 1, visible true, 1792 points in frustum, and a
  // screenshot with nothing on it.
  //
  // depthTest off plus a high renderOrder makes it an overlay -- the reading
  // is ABOUT the person, so it belongs on top of them, the way a surgical
  // projection or a HUD reticle does.
  mat.depthTest = false;
  pts.renderOrder = 999;
  scene.add(pts);

  let raf = null;
  function fadeTo(target, ms = 600) {
    if (raf) cancelAnimationFrame(raf);
    const from = mat.uniforms.uFade.value;
    const t0 = performance.now();
    pts.visible = true;
    const step = () => {
      const t = Math.min(1, (performance.now() - t0) / ms);
      // ease-out so it arrives softly rather than snapping on
      mat.uniforms.uFade.value = from + (target - from) * (1 - Math.pow(1 - t, 3));
      if (t < 1) { raf = requestAnimationFrame(step); }
      else { raf = null; if (target === 0) pts.visible = false; }
    };
    step();
  }

  return {
    points: pts,
    stats: data.stats,
    // The raw bake, so coverage.js can order each arm's cells by distance
    // from that arm's real base rather than re-fetching the file.
    data,
    /** Free the GPU buffers. scene.remove() only unparents -- the geometry
     *  and the compiled shader stay resident, so swapping bodies repeatedly
     *  leaked one of each per press. */
    dispose() {
      if (raf) cancelAnimationFrame(raf);
      geo.dispose();
      mat.dispose();
    },
    armCount: data.arms.length,
    show: (ms) => fadeTo(1, ms),
    hide: (ms) => fadeTo(0, ms),
    /** Follow a live body. The brainstorm's stated innovation is "arms adapt
     *  dynamically to movement -- if the person moves their leg, the arm
     *  adjusts to the new position", and nothing on screen showed it.
     *
     *  The cartoon's bones already track a real person through MediaPipe, so
     *  the honest version is to carry the measured cells with those bones:
     *  when the volunteer moves, the machine's model of them moves, and the
     *  territories move with it. That is the claim, happening.
     *
     *  Cheap on purpose. Every cell is already parented to the cloud, so a
     *  whole-body follow is one transform rather than 5824 of them. Per-limb
     *  deformation would need the region labels the export now carries and is
     *  the obvious next step, but a demo needs the motion to be legible more
     *  than it needs it to be per-joint.
     */
    follow(node) {
      if (!node || !node.torso) return;
      const t = node.torso;
      // Lean the whole reconstruction with the torso, damped, so it reads as
      // the scan tracking a person rather than as a jitter.
      pts.rotation.z = t.rotation.z * 0.65;
      pts.rotation.x = t.rotation.x * 0.65;
    },

    /** Light only one arm's territory, dimming the rest. Used when a single
     *  arm is working so the audience can see WHOSE patch it is on.
     *
     *  IT MUST NOT UNCLEAN A CLEANED CELL. This function repaints every cell
     *  from the arm colours, and the coverage sweep writes the SAME buffer to
     *  mark a cell scrubbed -- so with both running, whichever wrote last won.
     *  Measured per animation frame: the body climbed to 265 cells and snapped
     *  to 0 three times in one scrub, once per splotch pop, because each pop
     *  changes the arms' phase and the phase change calls focus(null).
     *
     *  The fix is one owner per QUESTION rather than one owner per buffer:
     *  coverage decides whether a cell is clean, focus decides how brightly
     *  its region is lit. `isClean` is the hook the sweep installs so this
     *  function can ask instead of overwrite.
     */
    focus(armIndex) {
      const col = geo.getAttribute('color');
      for (let i = 0; i < n; i++) {
        // Cleaned cells keep the sweep's colour. Dimming still applies, so a
        // cleaned cell in an unfocused region reads as cleaned-and-not-active
        // rather than jumping back to dirty.
        const a = owner[i];
        const on = armIndex === null || a === armIndex;
        // 0.38, NOT 0.12. Measured against the floor the scan sits on: at
        // 0.12 a dimmed cell lands at 0.08 luminance versus the floor's 0.21,
        // so it is DARKER than the background and the whole unfocused body
        // reads as a black cutout rather than as dim data. A screenshot of
        // feed mode showed exactly that once the camera moved closer.
        //
        // 0.38 puts it at 0.27, just above the floor, while the focused arm
        // stays 2.6x brighter -- which is the job, since focus exists to say
        // whose patch is being worked, not to hide the rest of the person.
        const k = on ? 1.0 : 0.38;
        if (isClean && isClean(i)) {
          // A CLEANED CELL DIMS LESS. At the same 0.12 the dirty cells use, a
          // cleaned cell lands on rgb(0.10, 0.11, 0.12) -- measured against
          // each arm's dimmed colour, that is 0.011 to 0.036 of luminance
          // apart, which is nothing on a projector. Every bit of the cleaning
          // an audience had just watched vanished the moment feed mode focused
          // one arm.
          //
          // 0.40 keeps it 0.272 above a dimmed dirty cell, so the progress
          // stays readable, and 0.559 below a lit one, so the working arm's
          // region still clearly wins the eye.
          const ck = on ? 1.0 : 0.40;
          col.setXYZ(i, CLEAN_RGB[0] * ck, CLEAN_RGB[1] * ck, CLEAN_RGB[2] * ck);
          continue;
        }
        // UNREACHABLE CELLS ARE NOT DIMMED BY A FOCUS. They belong to no arm
        // (owner -1), so `on` was never true for them and all 5125 of them --
        // 88% of the body, the whole torso and head -- went dark every time
        // one arm took focus. A screenshot of feed mode showed the person as a
        // black cutout with two coloured arms floating on it.
        //
        // Focus answers "whose patch is being worked". A cell nobody can reach
        // is not an answer to that question, so it keeps its own dim slate and
        // the body keeps its silhouette.
        if (a < 0) {
          col.setXYZ(i, UNREACHED.r, UNREACHED.g, UNREACHED.b);
          continue;
        }
        const c = ARM_COLOURS[a % 4];
        col.setXYZ(i, c.r * k, c.g * k, c.b * k);
      }
      col.needsUpdate = true;
    },

    /** The coverage sweep installs its own "has this cell been scrubbed"
     *  predicate here, so focus() can preserve what it painted. Kept as a
     *  setter rather than a constructor argument because coverage is built
     *  AFTER territories -- it needs the point cloud that territories owns. */
    setCleanTest(fn) { isClean = typeof fn === 'function' ? fn : null; },
  };
}
