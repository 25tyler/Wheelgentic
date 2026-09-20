/* carechair-embed.js -- open the Wheelgentic 3D view from Crystal's UI.
 *
 * WHY THIS FILE EXISTS AT ALL, RATHER THAN AN EDIT TO HER app.js
 * --------------------------------------------------------------
 * Her UI is the product's front door and it is hers: index.html, app.js,
 * server.js, robot-adapter.js, style.css and voice-*.js are not ours to
 * change (plan section 8). So this attaches from the outside and touches
 * nothing she wrote. Her side is ONE line in server.js's static table:
 *
 *     ['/carechair-embed.js', ['carechair-embed.js', 'text/javascript']],
 *
 * plus a <script src> beside her app.js, and that is the whole integration.
 *
 * HOW IT ATTACHES WITHOUT FIGHTING HER CODE
 * -----------------------------------------
 * Her app.js binds a document-level click listener and calls action() for
 * anything with [data-action]. Listeners on the same target fire in the
 * order they were added, and she never calls stopPropagation, so a second
 * document listener sees the same clicks and cannot starve hers. We read;
 * we do not preventDefault and we do not swallow. If this file throws or is
 * never served, her UI behaves exactly as it does today -- which is the
 * property that makes it safe to hand her a one-line change.
 *
 * WHAT IT DOES NOT DO
 * -------------------
 * It does not send a task. Her robot-adapter.js already owns that path and
 * already POSTs to /api/task with a command_id it refuses to retry. A second
 * sender would be a second door into the machine past the governor, which is
 * the one thing the architecture forbids (plan section 8). This file opens a
 * WINDOW onto the machine. It is a viewer, not a controller.
 */

