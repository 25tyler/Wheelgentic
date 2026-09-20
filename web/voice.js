// web/voice.js — say what you want and the chair does it.
//
// The brainstorm lists "Voice support / personal agent" as one of the four
// things this wheelchair does. Someone who cannot wash themselves often
// cannot reach a button panel either, so a voice is not a garnish here -- it
// is the interface that matches the user.
//
// TAKEN, NOT BUILT. This is the browser's own Web Speech API, which ships in
// Chrome and needs no library, no key and no server. Checked before writing a
// line: `window.SpeechRecognition` and `window.speechSynthesis` are both
// present on the demo machine. The alternative was Whisper in a worker, which
// is ~40MB of model to do worse at six fixed phrases.
//
// IT SPEAKS BACK, and that matters more than it looks. A machine that only
// listens is unnerving when it is about to touch you. One short spoken
// acknowledgement per command is the difference between a tool and a carer.
//
// ON-DEVICE, AND SAY SO HONESTLY. Chrome's recogniser sends audio to Google
// for transcription, so this is the ONE part of the system that is not
// on-device. The HUD's privacy line is about the CAMERA, which never leaves
// the laptop, and nothing here changes that -- but do not claim the voice is
// local, because it is not. If a judge asks, the honest answer is that the
// camera is local and the speech is not yet.

/** Intents the chair understands, in the words a person would actually use.
 *  Matched loosely: the recogniser returns a whole phrase and we look for any
 *  of these inside it, so "can you wash my arm please" hits `shower`. */
