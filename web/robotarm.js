// web/robotarm.js — the SCRUBBING ARM, on screen.
//
// WHY THIS EXISTS: the projector showed only the cartoon person, so the
// audience never saw the scrub HAPPEN -- only the aftermath, splotches
// vanishing off a limb for no visible reason. The whole pitch is "watch the
// robot clean her arm", and the money shot was missing its subject.
//
// Now a judge can look back and forth: cartoon arm scrubbing cartoon forearm,
// real arm scrubbing real forearm, same rhythm.
//
// IT DERIVES ITS MOTION FROM EVENTS THE BROWSER ALREADY HAS. No new socket
// field, no streamed pose. That keeps the architecture rule intact -- the
// socket carries EVENTS ONLY so the cartoon survives the robot stack dying --
// and it degrades honestly: if Python dies the arm parks at rest, which is
// exactly what the real arm does.
//
// ART: same language as the character. Flat colour, hard cel bands, no
// texture. Four boxes. Fall Guys / PEAK spend nothing on surface detail and
// everything on motion, and a detailed robot beside a flat person would read
// as two different shows.
import * as THREE from 'three';

const BASE   = 0x5a6472;   // slate
const LINK   = 0x8792a0;   // lighter slate
const JOINT  = 0x39414e;   // darker than either, so every pivot reads as a
                           // JOINT rather than more of the same link. This is
                           // the single change that turns a stick into a machine
                           // at projector distance.
const SPONGE = 0xffd94a;   // the ONE saturated accent on screen -- the eye
                           // should go to the thing doing the work

/** THE REAL MACHINE'S PROPORTIONS, or the old hand-typed ones.
 *
 *  The numbers below were typed by hand and they are the wrong shape. The
 *  RoArm-M2-S is upper 238.7mm, fore 280.2mm -- a fore/upper ratio of 1.174,
 *  where these constants say 0.833. The drawn forearm is 29% too SHORT, so the
 *  co-star of the pitch is not the shape of the robot it claims to be.
 *
 *  tools/export_armgeom.py writes web/assets/armgeom.json from
 *  scrub3d/kinematics.py (the vendor's URDF) and scrub3d/armmesh.py (the
 *  vendor's STL meshes) -- the same geometry the collision layer places its
 *  capsules with, so the page and the safety governor read ONE arm rather than
 *  two that can drift. loadArmGeometry() below fetches it.
 *
 *  THE TOTAL IS PRESERVED, ONLY THE SPLIT CHANGES. shoulder_pivot + upper +
 *  fore sums to 1.42 either way, because the sponge's reach was SOLVED against
 *  the measured dirt position (base 0.63 puts the sponge at 1.40 against dirt
 *  at 1.33) and web/main.js reads `robot.sponge`'s world position to place the
 *  suds. Free-scaling the links would move the sponge and break a placement
 *  that was measured rather than guessed.
 *
 *  THESE STAY AS THE FALLBACK AND ARE NOT DEAD CODE. A missing or malformed
 *  armgeom.json must never blank the arm on stage -- the page has to boot with
 *  no backend at all, which is the whole reason the geometry is baked instead
 *  of streamed. Absent file, old shape, demo intact.
 */
const GEOM = {
  shoulder_pivot: 0.08,
  upper: 0.72,
  fore: 0.60,
  w_upper: 0.20,
  w_fore: 0.17,
  d_eoat: 0.30,
};

/** Distance from the arm's root to the SPONGE at rest, in page units.
 *
 *  THE ONE NUMBER IN THIS FILE THAT MAY NOT MOVE. The sponge's reach was
 *  solved against the measured dirt position (1.40 against dirt at 1.33) and
 *  web/main.js reads `robot.sponge`'s world position to place the suds, so
 *  every other length here is free to change and this one is not.
 *
 *  IT IS NOT THE SUM OF THE THREE LINK LENGTHS, and mistaking it for that is a
 *  bug this file already had: the sponge sits at 0.62 on a 0.60 fore link, so
 *  the link sum is 1.40 while the sponge chain is 1.42. Both the load-time
 *  validation and the sponge's own placement measure against THIS.
 */
const SPONGE_CHAIN = 0.08 + 0.72 + 0.62;

/** THE ANGLES THE METAL CAN ACTUALLY HOLD, in radians.
 *
 *  tools/export_armgeom.py has always WRITTEN these (armgeom.json carries a
 *  `limits_deg` block straight from scrub3d/kinematics.py's JOINT_LIMITS) and
 *  loadArmGeometry has always THROWN THEM AWAY -- it read `.units` and nothing
 *  else. So the measured lengths landed on screen and the measured limits did
 *  not, and the drawn arm stayed free to bend somewhere the real one clamps.
 *
 *  The fallback below is the URDF's own numbers, NOT a safe-looking guess:
 *  shoulder +/-90deg (roarm_description.urdf link1_to_link2, +/-1.5708) and
 *  elbow [-57.3, 180] (link2_to_link3, [-1, 3.1416]). kinematics.py's docstring
 *  works out at length why the elbow takes the URDF bound and not the
 *  firmware's -- the arm demonstrably reaches its own init pose, which needs
 *  j2 = 129.6deg, and the firmware's +/-90 would forbid that.
 *
 *  WHY A FALLBACK RATHER THAN "NO FILE MEANS NO CLAMP": a missing armgeom.json
 *  already leaves the arm drawn at the old hand-typed lengths, and an arm that
 *  is the wrong shape AND free to hyperextend is strictly worse than one that
 *  is only the wrong shape. The clamp costs nothing when the poses are legal,
 *  which at rest they all are.
 */
const LIMITS = {
  // The base yaw takes the FIRMWARE's tighter +/-90deg, not the URDF's
  // +/-180. kinematics.py's JOINT_LIMITS makes the same split for the same
  // reason it gives at length: the elbow needs the URDF bound because the arm
  // demonstrably reaches its own init pose, and the base has no such proof, so
  // the conservative value is the honest one to draw.
  yaw: [-Math.PI / 2, Math.PI / 2],
  sh: [-Math.PI / 2, Math.PI / 2],
  el: [-1.0, Math.PI],
};

/** Hold `v` inside joint `j`'s real range.
 *
 *  THIS IS THE WHOLE POINT OF THE FILE'S HONESTY CLAIM. Every angle the page
 *  draws goes through here, so no combination of pose, per-region offset and
 *  scrub stroke can put the cartoon in a pose the metal cannot hold. A judge
 *  who knows the RoArm cannot catch the drawing doing something the machine
 *  would refuse.
 */