(() => {
  'use strict';

  // Her button names, mapped to ours. Values are the ?view= keys web/main.js
  // already accepts -- VIEWS in main.js is the other half of this contract,
  // and an unknown key there falls back to the full scene rather than an
  // error page, so a button she adds before we add a framing still opens
  // something sensible.
  const VIEWS = {
    bathe: 'bathe',     // "Showering"
    eat:   'eat',       // "Eating"
    meds:  'meds',      // "Take Meds"
  };

  // Where our page is served from. Same host by default; a data attribute on
  // the script tag overrides it, so she can point at another machine without
  // editing this file either.
  const SELF = document.currentScript;
  const ORIGIN = (SELF && SELF.dataset.wheelgentic) || 'http://localhost:8000';

  /* The panel. Built once, lazily, and reused: an iframe that is created and
   * destroyed per click reloads three.js and the whole scene every time,
   * which on the demo machine is seconds of black. Hidden instead. */
  let host = null;
  let frame = null;
  let current = null;
  let reordering = false;   // see the 'close' handler in build()

  function build() {
    host = document.createElement('dialog');
    // Inline styles rather than a class: her style.css is hers, and a class
    // name we invent could collide with one she adds later.
    host.style.cssText = [
      'inset:0', 'display:none', 'align-items:center', 'justify-content:center',
      'background:rgba(12,14,22,.72)', 'backdrop-filter:blur(3px)',
      'border:0', 'padding:0', 'overflow:hidden',
    ].join(';');

    // ABOVE HER DIALOG, WHICH IS THE ONE THING z-index ALONE CANNOT DO. Her
    // modal is a native <dialog> opened with showModal(), so it lives in the
    // browser's top layer and paints over EVERY normal element no matter how
    // large their z-index -- measured: our panel at z-index 9999 rendered
    // underneath her check-in card and the 3D view was unreadable. The top
    // layer is only reachable from the top layer, so the panel is a <dialog>
    // too. Later showModal() wins among dialogs, and ours opens on the same
    // click after hers, so ours is on top and her card stays behind it,
    // reachable the moment ours is closed.
    // A dialog's default max-width/max-height are content-sized, so without
    // these the backdrop is a small box in the middle rather than the screen.
    host.style.position = 'fixed';
    host.style.maxWidth = '100vw';
    host.style.maxHeight = '100vh';
    host.style.width = '100vw';
    host.style.height = '100vh';

    const shell = document.createElement('div');
    shell.style.cssText = [
      'position:relative', 'width:min(92vw,1100px)', 'height:min(78vh,700px)',
      'border-radius:14px', 'overflow:hidden',
      'box-shadow:0 24px 70px rgba(0,0,0,.45)', 'background:#0b0e16',
    ].join(';');

    frame = document.createElement('iframe');
    frame.style.cssText = 'width:100%;height:100%;border:0;display:block';
    // No allow= list: this page needs no camera, no microphone and no
    // autoplay. The 3D view draws from numbers on a websocket; the browser
    // permission prompt a media feature would trigger is exactly the kind of
    // surprise a caretaker should never see.
    frame.setAttribute('title', 'Wheelgentic live view');

    const close = document.createElement('button');
    close.textContent = '×';
    close.setAttribute('aria-label', 'Close live view');
    close.style.cssText = [
      'position:absolute', 'top:10px', 'right:12px', 'z-index:2',
      'width:34px', 'height:34px', 'border-radius:50%', 'border:0',
      'background:rgba(255,255,255,.14)', 'color:#fff',
      'font-size:20px', 'line-height:1', 'cursor:pointer',
    ].join(';');
    close.addEventListener('click', hide);

    shell.append(frame, close);
    host.append(shell);
    // Clicking the dimmed backdrop closes, the way her own dialog does.
    host.addEventListener('click', (e) => { if (e.target === host) hide(); });
    // Every close route ends here -- Escape, the backdrop, the button -- so
    // the display flag can never disagree with whether the dialog is open.
    // `reordering` is true only for the close()/showModal() pair below, which
    // is a re-entry into the top layer rather than a real close. Without the
    // flag that pair would fire this handler and hide the panel it is trying
    // to raise.
    host.addEventListener('close', () => {
      if (reordering) return;
      host.style.display = 'none';
    });
    document.body.append(host);
  }

  function show(view) {
    if (!host) build();
    // Only reload when the view actually changes. Re-assigning the same src
    // restarts the scene for no reason.
    if (view !== current) {
      frame.src = `${ORIGIN}/?view=${encodeURIComponent(view)}`;
      current = view;
    }
    // showModal() puts it in the top layer; display:flex is what centres the
    // shell once it is there. A dialog that is merely display:flex is not
    // modal and would sit under hers again.
    host.style.display = 'flex';

    // WAIT FOR HER DIALOG, THEN OPEN ON TOP OF IT. Instrumented on the real
    // page: the showModal order is OURS then HERS, so opening immediately puts
    // her check-in card above our view and the 3D picture is invisible.
    //
    // Closing and re-opening ours to win the top layer was tried and is worse:
    // the close/open pair races its own 'close' handler and the panel vanishes
    // altogether. So we simply open LAST -- one frame later, after her
    // listener has run -- which is all the top layer's rule requires.
    //
    // rAF rather than setTimeout(0) because the deciding moment is a paint:
    // waiting for the next frame guarantees her showModal() has already
    // happened, and it costs 16ms nobody can see against an iframe that takes
    // far longer to draw a 3D scene anyway.
    requestAnimationFrame(() => {
      if (host.open) return;              // nothing else opened over us
      try { host.showModal(); } catch (_) { /* already gone */ }
    });
  }

  function hide() {
    if (!host) return;
    host.style.display = 'none';
    // close() releases the top layer, which is what lets her own dialog --
    // still open behind ours -- take focus and be readable again.
    if (host.open) { try { host.close(); } catch (_) { /* not open */ } }
  }

  document.addEventListener('click', (e) => {
    const el = e.target.closest && e.target.closest('[data-action]');
    if (!el) return;
    const view = VIEWS[el.dataset.action];
    if (!view) return;              // her other buttons are none of our business
    // Deliberately NOT preventDefault: her action() still runs and still logs
    // the care event. Both things happen, which is what the product wants --
    // the journal entry AND the picture.
    show(view);
  });

  // Escape is handled by the <dialog> itself: the browser closes it and fires
  // 'close'. We listen for that rather than for the key, because closing can
  // also come from the backdrop or the button, and every route has to leave
  // display:none behind it. Without this the dialog would close while
  // host.style.display stayed 'flex', and the next show() would set an
  // already-flex element and never call showModal() again -- the panel would
  // simply stop opening, with nothing in the console.
  //
  // Wired in build() rather than here, because `host` does not exist until
  // the first click.
})();
