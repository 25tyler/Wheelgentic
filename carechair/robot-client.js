// What the arms are doing, and the two live views, on the page rather than on the desktop:
// the cartoon that mirrors the person (web/, served by scrub3d/live/carebot.py) and the 3D
// view (Rerun's web viewer, which carebot starts). The page asks OUR server, which asks the
// robot backend: the backend is another origin and the page may not.
const WORDS = {
  idle: ['Ready', 'Waiting at its resting position. Ask for a shower, a drink, food or your medication.'],
  showering: ['Helping you wash', 'Say “stop” at any time and it will draw back and fold away.'],
  drinking: ['Bringing your drink', 'It comes to your mouth, tips, and goes back by itself.'],
  stopping: ['Stopping', 'Drawing back.'],
  parking: ['Folding away', 'Going back to its resting position.'],
  demo: ['Demo mode', 'Voice commands are understood but not sent to the arms (ROBOT_MODE=demo).'],
  unreachable: ['Helper not connected', 'Start scrub3d/live/carebot.py, then this will come alive.'],
};
const esc = text => String(text).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const SCENES = { showering: '?view=bathe', eating: '?view=eat', take_meds: '?view=meds' };   // web/main.js VIEWS
export function mountRobot(root, { fetchFn = (...a) => fetch(...a), every = 1000, onDone = null } = {}) {
  if (!root) return { dispose() {} };
  root.innerHTML = `
    <div class="robot-card">
      <div class="robot-status"><span class="live-dot" id="robot-dot"></span>
        <div><strong id="robot-title">Connecting…</strong><small id="robot-text"></small><small id="robot-said" class="robot-said"></small></div>
        <button class="stop-button" id="robot-stop" type="button">Stop the arms</button></div>
      <div class="robot-views" id="robot-views" hidden>
        <figure><iframe id="robot-cartoon" title="Your cartoon, mirroring you" allow="camera; autoplay" loading="lazy"></iframe><figcaption>You, as the cartoon sees you. Only the cartoon is ever shown.</figcaption></figure>
        <figure><iframe id="robot-rerun" title="The arms and you, in 3D" loading="lazy"></iframe><figcaption>The arms and what they plan, in 3D.</figcaption></figure>
      </div>
    </div>`;
  const $ = id => root.querySelector(id);
  let disposed = false, shown = '', timer = null, running = null;
  // In the split view (web/compare.html) the 3D render already fills the right half and the
  // cartoon opens in Tyler's own window over this page: showing both again here would be twice.
  const framed = (() => { try { return window.self !== window.top; } catch { return true; } })();
  const BUTTON = { showering: 'bathe', eating: 'eat', take_meds: 'meds' };
  async function poll() {
    if (disposed) return;
    let s;
    try { s = await (await fetchFn('/api/robot/status')).json(); } catch { s = { state: 'unreachable' }; }
    if (disposed) return;
    const [title, text] = WORDS[s.state] || [s.state, ''];
    $('#robot-title').textContent = title + (s.dry ? ' (practice run: nothing real moves)' : '');
    $('#robot-text').textContent = text;
    $('#robot-said').textContent = (s.said || []).at(-1) || '';
    $('#robot-dot').className = 'live-dot robot-' + (s.state || 'unreachable');
    $('#robot-stop').hidden = !['showering', 'drinking'].includes(s.state);
    // A task that was running and no longer is has finished: the page logs it.
    if (['showering', 'drinking'].includes(s.state) && !running) {
      running = { task: s.task, at: Date.now() };
      // Asked for by voice: open the cartoon on that scene, as a press of the care button does.
      if (framed) document.querySelector(`.main-action[data-action="${BUTTON[s.task]}"]`)?.click();
    }
    else if (running && s.state === 'idle') { const was = running; running = null; onDone?.(was.task, (Date.now() - was.at) / 1000); }
    const views = !framed && s.views && s.views.cartoon ? s.views : null;
    // The cartoon shows the scene of what the arms are doing: said aloud or not, it follows.
    const scene = views ? views.cartoon + (SCENES[running?.task] || '') : '';
    const key = views ? scene + views.rerun : '';
    if (key !== shown) {                       // only on a change: a reload restarts its camera
      const rerunWas = $('#robot-rerun').getAttribute('src');
      shown = key;
      $('#robot-views').hidden = !views;
      if (views) { $('#robot-cartoon').src = scene; if (rerunWas !== views.rerun) $('#robot-rerun').src = views.rerun; }
    }
    timer = setTimeout(poll, every);
  }
  $('#robot-stop').onclick = () => { void fetchFn('/api/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'stop' }) }).catch(() => {}); };
  void poll();
  return { dispose() { disposed = true; clearTimeout(timer); }, esc };
}