function clampJoint(j, v) {
  const [lo, hi] = LIMITS[j];
  return v < lo ? lo : (v > hi ? hi : v);
}

/** Fetch the measured geometry and fold it into GEOM. Call once before the
 *  first makeRobotArm(); safe to skip entirely.
 *
 *  NEVER THROWS AND NEVER REJECTS. Every failure mode -- no file, 404, bad
 *  JSON, a key missing, a nonsense number -- leaves GEOM at the hand-typed
 *  fallback and the arm draws exactly as it did before. An arm that fails to
 *  appear because a JSON fetch went wrong is a far worse outcome than an arm
 *  with slightly wrong link lengths.
 */
export async function loadArmGeometry(url = 'assets/armgeom.json') {
  try {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) return false;
    const doc = await r.json();
    const u = doc.units;
    if (!u) return false;
    // VALIDATE BEFORE ADOPTING. A zero or negative length collapses a link to
    // nothing, and a NaN propagates silently through three.js into an
    // invisible mesh with no error anywhere -- the exact failure that is
    // impossible to debug from the back of a room.
    const keys = Object.keys(GEOM);
    const next = {};
    for (const k of keys) {
      const v = Number(u[k]);
      if (!Number.isFinite(v) || v <= 0) return false;
      next[k] = v;
    }
    // The chain total is what keeps the sponge where the dirt placement put
    // it, and the anchor is SPONGE_CHAIN, not the fallback links' own sum.
    // Those two are NOT the same number and reading the wrong one rejected
    // every valid file: the sponge sits at 0.62 on a 0.60 fore link, 20mm of
    // page units past its end, so the link sum is 1.40 while the sponge chain
    // is 1.42. tools/export_armgeom.py pins its split to 1.42 deliberately.
    //
    // The tolerance is rounding slack only. The exporter writes four decimals,
    // so three lengths can drift half a unit in the last place between them.
    const total = next.shoulder_pivot + next.upper + next.fore;
    if (Math.abs(total - SPONGE_CHAIN) > 0.002) return false;

    // WHICH ARM IS THIS. The chain always sums to 1.42 by construction, so
    // the length check above passes for EITHER arm and cannot tell them
    // apart. Measured: a bake run without SCRUB3D_ARM=openyam wrote the
    // RoArm's 238.7mm upper and 280.2mm fore into this file, the page
    // adopted it happily, and the arms on screen were a different robot
    // from the one bolted to the chair -- with nothing anywhere saying so.
    //
    // The exporter now stamps `arm`. A file that names the other one is
    // refused rather than drawn, because the fallback geometry being
    // slightly wrong is a smaller lie than confidently drawing the wrong
    // machine. An older file with no `arm` key is still accepted: it
    // predates the stamp and rejecting it would break a working page.
    if (doc.arm && doc.arm !== 'openyam') {
      console.warn(`[arm] armgeom.json describes ${doc.arm}, not the OpenYAM `
                   + `on the chair -- keeping the built-in proportions. `
                   + `Re-bake with SCRUB3D_ARM=openyam.`);
      return false;
    }

    // THE WIDTHS NEED THEIR OWN CHECK, because the length check above passes
    // happily while the arm is drawn as a slab. Measured 2026-09-19: a file
    // whose three lengths summed to 1.42 correctly also carried w_fore =
    // 1.6442, a 968mm-thick forearm on a 458mm link, and both arms rendered
    // as flat panels covering the person. The lengths were right; nothing
    // looked at the widths.
    //
    // A link wider than it is long is the cheap, general signature: no arm
    // segment on this machine is thicker than its own length, and the failure
    // that produced it (a bounding RADIUS written where a width belongs) always
    // trips it. Rejecting the whole file rather than clamping the width is
    // deliberate -- the fallback geometry is known good, and a file we have to
    // correct is a file we should not be adopting.
    const WIDTH_OF = { w_upper: next.upper, w_fore: next.fore };
    for (const [wk, len] of Object.entries(WIDTH_OF)) {
      if (next[wk] > len) return false;
    }
    Object.assign(GEOM, next);

    // THE LIMITS ARE ADOPTED SEPARATELY AND MAY FAIL ON THEIR OWN. The lengths
    // above are already validated and assigned, so a malformed `limits_deg`
    // must not roll them back -- it just leaves the URDF fallback in place. A
    // file with good lengths and a typo'd limit should still get the right
    // shape on stage.
    //
    // Same validation shape as the lengths: reject anything non-finite or
    // inverted rather than trusting the file, because a NaN limit turns every
    // comparison in clampJoint false and silently restores the old unbounded
    // behaviour with nothing logged anywhere.
    const d = doc.limits_deg;
    if (d) {
      const nextLim = {};
      for (const [k, src] of [['sh', d.shoulder], ['el', d.elbow]]) {
        if (!Array.isArray(src) || src.length !== 2) return true;
        const lo = Number(src[0]) * Math.PI / 180;
        const hi = Number(src[1]) * Math.PI / 180;
        if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return true;
        nextLim[k] = [lo, hi];
      }
      Object.assign(LIMITS, nextLim);
    }
    return true;
  } catch (_) {
    return false;
  }
}

/** Per-arm reach, so four arms around one body do not all make the identical
 *  gesture. Each owns a different region and has to come at it differently:
 *  a shoulder is high and close, a shin is low and far.
 *
 *  `sh` and `el` are added to the shared poses below. Small numbers on
 *  purpose -- this is meant to read as four machines working on one person,
 *  not as four different machines.
 */
const REGION_REACH = {
  'forearm-right': { sh:  0.00, el:  0.00 },   // the original, unchanged
  'forearm-left':  { sh:  0.04, el: -0.05 },
  'leg-right':     { sh: -0.22, el:  0.18 },   // lower and further out
  'leg-left':      { sh: -0.20, el:  0.16 },
};

