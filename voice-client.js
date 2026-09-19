let history = [];
let dispose = () => {};
export function disposeVoice() { dispose(); dispose = () => {}; }

export function openVoice(modal) {
  modal(`<h2>Talk to Wheelgentic</h2>
    <p>Say what you need: eating, showering, or medication assistance. Everything else stays a conversation.</p>
    <div id="voice-config" class="voice-note">Checking voice services…</div>
    <div class="voice-controls"><button id="record-voice" class="primary">● Start microphone</button>
    <button id="pause-voice" class="secondary">Pause</button><button id="stop-voice" class="stop-button">Stop</button></div>
    <p id="voice-status" role="status" aria-live="polite">Ready. Press the microphone, speak, then finish recording.</p>
    <div class="voice-results" aria-live="polite"><label>You said</label><div id="voice-transcript">—</div>
    <label>Wheelgentic</label><div id="voice-response">How can I help?</div><span id="voice-route" class="voice-route"></span></div>
    <form id="voice-form"><label for="voice-text">Or type a command</label><div class="voice-input-row">
    <input id="voice-text" placeholder="Wash my left arm" maxlength="1000" required autocomplete="off">
    <button id="voice-send" class="primary">Send ↗</button></div></form>
    <details class="voice-debug"><summary>Structured command</summary><pre id="voice-command">No command yet.</pre></details>
    <p class="voice-privacy">Recordings go to Deepgram; transcripts go to Meta. Optional history compression uses The Token Company. Audio is not saved by this app.</p>`);
  const $ = s => document.querySelector(s);
  const dialog = $('#modal');
  let closed = false, busy = false, permissionPending = false;
  let stream, recorder, recordingTimer, requestController;
  let sequence = 0, discard = false;
  const status = text => { if (!closed) $('#voice-status').textContent = text; };
  function releaseMic() { stream?.getTracks().forEach(track => track.stop()); stream = null; clearTimeout(recordingTimer); }
  function setBusy(value) {
    busy = value;
    if (!closed) { $('#record-voice').disabled = value; $('#voice-send').disabled = value; }
  }
  function cancelRecording() {
    discard = true;
    if (recorder?.state === 'recording') recorder.stop();
    releaseMic();
    if (!closed) $('#record-voice').textContent = '● Start microphone';
  }
  async function api(path, options, signal) {
    const response = await fetch(path, { ...options, signal });
    let data;
    try { data = await response.json(); } catch { throw new Error('The voice server is unavailable. Restart npm run dev.'); }
    if (!response.ok) throw new Error(data.error || 'Request failed. Please try again.');
    return data;
  }
  function showResult(data) {
    $('#voice-response').textContent = data.response;
    $('#voice-command').textContent = JSON.stringify(data.command, null, 2);
    const labels = { eating: 'Eating', showering: 'Showering', take_meds: 'Take meds', talk_to_me: 'Talk to me' };
    $('#voice-route').textContent = `${labels[data.command.category]} · ${data.delivery.status}${data.compression === 'compressed' ? ' · history compressed' : ''}`;
    document.querySelectorAll('.main-action').forEach(button => {
      button.classList.toggle('voice-selected', button.dataset.action === ({ eating: 'eat', showering: 'bathe', take_meds: 'meds' }[data.command.category]));
    });
    status(data.compression === 'unavailable' ? 'Done. History compression was unavailable; original context was used.' : 'Ready for your next command.');
  }
  async function processTranscript(transcript, id, signal) {
    if (closed || id !== sequence) return;
    $('#voice-transcript').textContent = transcript;
    status('Understanding your request…');
    const data = await api('/api/command', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ transcript, history }),
    }, signal);
    if (closed || id !== sequence) return;
    showResult(data);
    history = [...history, { role: 'user', content: transcript }, { role: 'assistant', content: data.response }].slice(-20);
  }
  async function submit(text, audio) {
    const id = ++sequence;
    requestController = new AbortController();
    const signal = AbortSignal.any([requestController.signal, AbortSignal.timeout(45000)]);
    setBusy(true);
    $('#voice-response').textContent = 'Working on it…';
    $('#voice-route').textContent = '';
    $('#voice-command').textContent = 'Waiting for interpretation…';
    try {
      if (audio) {
        status('Transcribing with Deepgram…');
        const data = await api('/api/transcribe', { method: 'POST', headers: { 'Content-Type': audio.type }, body: audio }, signal);
        text = data.transcript;
      }
      await processTranscript(text, id, signal);
    } catch (error) {
      if (!closed && id === sequence) {
        $('#voice-response').textContent = 'Request not confirmed. No completed care activity was logged.';
        status(error.name === 'TimeoutError' ? 'Request timed out. Use Stop to cancel any pending robot request.' : error.message);
      }
    } finally { if (!closed && id === sequence) setBusy(false); }
  }
  async function control(action) {
    const id = ++sequence; requestController?.abort(); cancelRecording(); setBusy(true);
    status(`Sending ${action}…`);
    try {
      const data = await api('/api/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) }, AbortSignal.timeout(15000));
      if (!closed && id === sequence) showResult(data);
    } catch (error) { if (id === sequence) status(`${action} was not confirmed: ${error.message}`); }
    finally { if (!closed && id === sequence) setBusy(false); }
  }
  $('#pause-voice').onclick = () => control('pause');
  $('#stop-voice').onclick = () => control('stop');
  $('#voice-form').onsubmit = event => {
    event.preventDefault();
    const text = $('#voice-text').value.trim();
    const direct = text.toLowerCase().replace(/[.!?]/g, '');
    if (['stop', 'pause'].includes(direct)) return control(direct);
    if (busy || permissionPending || recorder?.state === 'recording' || !text) return;
    $('#voice-text').value = ''; submit(text);
  };
  $('#record-voice').onclick = async () => {
    if (recorder?.state === 'recording') { setBusy(true); recorder.stop(); releaseMic(); return; }
    if (busy || permissionPending) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      status('Microphone recording is unavailable in this browser. Use Chrome on localhost or type a command.'); return;
    }
    const startSequence = ++sequence;
    permissionPending = true; $('#record-voice').disabled = true;
    status('Waiting for microphone access…');
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (closed || startSequence !== sequence) { acquired.getTracks().forEach(t => t.stop()); return; }
      stream = acquired;
      const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error('No supported recording format. Please type a command.');
      recorder = new MediaRecorder(stream, { mimeType });
      const chunks = []; discard = false;
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => { cancelRecording(); status('Recording failed. Please try again or type a command.'); };
      recorder.onstop = () => {
        if (closed || discard || startSequence !== sequence) return;
        releaseMic();
        $('#record-voice').textContent = '● Start microphone';
        const blob = new Blob(chunks, { type: mimeType });
        if (!blob.size) { setBusy(false); status('Recording was empty. Please try again.'); return; }
        submit(null, blob);
      };
      recorder.start(); $('#record-voice').textContent = '■ Finish recording';
      status('Listening… Press Finish recording when done (20-second limit).');
      recordingTimer = setTimeout(() => { if (recorder?.state === 'recording') recorder.stop(); releaseMic(); }, 20000);
    } catch (error) {
      releaseMic();
      status(error.name === 'NotAllowedError' ? 'Microphone access was denied. Allow it in the browser or type a command.' : error.message);
    } finally { permissionPending = false; if (!closed) $('#record-voice').disabled = busy; }
  };
  function cleanup() {
    closed = true; ++sequence; cancelRecording(); requestController?.abort();
    dialog.removeEventListener('close', cleanup);
  }
  dispose = cleanup;
  dialog.addEventListener('close', cleanup, { once: true });
  api('/api/voice/status', {}, AbortSignal.timeout(5000)).then(data => {
    if (closed) return;
    $('#voice-config').textContent = `${data.deepgram ? 'Deepgram configured' : 'Deepgram key missing'} · ${data.meta ? 'Meta configured' : 'Meta key missing'} · Robot ${data.robotMode === 'demo' ? 'demo (no movement)' : 'backend enabled'}`;
  }).catch(() => { if (!closed) $('#voice-config').textContent = 'Voice server unavailable. Restart npm run dev.'; });
}