// EXPORTED so a test can assert the ORDER. match() returns the first
// intent whose phrase is a substring, so a more specific intent listed
// after a more general one is unreachable: "clean my left arm" contains
// "clean", and with the wash intent first the script's closing beat
// restarted the shower instead of answering. The order is load-bearing
// and nothing about reading this list makes that obvious.
export const INTENTS = [
  // ---- QUESTIONS THE CHAIR ANSWERS FROM ITS OWN LIVE STATE --------------
  //
  // FIRST IN THE LIST, and that is the whole design of this block. The
  // matcher takes the first intent whose phrase appears anywhere in what was
  // heard, and a question about a thing contains the word for that thing:
  // "how clean am I" contains 'clean', "what is my heart rate" contains
  // 'heart', "which arm is washing my leg" contains 'my leg' and 'wash'.
  // Placed after the action intents, all three were measured going to the
  // wrong place -- the first two started a wash and opened vitals, and the
  // chair answered a question by doing a chore. Questions are therefore
  // matched before orders, and each phrase below is written to be long
  // enough that it cannot swallow an order.
  //
  // Everything here is `say: null`: the page fills the answer in from what is
  // on screen RIGHT NOW -- the solver's per-arm counts, the cleanliness
  // fraction, the seconds of care, the heart rate the readout is drawing.
  // Ask the same question twice during a wash and the answer changes.
  //
  // WHY THIS IS THE AGENT HALF AND A CHATBOT IS NOT. BRAINSTORM-2 lists
  // "personal agent" beside voice control. A language model would answer
  // from its training, fluently, about a machine it cannot see, and a judge
  // could not check a word of it. Every answer here is checkable against the
  // projector while it is being spoken, which is the stronger demonstration
  // -- and it needs no API key on a laptop strangers will handle, no venue
  // wifi, and no latency on the beat that has to feel instant.
  { mode: 'ask-clean',  say: null,
    phrases: ['how clean', 'how much left', 'are you done', 'how far along',
              'how much longer', 'finished yet', 'are we done'] },
  { mode: 'ask-arms',   say: null,
    phrases: ['which arm is', 'who is washing', 'what is each arm',
              'which one is doing', 'who does what', 'how are you splitting'] },
  { mode: 'ask-care',   say: null,
    phrases: ['how long have you', 'how much time', 'time so far',
              'how long has this'] },
  { mode: 'ask-vitals', say: null,
    phrases: ['what is my heart', 'how is my heart', 'my pulse',
              'am i okay', 'how am i doing', 'my heart rate'] },

  // ASK FOR A SPOT. BRAINSTORM-2 line 92: "User can indicate an area to clean
  // more, and the arm focuses there." Listed BEFORE the generic wash intent
  // would match, because "clean my arms" contains 'clean' and would otherwise
  // just restart the shower.
  //
  // The reply is filled in by the page, which knows which arm the solver gave
  // that region to -- a fixed sentence here would be guessing at the
  // partition's answer.
  { mode: 'spot',   say: null,
    phrases: ['my arm', 'my arms', 'left arm', 'right arm',
              'my left', 'my right', 'arm again', 'more on'] },
  { mode: 'shower', say: 'Starting your wash.',
    phrases: ['wash', 'shower', 'clean', 'scrub', 'bath'] },
  { mode: 'feed',   say: 'Bringing your food.',
    phrases: ['eat', 'food', 'feed', 'hungry', 'meal', 'lunch', 'dinner'] },
  // DRINKING IS ITS OWN REQUEST. 'drink' and 'water' used to sit in the food
  // list, so "I'm thirsty" answered "Bringing your food" and carried a bowl
  // of soup to the person's mouth. The mode strip promises eating, drinking
  // and pills; this is the middle one.
  { mode: 'drink',  say: 'Here is some water.',
    phrases: ['drink', 'water', 'thirsty', 'sip'] },
  { mode: 'pills',  say: 'Here are your pills, with some water.',
    phrases: ['pill', 'medicine', 'medication', 'tablet'] },
  { mode: 'vitals', say: 'Checking your heart rate.',
    phrases: ['vital', 'heart', 'pulse', 'how am i', 'health', 'oxygen'] },
  { mode: 'stop',   say: 'Stopping now.',
    phrases: ['stop', 'wait', 'pause', 'hold on'] },

  // ---- THE AGENT HALF ---------------------------------------------------
  // The brainstorm lists "personal agent (Claude computer use, Voice
  // control)" with a question mark on the computer-use half, so it is an open
  // idea rather than a decision. Wiring a live model here was considered and
  // rejected for this demo: it needs an API key on a laptop that will be
  // handled by strangers, it needs network at a venue where wifi is a
  // coin-toss, and it adds seconds of latency to the one beat that has to
  // feel instant.
  //
  // These answer from what the chair ACTUALLY knows instead. That is a
  // stronger claim than a chatbot bolted on: every answer below is a fact
  // this system can genuinely report, which is what a personal agent for
  // someone being cared for actually needs to do.
  { mode: null, say: 'Yes. Everything the camera sees stays on this laptop. Nothing is recorded and nobody sees it but the cartoon.',
    phrases: ['private', 'privacy', 'recording', 'who can see', 'is anyone watching', 'camera'] },
  { mode: null, say: 'Four arms. Each one measures the part of you it is responsible for, so no two people get the same movement.',
    phrases: ['how many arms', 'how does it work', 'what are you doing', 'explain'] },
  { mode: null, say: 'I stop the moment you say stop, and every joint gives way if it meets resistance.',
    phrases: ['is it safe', 'will it hurt', 'safe', 'hurt me'] },
  { mode: null, say: 'I am here. Ask me for a wash, for food, for your pills, or for your heart rate.',
    phrases: ['hello', 'hey', 'are you there', 'help me', 'what can you do'] },

];

/** Say something out loud.
 *
 *  SEPARATE FROM THE RECOGNISER ON PURPOSE. Speaking is speechSynthesis and
 *  listening is SpeechRecognition -- two different APIs with two different
 *  support stories. This used to live only inside makeVoice, which returns
 *  null when there is no recogniser, so on any browser that could speak but
 *  not listen the chair went completely mute. The spot answer ("arm 3 has
 *  that") is the one line the script promises out loud, and it was reachable
 *  only after the microphone had already been started.
 *
 *  A chair that cannot hear you should still be able to answer.
 */
export function say(text) {
  // NOTHING TO SAY IS NOT "null". SpeechSynthesisUtterance(null) reads the
  // word "null" out loud on stage.
  if (!text) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    // Slightly slow and low. The default voice reads like a phone menu;
    // this is meant to sound like it is talking to someone it is caring for.
    u.rate = 0.95;
    u.pitch = 0.9;
    window.speechSynthesis.cancel();   // never queue, always answer the last thing
    window.speechSynthesis.speak(u);
  } catch (e) { /* a demo must not die because audio is blocked */ }
}