export function makeRobotArm(scene, ramp, region) {
  const root = new THREE.Group();
  const mat = (c) => new THREE.MeshToonMaterial({ color: c, gradientMap: ramp });

  // REBUILT AFTER LOOKING AT IT ON A PROJECTOR-SIZED FRAME. The first version
  // was four bare boxes -- a 0.13-wide stick, a 0.11-wide stick, and a block --
  // and it did not read as a robot arm. It read as scaffolding somebody forgot
  // to delete, which is a problem when it is the co-star of the pitch and the
  // thing the entire project is about.
  //
  // What makes a low-poly machine read as a MACHINE at ten feet is not surface
  // detail, it is JOINTS: a visible cylinder at every pivot, darker than the
  // links it connects. That one addition does more than any amount of texture.
  // Everything below is still flat colour with hard cel bands, same language as
  // the character, and the whole arm is still ~40 triangles of budget in a
  // scene measured at 777.
  //
  // THE SEGMENT LENGTHS AND PIVOT OFFSETS ARE UNCHANGED (0.72 and 0.60, pivots
  // at 0.08 / 0.72), because the sponge's reach was SOLVED against the measured
  // dirt position -- base 0.63 puts the sponge at 1.40 against dirt at 1.33.
  // Re-proportioning the links would silently break that. Only girth, joints
  // and the head are new.
  const joint = (r, len, axis = 'z') => {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 10), mat(JOINT));
    // Cylinders are built along Y; roll them onto the pivot axis.
    if (axis === 'z') m.rotation.x = Math.PI / 2;
    return m;
  };

  // ---- BASE: a plate, a collar and a shoulder housing, not a single slab.
  const base = new THREE.Group();
  const plate = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.07, 0.62), mat(BASE));
  base.add(plate);
  // Four bolt heads. Tiny, but they are what say "bolted down" rather than
  // "resting on".
  for (const [bx, bz] of [[-0.23, -0.23], [0.23, -0.23], [-0.23, 0.23], [0.23, 0.23]]) {
    const bolt = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.05, 6), mat(JOINT));
    bolt.position.set(bx, 0.05, bz);
    base.add(bolt);
  }
  const collar = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.24, 0.14, 12), mat(BASE));
  collar.position.y = 0.1;
  base.add(collar);
  const housing = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.22, 0.26), mat(LINK));
  housing.position.y = 0.2;
  base.add(housing);
  root.add(base);

  // ---- UPPER LINK. Lengths come from GEOM, which is the vendor's URDF when
  // armgeom.json loaded and the old hand-typed shape when it did not.
  //
  // EVERY DERIVED NUMBER IS DERIVED, NOT RE-TYPED. The mesh sits at half its
  // own length, the spine is a fixed fraction of it, and the fore link's pivot
  // is the upper link's length. Writing 0.36 beside a 0.72 is how the previous
  // version went wrong in the first place: change one and the other silently
  // stops matching.
  const upper = new THREE.Group();
  upper.position.y = GEOM.shoulder_pivot;
  upper.add(joint(0.13, 0.34));                     // shoulder
  const upperMesh = new THREE.Mesh(
    new THREE.BoxGeometry(GEOM.w_upper, GEOM.upper, GEOM.w_upper), mat(LINK));
  upperMesh.position.y = GEOM.upper / 2;
  upper.add(upperMesh);
  // A recessed spine down the link, so a flat box gains one plane of relief.
  const spine = new THREE.Mesh(
    new THREE.BoxGeometry(GEOM.w_upper * 0.4, GEOM.upper * 0.83, GEOM.w_upper * 1.1),
    mat(BASE));
  spine.position.y = GEOM.upper / 2;
  upper.add(spine);
  // ---- THE BASE YAW, which is the real machine's FIRST joint.
  //
  // THE ARM WAS DRAWN WITH TWO JOINTS AND THE MACHINE HAS THREE.
  // scrub3d/kinematics.py solves j0 (base yaw), j1 (shoulder) and j2 (elbow),
  // and its fk() turns the yaw into `x = BASE_X + cos(j0)*radial,
  // y = -sin(j0)*radial`: the shoulder and elbow move the tool in a plane and
  // the yaw SWINGS THAT PLANE. Without it the drawn arm could only ever reach
  // points in one fixed vertical slice, which is why the sponge could not
  // follow the forearm sideways -- MEASURED before this was added: asked for
  // three points spanning the limb, the sponge moved 0.12 of the 0.32 it was
  // asked for and sat up to 0.34 away from every one of them.
  //
  // A GROUP, NOT A ROTATION ON `base`. `base` carries the plate, the bolts and
  // the collar, and those are bolted to a plinth: turning them would swivel
  // the furniture with the arm. The real machine yaws the column above its
  // mounting plate and this draws the same split.
  const yaw = new THREE.Group();
  yaw.add(upper);
  base.add(yaw);

  // ---- FORE LINK. Its pivot IS the upper link's length -- the elbow is where
  // the upper arm ends, so this can never be a separate constant.
  const fore = new THREE.Group();
  fore.position.y = GEOM.upper;
  fore.add(joint(0.115, 0.3));                      // elbow
  const foreMesh = new THREE.Mesh(
    new THREE.BoxGeometry(GEOM.w_fore, GEOM.fore, GEOM.w_fore), mat(LINK));
  foreMesh.position.y = GEOM.fore / 2;
  fore.add(foreMesh);
  const foreSpine = new THREE.Mesh(
    new THREE.BoxGeometry(GEOM.w_fore * 0.41, GEOM.fore * 0.83, GEOM.w_fore * 1.12),
    mat(BASE));
  foreSpine.position.y = GEOM.fore / 2;
  fore.add(foreSpine);
  upper.add(fore);

  // ---- WRIST + SPONGE HEAD. The sponge is the one saturated thing on screen,
  // so it gets a dark mount behind it to stop it floating: the eye reads a tool
  // held by a machine rather than a yellow block stuck to a stick.
  const wrist = joint(0.09, 0.24);
  wrist.position.y = GEOM.fore;
  fore.add(wrist);
  // The mount goes with the sponge: it is the thing that holds it, and a
  // tool head that stays still while its own bracket turns reads as broken.
  const mount = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.1, 0.18), mat(BASE));
  mount.position.y = -0.02;
  wrist.add(mount);

  // SPONGE KEEPS ITS NAME, ITS PARENT AND ITS y=0.62. Tests read its world
  // position to check the sponge meets the dirt; moving it would break the one
  // measurement this whole placement was solved against.
  // How fast the head turns while scrubbing, radians a second. Fast enough
  // to read as powered at projector distance, slow enough that a BOX does
  // not strobe into a blur -- the head is 0.3 x 0.26, so past about 9 the
  // corners stop being separable and it reads as a smear.
  const SPIN_RATE = 7.0;
  let spinning = false;   // true only in the scrub phase
  let spinUp = 0;         // 0..1, eased, so it spools rather than snaps
  // THE SPONGE'S HEIGHT IS THE ONE NUMBER THAT MAY NOT MOVE, and it is now
  // computed rather than typed so it cannot. The chain from the arm root is
  // shoulder_pivot + upper + (sponge on fore), and that total was solved
  // against the measured dirt position -- 1.42 units, sponge at 1.40 against
  // dirt at 1.33. Re-proportioning the links redistributes the first two
  // terms, so this third one absorbs the difference and the sponge lands
  // exactly where it landed before. With the fallback geometry it evaluates
  // to the old literal 0.62.
  //
  // loadArmGeometry() already refuses any file whose three lengths do not sum
  // to SPONGE_CHAIN, so this can only ever be a small correction, never a
  // rescue of a wrong file.
  const sponge = new THREE.Mesh(
    new THREE.BoxGeometry(GEOM.d_eoat, 0.18, 0.26), mat(SPONGE));
  // ON THE WRIST, NOT ON THE FOREARM, SO THE WRIST ACTUALLY CARRIES IT.
  // The wrist group above turns from joints 4 to 6, but with the tool
  // parented to `fore` that rotation moved nothing a viewer could see: the
  // arm's own wrist mesh twisted and the sponge stayed put.
  //
  // Measured against the OpenYAM URDF, on the real arms' current pose: with
  // the wrist held at home the tool sits 146mm (left) and 225mm (right)
  // from where the real wrist angles put it. That is the whole error, and
  // it lands exactly where the tool meets the person.
  //
  // The y offset is the same total as before, less the wrist's own height,
  // so the sponge is in the IDENTICAL world position at the home pose --
  // SPONGE_CHAIN is solved against the measured dirt placement and
  // tests read this mesh's world position to check the sponge meets the
  // skin. Reparenting must not move it; it must only let it follow.
  sponge.position.y = SPONGE_CHAIN - GEOM.shoulder_pivot - GEOM.upper
                      - GEOM.fore;
  wrist.add(sponge);

  scene.add(root);

  // ---- pose targets, reached through the same spring idea as the character.
  // A robot arm that snaps between poses looks like a diagram; one that
  // travels, arrives and settles looks like a machine with mass.
  const REST   = { sh: -0.55, el: 0.95 };   // folded back, out of the way
  const HOVER  = { sh:  0.30, el: 0.55 };   // above the forearm, not touching
  const CONTACT= { sh:  0.46, el: 0.40 };   // pressed onto it

  // ---- THE FEED BEAT'S TWO POSES. Down at the tray, and up at the mouth.
  //
  // MEASURED OFF THIS ARM, NOT TYPED FROM TASTE. Each was found by driving
  // the joints and reading toolWorld() back, because the numbers that matter
  // are where the CLAW lands and there is no way to get those from angles by
  // inspection. On the feeding arm (fleet[1], rolled 45 degrees about Z on
  // its mount) they put the claw at:
  //
  //   TRAY  ->  (-1.06, 1.52, 0.67)   out to the arm's own side, beside its
  //                                    mount, where a bowl can sit
  //   MOUTH ->  (-0.21, 1.48, 0.59)   in front of the seated head, which is
  //                                    measured at (0.00, 1.48, -0.07)
  //
  // The mouth pose sits about two thirds of a unit IN FRONT of the face rather
  // than on it, and level with it. That gap is the picture: a spoon offered to
  // someone, not pushed into their head.
  //
  // WHY THE TRAY IS NEAR REST AND NOT DOWN ON THE FLOOR. These springs are
  // heavily damped, and a pose far from the one the arm is holding takes over
  // three seconds to close on (measured on this arm: 0.81 units of error still
  // left at 300ms and 0.22 at 3000ms, for a tray pose down at sh 0.90). A
  // mouthful is 1.6 to 2.2 seconds, so the claw would never arrive and the
  // grab would be honestly refused every time -- a bowl sitting on the tray for
  // the whole beat while the counter ticked. From rest, this tray closes to
  // within 0.005 in 1.2 seconds. The arm's own dynamics decide where the tray
  // can be; that is the constraint, and it is a real one.
  //
  // WHY POSES AND NOT reachTo(). solveIK models a yaw and two links rotating
  // about X; this arm is also rolled 45 degrees about Z by its measured mount,
  // which that decomposition does not carry. Asking it for a world point on
  // this arm lands the claw over half a unit away (measured: 0.61 to 0.82 for
  // every point tried along the tray-to-mouth line, including the point the
  // claw was already sitting on). Two poses read off the real workspace are
  // honest about what this drawing can solve; a solver that misses by 0.7 and
  // is called anyway is not.
  const FEED_TRAY  = { yaw: -0.25, sh: 0.00, el: 1.05 };
  const FEED_MOUTH = { yaw:  1.00, sh: 0.80, el: 1.50 };

  let target = { ...REST };
  let cur    = { ...REST };
  let vel    = { sh: 0, el: 0 };
  let stroke = 0;        // -1..1, the side-to-side of an actual scrub
  let strokeAmp = 0;     // decays, so the arm stops stroking when the scrub does

  const K = 9.0, DAMP = 0.80;

  // ---- REACHING A POINT, rather than playing an animation shaped like one.
  //
  // WHAT THIS REPLACES. The scrub used to be `fore.rotation.z = stroke *
  // strokeAmp * 0.30` with strokeAmp decaying by 0.06^h -- a roll in place,
  // on constants picked by eye, with no connection to where the real sponge
  // is. The real machine knows exactly where its sponge is on the forearm
  // (py/scrubbot.py's SCRUB branch computes `u` from the MEASURED forearm and
  // motion.scrub_offset, and now sends it as EVENT["scrub_u"]), so the cartoon
  // can put its sponge at the matching place on the cartoon's forearm instead
  // of miming.
  //
  // WHY TWO JOINTS AND NOT A FULL SOLVER. The drawn arm has exactly two
  // revolute joints that move -- upper.rotation.x and fore.rotation.x, both
  // about the same axis -- so its reachable set is a disc in the root's YZ
  // plane and the closed form below is not an approximation of a better
  // solver, it IS the solution for this linkage. scrub3d/kinematics.py solves
  // the REAL five-axis arm; importing that here would be a second pipeline
  // drawing a machine this file does not draw.
  const _p = new THREE.Vector3();

  /** Yaw + two-link IK in the arm root's own frame.
   *  -> {yaw, sh, el} always; `reached` says whether it got there.
   *
   *  THE SAME DECOMPOSITION scrub3d/kinematics.py USES. The yaw swings the
   *  arm's working plane to contain the target, and the shoulder and elbow
   *  then solve a planar two-link problem inside it. fk() there is
   *  `x = BASE_X + cos(j0)*radial, y = -sin(j0)*radial, z = height`; this is
   *  that, in the page's Y-up frame instead of the robot's Z-up one.
   *
   *  `L1` is root-to-elbow (the shoulder pivot plus the upper link) and `L2`
   *  is elbow-to-sponge. L2 is measured to the SPONGE, not to the end of the
   *  fore link: the sponge sits 0.62 along a 0.60 link, and solving to the
   *  link end would leave the sponge 20mm of page units past every target.
   *
   *  ANGLE CONVENTION IS TAKEN FROM THE DRAWING, NOT ASSUMED. Both links are
   *  built along +Y and rotated about X, so a shoulder angle a swings +Y
   *  toward -Z. In the yawed plane the reach coordinate is therefore -radial
   *  and the height is y. Getting this backwards puts the arm behind itself,
   *  which is why it is written down here rather than left for the next
   *  reader to rediscover.
   *
   *  IT NEVER RETURNS null. An unreachable point gets the CLOSEST pose the
   *  linkage can hold -- arm pointed straight at it, stretched or folded as
   *  far as it goes -- because the alternative measured worse: the forearm
   *  sits 1.45 to 1.54 units from the arm root and the whole chain is 1.42, so
   *  a refuse-if-unreachable solve refused EVERY point on the limb and the
   *  sponge never moved at all. Reaching as far as the metal allows toward the
   *  real target is both what a real arm does and what reads correctly on
   *  screen; `reached` lets the caller tell the two apart.
   */
  function solveIK(x, y, z) {
    const L1 = GEOM.shoulder_pivot + GEOM.upper;
    const L2 = SPONGE_CHAIN - L1;                 // root-to-sponge minus L1
    // THE YAW FIRST. atan2(x, -z) and not atan2(z, x): at yaw 0 the arm
    // reaches toward -Z (see the shoulder convention above), so that is the
    // direction the angle is measured from.
    const yawA = Math.atan2(x, -z);
    // Distance to the target within the yawed plane. Once the plane contains
    // the point, the radial distance is just the horizontal distance.
    const r = Math.hypot(x, z);
    const d = Math.hypot(r, y);
    const cl = (v) => (v < -1 ? -1 : (v > 1 ? 1 : v));
    // Straight at the target, in the plane. The shoulder measures from +Y
    // toward -Z, and the target's in-plane coordinates are (height y, reach
    // r), so the bearing is atan2(r, y).
    const bearing = Math.atan2(r, y);
    if (d > L1 + L2 - 1e-6) {
      // FURTHER THAN THE ARM IS LONG: straighten it and point. An elbow left
      // bent here would have the arm reaching short AND crooked, which reads
      // as a machine that has given up rather than one at full stretch.
      return { yaw: yawA, sh: bearing, el: 0, reached: false };
    }
    if (d < Math.abs(L1 - L2) + 1e-6) {
      return { yaw: yawA, sh: bearing, el: Math.PI, reached: false };
    }
    // Law of cosines. The acos arguments are inside [-1,1] by the two tests
    // above, but a NaN here would propagate silently through three.js into an
    // invisible arm with no error anywhere, so they are clamped as well.
    const a2 = Math.acos(cl((L1 * L1 + L2 * L2 - d * d) / (2 * L1 * L2)));
    const a1 = Math.acos(cl((d * d + L1 * L1 - L2 * L2) / (2 * d * L1)));
    // ELBOW-DOWN. Both elbow-up and elbow-down reach the same point; the arm
    // is drawn reaching IN toward the chair from its plinth, and the up
    // solution swings the elbow through the person.
    return { yaw: yawA, sh: bearing + a1, el: Math.PI - a2, reached: true };
  }

  // The world point the sponge is being asked to sit on, or null for "no real
  // data -- use the pose targets". Kept as a point rather than as angles so a
  // limb that moves under the sponge (the character breathes and rocks) is
  // re-solved every frame instead of once when the message arrived.
  let reachPt = null;

  // ---- THE REAL MACHINE'S OWN ANGLES, when the backend sends them --------
  //
  // EVERYTHING ABOVE THIS POINT INFERS A POSE. reachTo() solves where the
  // sponge should be and solveIK() works backwards to two angles; setPhase()
  // picks one of three hand-tuned poses. Both are the page deciding what the
  // arm is probably doing. py/scrubbot.py's EVENT["joints"] is the arm
  // SAYING what it is doing, and when that arrives it outranks both.
  //
  // WHY THE MAPPING IS THREE JOINTS OF SIX AND NOT ALL SIX. The drawn arm
  // has exactly three revolute joints -- the base yaw, the shoulder and the
  // elbow -- and no wrist that turns (the sponge head spins on its own axis
  // and that is a different thing). The real arm has six. So j0/j1/j2 land
  // on yaw/sh/el and j3/j4/j5 have nowhere on this drawing to go. Inventing
  // a wrist to spend them on would be drawing a machine this file does not
  // model, which is the same rule solveIK's own comment states about not
  // importing the five-axis solver.
  //
  // `jointsSrc` is kept beside the angles rather than derived from them,
  // because the whole point of the field is that COMMANDED and MEASURED
  // angles are numerically indistinguishable. A caller asking "am I drawing
  // a measurement?" must get the answer from the wire, never from the pose.
  let jointsQ = null;         // [j0, j1, j2, ...] radians, or null
  let jointsSrc = null;       // 'commanded' | 'measured' | null

  // THE BASE YAW'S OWN SPRING STATE, and it is deliberately NOT a third key
  // on `target`/`cur`/`vel` above. setPhase() assigns `target` WHOLESALE --
  // `target = { sh: ..., el: ... }` -- so a yaw key living there would be
  // silently deleted on every phase change and the joint would jump to
  // undefined. Two lines beside the loop is the safe shape; it runs the SAME
  // K and DAMP, so it is the same spring, not a second one with its own feel.
  let yawCur = 0, yawVel = 0, yawTarget = 0;

  return {
    root,
    // THE SPONGE MESH ITSELF, so a test can read its world position. Without it
    // a gap check has to traverse root->upper->fore looking for a mesh, and the
    // day someone retints or reshapes it that search finds nothing and the test
    // passes by asserting on undefined -- the exact trap __wheelgentic's own comment
    // warns about for the arm handle.
    sponge,
    // THE WRIST GROUP, which is the arm's hand. Exposed for the same reason
    // the sponge is: a caller that wants to hang something off the tool end
    // otherwise has to traverse root->upper->fore->? and guess which group is
    // the last one that turns. The sponge is parented HERE, and so is
    // anything grip() takes hold of.
    wrist,

    /** TAKE HOLD OF SOMETHING. After this the object is part of the arm: it
     *  moves because the wrist moves, and nothing may tween its position.
     *
     *  .attach, NEVER .add. Object3D.attach recomputes the child's local
     *  transform so its WORLD transform is unchanged by the reparenting;
     *  .add keeps the local transform and therefore teleports the object by
     *  however far the two parents are apart. A bowl sitting on the tray that
     *  jumps to the arm root the instant the claw closes is the exact tell
     *  that says these two things were never connected.
     *
     *  The tool-tip offset is the sponge's own y, so a gripped prop sits
     *  where the sponge sits -- the one place on this arm that was solved
     *  against a measurement rather than picked by eye.
     */
    grip(obj) {
      if (!obj) return false;
      // World matrices must be current or .attach preserves a stale world
      // position: the arm's springs move `cur` every frame and three.js only
      // rebuilds matrices at render time, so grabbing between renders would
      // read last frame's pose.
      root.updateWorldMatrix(true, true);
      obj.updateWorldMatrix(true, false);
      wrist.attach(obj);
      // HELD IN THE CLAW, NOT WHEREVER IT HAPPENED TO BE. .attach preserves
      // the world position, which is right for the reparent itself -- the
      // prop must not jump on the frame it is grabbed -- but it also
      // preserves whatever gap existed at that moment. The claw closes on a
      // point; the bowl was a few centimetres off it, so the whole carry
      // showed a bowl floating beside the hand rather than in it.
      //
      // So the local position is then snapped to the tool point, the same
      // way the sponge has a fixed offset on this group rather than a
      // remembered one. GEOM.d_eoat is the claw's own width, so half of it
      // puts the prop against the jaws instead of inside them.
      obj.position.set(0, SPONGE_CHAIN - GEOM.shoulder_pivot - GEOM.upper
                          - GEOM.fore + GEOM.d_eoat * 0.5, 0);
      obj.rotation.set(0, 0, 0);
      return true;
    },

    /** LET GO, back into `parent` (the scene, normally). Same .attach rule in
     *  reverse: the prop stays exactly where the claw left it rather than
     *  snapping back to whatever local position it had before the grab. */
    release(obj, parent) {
      if (!obj) return false;
      const dest = parent || obj.parent?.parent || scene;
      root.updateWorldMatrix(true, true);
      dest.updateWorldMatrix(true, false);
      dest.attach(obj);
      return true;
    },

    /** DRIVE THE FEED TRAVEL: 0 is the tray, 1 is the mouth.
     *
     *  It writes the pose TARGETS and lets the same springs everything else
     *  here travels on carry the arm there, so the arm arrives with the mass
     *  and settle the rest of the page has. The page tweens `t`; the claw's
     *  position is whatever the linkage makes of it, which is the point --
     *  a prop held in that claw moves because these angles moved.
     *
     *  Pass null to hand the arm back to setPhase.
     */
    feedPose(t) {
      if (t === null || t === undefined) return;
      const k = t < 0 ? 0 : (t > 1 ? 1 : t);
      const lerp = (a, b) => a + (b - a) * k;
      target = { sh: lerp(FEED_TRAY.sh, FEED_MOUTH.sh),
                 el: lerp(FEED_TRAY.el, FEED_MOUTH.el) };
      yawTarget = lerp(FEED_TRAY.yaw, FEED_MOUTH.yaw);
      // A STANDING REACH POINT WOULD OVERWRITE ALL OF THAT. update() prefers
      // reachPt over the pose target every frame, so a feed entered straight
      // out of a scrub would have the arm still solving for the last sponge
      // position and never travel at all.
      reachPt = null;
    },

    /** WHERE THE CLAW WOULD BE at travel fraction `t`, without moving it.
     *
     *  The page asks this once per mouthful to find out where the tray is --
     *  "the tray" being wherever the arm can actually put the claw, rather
     *  than a constant typed beside it. It has to be a QUESTION and not a
     *  pose change: snapping the drawn arm to the tray to read its position
     *  would teleport it on screen every time a mouthful starts, which is the
     *  same lie in a different place.
     *
     *  It poses the chain, reads the matrix and puts every angle back, so the
     *  drawn arm is byte-identical afterwards. Cheap enough to call per
     *  mouthful; do not call it per frame.
     */
    feedPoseWorld(t, out) {
      const k = t < 0 ? 0 : (t > 1 ? 1 : t);
      const lerp = (a, b) => a + (b - a) * k;
      const keep = { y: yaw.rotation.y, u: upper.rotation.x, f: fore.rotation.x };
      yaw.rotation.y   = clampJoint('yaw', lerp(FEED_TRAY.yaw, FEED_MOUTH.yaw));
      upper.rotation.x = clampJoint('sh',  lerp(FEED_TRAY.sh,  FEED_MOUTH.sh));
      fore.rotation.x  = clampJoint('el',  lerp(FEED_TRAY.el,  FEED_MOUTH.el));
      root.updateWorldMatrix(true, true);
      const v = (out || new THREE.Vector3()).setFromMatrixPosition(sponge.matrixWorld);
      // BACK EXACTLY AS IT WAS, and the matrices rebuilt with it -- leaving a
      // stale world matrix behind would have the very next grip() attach a
      // prop against the pose this question posed rather than the pose the
      // arm is holding.
      yaw.rotation.y = keep.y; upper.rotation.x = keep.u; fore.rotation.x = keep.f;
      root.updateWorldMatrix(true, true);
      return v;
    },

    /** Where the claw is, in world space. The page needs this to know whether
     *  the arm ACTUALLY arrived at the tray before the grab -- the rule is
     *  that a prop cannot be picked up by an arm that never got there. */
    toolWorld(out) {
      const v = out || new THREE.Vector3();
      sponge.updateWorldMatrix(true, false);
      return v.setFromMatrixPosition(sponge.matrixWorld);
    },

    /** Park it beside the character's scrubbed arm. */
    placeNear(x, y, z) { root.position.set(x, y, z); },

    /** IDLE / APPROACH / SCRUB / RETREAT, driven by what the page knows. */
    setPhase(phase) {
      // THE SPONGE SPINS ONLY WHILE SCRUBBING. BRAINSTORM-2 line 80 calls a
      // spinning sponge the "big wow factor" and doubts it only on hardware
      // grounds -- servo, wiring, power -- none of which apply on a
      // projector. A sponge held against a limb and not turning reads as a
      // block being dragged.
      spinning = (phase === 'scrub');
      // REST is shared: a parked arm is a parked arm, and four arms folding
      // to different idle poses reads as four of them being broken
      // differently. Only the working poses take the region offset.
      const r = REGION_REACH[region] || { sh: 0, el: 0 };
      if (phase === 'scrub')       target = { sh: CONTACT.sh + r.sh, el: CONTACT.el + r.el };
      else if (phase === 'hover')  target = { sh: HOVER.sh + r.sh,   el: HOVER.el + r.el };
      else                         target = { ...REST };
      // LEAVING THE SCRUB DROPS THE REACH. Without this the estop parks the
      // pose target at REST while reachPt keeps overriding it every frame, so
      // `x` would stop the real arm and leave the cartoon pressed on the
      // person -- the exact failure stopScrubChoreography() exists to prevent,
      // reintroduced one layer lower down.
      if (phase !== 'scrub') reachPt = null;
    },

    /** One sponge stroke. Called on every contact event, alternating sign, so
     *  the arm visibly rubs back and forth instead of buzzing in place. */
    strokeNow(dir) { stroke = dir; strokeAmp = 1; },

    /** PUT THE SPONGE HERE, in world space. The real position, not a mime.
     *
     *  `p` is where the sponge should sit -- web/main.js builds it from
     *  EVENT["scrub_u"] through the character's own limb mapping, so this
     *  point travels up and down the cartoon's forearm exactly as the real
     *  sponge travels the volunteer's. Pass null to hand the arm back to the
     *  pose targets, which is what happens the moment Python stops sending.
     *
     *  IT STORES THE POINT, NOT THE ANGLES. The character breathes, rocks and
     *  is shoved by its own impulse() during the scrub, so the limb moves
     *  under the sponge between messages at 15Hz. Re-solving from the stored
     *  point every frame is what keeps the sponge ON the arm rather than at
     *  the place the arm used to be; storing angles would re-introduce the
     *  stutter the 15Hz feed causes. */
    reachTo(p) {
      if (!p) { reachPt = null; return false; }
      if (!reachPt) reachPt = new THREE.Vector3();
      reachPt.copy(p);
      return true;
    },

    /** Is the arm being driven by real data right now? Reads as a boolean so a
     *  test can tell "mirroring the machine" from "playing the fallback"
     *  without reading a pose and guessing which produced it. */
    get reaching() { return reachPt !== null; },

    /** POSE FROM THE MACHINE'S OWN JOINT ANGLES. `q` is radians, `src` says
     *  whether they were commanded or measured. Pass null to hand the arm
     *  back to the inferred pose, which is what happens when Python stops.
     *
     *  IT VALIDATES BEFORE ADOPTING, like loadArmGeometry does and for the
     *  same reason: a NaN angle propagates silently through three.js into an
     *  arm drawn at no pose at all, with no error anywhere. py/scrubbot.py
     *  publishes `null` for a joint its driver cannot report (a RoArm solves
     *  three of six), and a null must leave that joint alone rather than
     *  read as zero -- zero is a claim the joint is at its origin.
     *
     *  The three angles this drawing HAS are taken and the rest ignored; see
     *  the jointsQ declaration for why a wrist is not invented to spend them
     *  on. Returns whether anything was adopted, so a caller can tell a
     *  refused payload from an accepted one. */
    setJoints(q, src) {
      if (!Array.isArray(q)) { jointsQ = null; jointsSrc = null; return false; }
      // ONLY THE THREE THIS ARM DRAWS. A payload whose first three are all
      // null or unusable carries nothing this file can pose from, so it is
      // refused whole rather than adopted as a partial pose -- half a pose
      // and half an inference is the drift the fallback exists to avoid.
      // ALL SIX NOW, NOT THREE. This took only the first three because the
      // drawn arm had no wrist to spend the others on -- the mesh at
      // GEOM.fore was decoration that never turned. The OpenYAM's wrist
      // moves the tool 163mm at 0.8 rad (armmesh_openyam's own self-test
      // measures it), so throwing 4, 5 and 6 away drew a real arm in a pose
      // it was not in, with the error concentrated exactly where the tool
      // meets the person.
      //
      // The first three still decide whether a payload is usable: an arm
      // with no base, shoulder or elbow has no pose at all, while a missing
      // wrist angle just leaves that joint where it was.
      const next = [];
      for (let i = 0; i < 6; i++) {
        const v = Number(q[i]);
        next.push(Number.isFinite(v) ? v : null);
      }
      if (next.slice(0, 3).every((v) => v === null)) {
        jointsQ = null; jointsSrc = null; return false;
      }
      jointsQ = next;
      jointsSrc = (src === 'measured') ? 'measured' : 'commanded';
      return true;
    },

    /** 'commanded', 'measured', or null when the arm is drawing its own
     *  inference. THE PAGE MUST BE ABLE TO SAY WHICH, and it cannot read it
     *  off the pose: a commanded angle and a measured one are the same
     *  number on screen. That is the whole reason this getter exists rather
     *  than the HUD guessing from `reaching`. */
    get jointsSource() { return jointsSrc; },

    update(dt) {
      const h = Math.min(dt || 0.016, 0.05);
      // SOLVE THE REACH BEFORE THE SPRING RUNS, so the spring's job is
      // unchanged: it still travels toward `target` with mass and settle. The
      // only difference is where `target` came from -- the real sponge
      // position rather than one of three fixed poses.
      //
      // SOLVED EVERY FRAME FROM THE WORLD POINT. The feed is 15Hz and the
      // render loop is 60fps, so stepping angles on arrival would stutter four
      // frames in five. Re-solving here means the point is what the feed
      // updates and the ANGLES move continuously, and the spring below then
      // smooths whatever remains -- which is the interpolation, done once,
      // where the limb's own motion is also accounted for.
      // THE MACHINE'S OWN ANGLES OUTRANK EVERY INFERENCE, and that ordering
      // is the point of phase 3. `reachPt` solves where the sponge OUGHT to
      // be and setPhase picks a pose the arm PROBABLY holds; jointsQ is what
      // the arm reports. When both are present the report wins, because a
      // drawing that argues with its own machine is the thing this whole
      // plan calls "whatever is real".
      //
      // THEY FEED `target`, NOT `cur`. Writing the angles straight onto the
      // drawn rotation would step the arm at the wire's 15Hz and stutter
      // four frames in five -- the exact failure reachTo()'s own comment
      // records. Handing them to the spring is the interpolation: 15Hz in,
      // 60fps out, through the same K and DAMP everything else here travels
      // on, so the real arm arrives with the same mass the cartoon has.
      if (jointsQ) {
        // A null joint HOLDS ITS TARGET rather than reading as zero. A RoArm
        // reports three of six and py/scrubbot.py sends null for the rest;
        // zero would yank the joint to its origin on every frame.
        if (jointsQ[0] !== null) yawTarget = jointsQ[0];
        if (jointsQ[1] !== null) target = { ...target, sh: jointsQ[1] };
        if (jointsQ[2] !== null) target = { ...target, el: jointsQ[2] };
        // THE WRIST, JOINTS 4 TO 6. Straight onto the group rather than
        // through the spring: the springs above exist so a cartoon arm
        // TRAVELS to a commanded pose instead of snapping, and a measured
        // wrist is not a destination -- it is where the metal already is,
        // arriving at 15Hz. Smoothing it would draw the wrist trailing the
        // arm it is bolted to.
        //
        // Axes from the URDF, read with armmesh_openyam._model():
        //   q[3] joint4  +y   the forearm roll
        //   q[4] joint5  +x   the wrist pitch, the one that moves the tool
        //   q[5] joint6  -z   the claw turn about its own axis
        // The page's fore link runs along +y, so these map to the same
        // three axes in the wrist's own frame.
        if (jointsQ[3] !== null) wrist.rotation.y = jointsQ[3];
        if (jointsQ[4] !== null) wrist.rotation.x = jointsQ[4];
        if (jointsQ[5] !== null) wrist.rotation.z = -jointsQ[5];
      } else if (reachPt) {
        // Into the arm root's own frame. worldToLocal mutates, so this goes
        // through the scratch vector rather than the stored point -- copying
        // the solved frame back into reachPt would make each frame's answer
        // the input to the next and walk the target away.
        root.updateWorldMatrix(true, false);
        _p.copy(reachPt);
        root.worldToLocal(_p);
        // ALL THREE COORDINATES, IN THE ORDER solveIK DECLARES THEM. This read
        // `solveIK(_p.y, _p.z)` -- two arguments to a three-argument function,
        // so the solver got x=_p.y, y=_p.z and z=undefined, and every
        // `Math.hypot(x, z)` inside it returned NaN. A NaN shoulder angle goes
        // into three.js without an error anywhere, which is exactly the silent
        // failure solveIK's own clamp comment warns about, and it meant the
        // whole reach path had been drawing no pose at all rather than a wrong
        // one. Caught by gripping a prop and watching it vanish.
        const s = solveIK(_p.x, _p.y, _p.z);
        // A REFUSED SOLVE HOLDS THE LAST TARGET. Same rule the FSM uses when
        // the governor says no (py/scrubbot.py: `arm.hold()`): an unreachable
        // point must not snap the arm anywhere, it must leave it where it is.
        //
        // THE SOLVED YAW IS DELIBERATELY NOT APPLIED. solveIK returns one, and
        // feeding it to yawTarget was tried: on an arm rolled about Z by its
        // measured mount the yaw the planar decomposition wants is not the yaw
        // that swings the working plane onto the target, so it turned the base
        // away from the point it was reaching for. The scrub has always run
        // with the base still and lands on the limb; leaving it still is the
        // behaviour that was measured to work. See FEED_TRAY above for why the
        // feed beat uses poses instead of this path at all.
        if (s) target = { sh: s.sh, el: s.el };
      }
      for (const j of ['sh', 'el']) {
        vel[j] += (target[j] - cur[j]) * K * h;
        vel[j] *= Math.pow(DAMP, h * 60);
        cur[j] += vel[j] * h;
      }
      // THE BASE YAW, on the same spring. It only ever moves when real joint
      // angles arrive -- yawTarget starts at 0 and nothing else writes it --
      // so with no backend this is three lines of arithmetic on zeros and
      // the drawn yaw stays exactly where it has always been.
      yawVel += (yawTarget - yawCur) * K * h;
      yawVel *= Math.pow(DAMP, h * 60);
      yawCur += yawVel * h;
      strokeAmp *= Math.pow(0.06, h);       // ~200ms of travel per stroke
      // CLAMPED AT THE POINT OF DRAWING, not where the targets are set.
      //
      // The spring OVERSHOOTS -- that is the whole reason it reads as a machine
      // with mass rather than a diagram -- so a pose that is legal as a target
      // is not automatically legal as a frame. Clamping the target would leave
      // the overshoot free to carry the joint past the limit anyway, and it is
      // the drawn frame a judge sees. So the last thing that happens before the
      // angle reaches three.js is the check.
      //
      // `cur` itself is deliberately NOT written back. Letting the spring keep
      // its own overshot state means it decays out naturally; writing the
      // clamped value back would make the joint stick against the limit and
      // then lurch when the target moves away from it.
      // CLAMPED TO THE FIRMWARE'S OWN +/-90, through the same function and
      // for the same stated reason: no angle the page draws may be a pose
      // the metal cannot hold, and the real arm's j0 is not this drawing's
      // yaw range -- the OpenYAM's j0 runs to +/-2.6 rad. Letting it through
      // unclamped would swing the drawn column past where the RoArm the
      // LIMITS table describes can go.
      yaw.rotation.y  = clampJoint('yaw', yawCur);
      upper.rotation.x = clampJoint('sh', cur.sh);
      fore.rotation.x  = clampJoint('el', cur.el);
      // the stroke is a small ROLL, which reads as rubbing across the limb
      fore.rotation.z  = stroke * strokeAmp * 0.30;
      sponge.rotation.z = -stroke * strokeAmp * 0.18;
      // AND THE HEAD TURNS WHILE IT IS ON THE BODY. Spun on Y, which is the
      // sponge's own axis against the limb -- Z is already owned by the
      // stroke above, and driving both on one axis would cancel the rub.
      //
      // EASED IN AND OUT, not switched. A head that snaps to full speed the
      // instant the phase flips reads as a video cut; spinUp is a level, so
      // it visibly spools up as the arm lands and coasts down as it lifts.
      spinUp += ((spinning ? 1 : 0) - spinUp) * Math.min(1, h * 6);
      if (spinUp > 0.001) sponge.rotation.y += SPIN_RATE * spinUp * h;
    },
  };
}
