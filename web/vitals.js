// web/vitals.js — the heartbeat trace under the vitals readout.
//
// WHY THIS IS HAND-WRITTEN AND NOT A LIBRARY. The standing rule on this
// project is to take rather than build, so this was checked first: there is
// no charting library vendored, ECC ships no sparkline skill, and the
// smallest real option (uPlot, ~40KB) is larger than the entire file it would
// replace. A scrolling one-line waveform is about forty lines of canvas. When
// taking costs more than building, build.
//
// WHY A TRACE AT ALL. Vitals mode printed a number and stopped. A number
// alone does not read as "this machine is monitoring someone" from ten feet
// away on a projector -- a moving line does, instantly, because everybody has
// seen a hospital monitor. The number is the data; the trace is what makes an
// audience believe it is live.
//
// THE SHAPE IS A REAL PQRST, not a sine. A sine wave reads as a synthesiser;
// the spike-and-recover of an actual cardiac cycle reads as a heart. It costs
// the same to draw.

// SIZED AGAINST THE NUMBER BESIDE IT. At 520 x 90 with a 2px stroke, on a
// 1600px frame, the trace read as a scratch next to digits whose own strokes
// are about 12px -- the two did not look like one instrument, and vitals is
// one of the five things the product claims to do. 760 x 150 puts the trace
// at roughly twice the digits' cap height so the R spike has somewhere to go.
//
// SAMPLES IS DELIBERATELY UNCHANGED. It is a TIME window, not a pixel count
// (see below), so widening the canvas stretches each step and shows the same
// four seconds larger. Scaling it with the width would put 5.9 seconds on
// screen and re-bunch the beats, which is the exact bug the 240 was chosen to
// fix.
const W = 760;              // css pixels; the canvas is sized x2 for retina
const H = 150;
// SAMPLES ARE NOT PIXELS. Writing one sample per pixel per frame put 520
// frames on screen, which at 60fps is 8.7 seconds and, at a resting rate,
// about ten beats squeezed into the width -- measured, and visible in a
// screenshot as a bunched row of spikes rather than a heartbeat.
//
// A real monitor shows four or five beats across its display. 240 samples is
// a 4 second window, drawn at 760/240 = 3.2px each.
const SAMPLES = 240;

/** One cardiac cycle, as normalised (phase, amplitude) control points.
 *  P wave, QRS complex, T wave -- the shape on every monitor in every
 *  hospital drama, which is exactly why it is legible without a caption. */
const PQRST = [
  [0.00,  0.00], [0.08,  0.00],
  [0.12,  0.12], [0.16,  0.00],   // P: small bump
  [0.22,  0.00],
  [0.25, -0.10],                  // Q: small dip
  [0.28,  1.00],                  // R: the spike
  [0.31, -0.28],                  // S: undershoot
  [0.36,  0.00],
  [0.50,  0.22], [0.60,  0.00],   // T: broad recovery
  [1.00,  0.00],
];

function amplitudeAt(phase) {
  // Linear between control points. Smoothing them would round off the R
  // spike, which is the one feature that makes this read as a heartbeat.
  for (let i = 1; i < PQRST.length; i++) {
    const [p0, a0] = PQRST[i - 1];
    const [p1, a1] = PQRST[i];
    if (phase <= p1) {
      const t = (phase - p0) / (p1 - p0 || 1);
      return a0 + (a1 - a0) * t;
    }
  }
  return 0;
}

export function makeVitals(opts = {}) {
  const host = document.getElementById(opts.mount || 'ecg');
  if (!host) return null;

  const canvas = document.createElement('canvas');
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = `${W}px`;
  canvas.style.height = `${H}px`;
  host.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  // A ring of past samples. The trace scrolls by drawing this buffer, which
  // is cheaper and steadier than translating the canvas every frame.
  const samples = new Float32Array(SAMPLES).fill(0);
  let write = 0;
  let phase = 0;
  let last = performance.now();
  let raf = null;
  let running = false;
  let bpm = 72;

  function frame(now) {
    if (!running) return;
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;

    // Advance the cardiac phase at the current rate. bpm/60 cycles a second.
    // AT ZERO THE PHASE DOES NOT ADVANCE and the sample is flat: no sensor
    // means no beat to draw, and a flat line is the one honest trace.
    phase = bpm ? (phase + dt * (bpm / 60)) % 1 : 0;
    samples[write] = bpm ? amplitudeAt(phase) : 0;
    write = (write + 1) % SAMPLES;

    ctx.clearRect(0, 0, W, H);

    // Baseline, so the trace has something to sit on even between beats.
    ctx.strokeStyle = 'rgba(78,201,245,0.18)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, H * 0.62);
    ctx.lineTo(W, H * 0.62);
    ctx.stroke();

    ctx.strokeStyle = '#4ec9f5';
    // 4, not 2: a 2px stroke on a projector at ten feet is a hairline.
    ctx.lineWidth = 4;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    for (let i = 0; i < SAMPLES; i++) {
      // Read oldest-first so the newest sample is at the right edge, which is
      // the direction every real monitor scrolls.
      const v = samples[(write + i) % SAMPLES];
      const x = (i / (SAMPLES - 1)) * W;
      const y = H * 0.62 - v * H * 0.48;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    // A dot riding the leading edge. Without it the trace reads as a static
    // picture of a waveform rather than something happening now.
    const head = samples[(write + SAMPLES - 1) % SAMPLES];
    ctx.fillStyle = '#9fe6ff';
    ctx.beginPath();
    // 4.5, not 2.6: the trace's stroke went 2 -> 4 when the canvas grew,
    // and a 5.2px dot on a 4px line reads as a bulge rather than a head.
    ctx.arc(W - 1, H * 0.62 - head * H * 0.48, 4.5, 0, Math.PI * 2);
    ctx.fill();

    raf = requestAnimationFrame(frame);
  }

  return {
    canvas,
    start() {
      if (running) return;
      running = true;
      last = performance.now();
      raf = requestAnimationFrame(frame);
    },
    stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
      raf = null;
    },
    /** Drive the rate from whatever the sensor (or the simulation) reports. */
    /** The rate to draw, or 0 for NO READING.
     *
     *  Zero is a real state and not a missing argument. No pulse oximeter is
     *  wired, so the vitals panel asks /api/vitals, gets
     *  {"status":"disconnected"} and has nothing to draw -- and a monitor
     *  that keeps drawing a 72bpm pulse in that state is claiming to read a
     *  heart it cannot see. The `v || 72` fallback used to turn exactly that
     *  case back into a healthy adult.
     */
    setBpm(v) {
      if (!v) { bpm = 0; return; }
      bpm = Math.max(30, Math.min(200, v));
    },
    /** The cardiac phase, 0 to 1 per beat, so anything else that
     *  wants to pulse with the heart reads the SAME value the trace
     *  is drawn from rather than running a timer beside it. */
    get phase() { return phase; },
    get bpm() { return bpm; },
  };
}