export function makeVoice(opts = {}) {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) {
    console.warn('voice: no SpeechRecognition in this browser');
    return null;
  }

  const onIntent = opts.onIntent || (() => {});
  const onHeard  = opts.onHeard  || (() => {});
  let rec = null;
  let listening = false;
  // The recogniser stops itself after a pause. For a demo the chair should
  // keep listening, so restart it -- but only while `listening` is true, or a
  // stopped recogniser resurrects itself forever.
  let wantRestart = false;

  // ONE implementation, shared with the module-level say() above. This was a
  // second copy of the same twelve lines; two copies of "how the chair
  // sounds" drift the moment one of them is tuned.
  const speak = say;

  function match(text) {
    const t = text.toLowerCase();
    for (const intent of INTENTS) {
      if (intent.phrases.some((p) => t.includes(p))) return intent;
    }
    return null;
  }

  function start() {
    if (listening) return;
    rec = new Rec();
    rec.continuous = true;
    rec.interimResults = true;      // so the HUD can show partial words
    rec.lang = 'en-US';

    rec.onresult = (ev) => {
      const r = ev.results[ev.results.length - 1];
      const text = r[0].transcript.trim();
      onHeard(text, r.isFinal);
      if (!r.isFinal) return;
      const intent = match(text);
      if (!intent) return;
      speak(intent.say);
      // A null mode is an ANSWER rather than a command: the chair says
      // something true and changes nothing. Without this guard an answer
      // would fall through and switch to a mode named null.
      if (intent.mode) onIntent(intent.mode, text);
    };

    // A denied microphone is a normal outcome on a locked-down machine and
    // must not take the page with it.
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        wantRestart = false;
        listening = false;
        onHeard('microphone blocked', true);
        return;
      }
      // NETWORK IS THE ERROR TO EXPECT at a venue, because Chrome sends the
      // audio to Google to transcribe -- the one part of this system that is
      // not on-device, as the demo script says out loud.
      //
      // Unhandled, it loops: onend fires, wantRestart is still true, it
      // restarts, fails again. Meanwhile the projector still reads SAY
      // SOMETHING, which looks exactly like a working microphone. The
      // presenter has just asked a judge to speak and is watching the judge,
      // not the readout, so nobody finds out until the silence is long enough
      // to be the demo's worst moment.
      //
      // Stop and SAY SO. A screen that admits it cannot hear is recoverable
      // in one keypress; a screen that lies is not recoverable at all.
      // `audio-capture` is the same shape: the microphone device went away
      // mid-session (unplugged, or another app took it), and without this it
      // loops exactly as the network case did.
      if (e.error === 'network' || e.error === 'audio-capture') {
        wantRestart = false;
        listening = false;
        onHeard(e.error === 'network' ? 'no network for speech'
                                      : 'microphone blocked', true);
      }
      // Everything else is normal and self-corrects: `no-speech` is a silence
      // timeout that onend restarts, and `aborted` is what stop() causes.
    };
    rec.onend = () => { if (wantRestart) { try { rec.start(); } catch (e) {} } };

    try { rec.start(); listening = true; wantRestart = true; }
    catch (e) { console.warn('voice: could not start', e); }
  }

  function stop() {
    wantRestart = false;
    listening = false;
    try { rec && rec.stop(); } catch (e) {}
    try { window.speechSynthesis.cancel(); } catch (e) {}
  }

  return {
    start, stop, speak,
    /** Drive the recogniser's own error handler, for tests.
     *
     *  The faults that matter here -- a dead network, a microphone that went
     *  away -- only happen on a real venue connection, which is exactly the
     *  place nobody can run a test. `rec` is a closure local created inside
     *  start(), so there is no way to reach its onerror from outside, and
     *  re-implementing the handling in a test would prove nothing about the
     *  code that ships.
     *
     *  This calls the SHIPPED handler with the SHIPPED event shape. It is not
     *  a second path: delete the handler and this stops working too.
     */
    simulateError(name) {
      if (rec && typeof rec.onerror === 'function') rec.onerror({ error: name });
    },
    get listening() { return listening; },
    toggle() { listening ? stop() : start(); return listening; },
  };
}
