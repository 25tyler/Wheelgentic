let history = [];
let dispose = () => {};
export function disposeVoice() { dispose(); dispose = () => {}; }

export function openVoice(modal) {
  modal(`<div class="voice-heading"><h2>Talk to Wheelgentic</h2></div>
    <div class="voice-controls"><button id="record-voice" class="microphone-button" aria-pressed="false">
      <span class="microphone-symbol" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg><span class="microphone-stop"></span></span>
      <span id="record-label">Start microphone</span>
    </button></div>
    <p id="voice-status" role="status" aria-live="polite">Tap to speak.</p>
    <div id="voice-results" class="voice-results" aria-live="polite" hidden><div class="voice-message voice-message-user"><span class="voice-speaker">You</span><div id="voice-transcript"></div></div>
      <div class="voice-message"><span class="voice-speaker">Wheelgentic</span><div id="voice-response"></div></div></div>
    <form id="voice-form" class="voice-input-row">
      <input id="voice-text" aria-label="Message Wheelgentic" placeholder="Or type a message…" maxlength="1000" required autocomplete="off">
      <button id="voice-send" class="voice-send-button" aria-label="Send message">↑</button>
    </form>`);
  const $ = s => document.querySelector(s);
  const dialog = $('#modal');
  dialog.classList.add('voice-dialog');
  let closed = false, busy = false, permissionPending = false;
  let stream, recorder, recordingTimer, requestController;
  let sequence = 0, discard = false;
  const status = text => { if (!closed) $('#voice-status').textContent = text; };
  function microphone(recording) {
    if (closed) return;
    $('#record-label').textContent = recording ? 'Stop microphone' : 'Start microphone';
    $('#record-voice').classList.toggle('is-recording', recording);
    $('#record-voice').setAttribute('aria-pressed', String(recording));
  }
  function releaseMic() { stream?.getTracks().forEach(track => track.stop()); stream = null; clearTimeout(recordingTimer); }
  function setBusy(value) {
    busy = value;
    if (!closed) { $('#record-voice').disabled = value; $('#voice-send').disabled = value; }
  }
  function cancelRecording() {
    discard = true;
    if (recorder?.state === 'recording') recorder.stop();
    releaseMic();
    microphone(false);
  }
  async function api(path, options, signal) {
    const response = await fetch(path, { ...options, signal });
    let data;
    try { data = await response.json(); } catch { throw new Error('Could not connect. Please try again.'); }
    if (!response.ok) {
      const messages = {
        PROVIDER_ERROR: 'Could not connect right now. Please try again.',
        PROVIDER_TIMEOUT: 'That took too long. Please try again.',
        NOT_CONFIGURED: 'Voice support is unavailable right now.',
        INVALID_COMMAND: 'I didn’t catch that. Please try saying it another way.',
        SERVER_ERROR: 'Something went wrong. Please try again.',
      };
      throw new Error(messages[data.code] || data.error || 'Request failed. Please try again.');
    }
    return data;
  }
  function showResult(data) {
    $('#voice-response').textContent = data.response;
    document.querySelectorAll('.main-action').forEach(button => {
      button.classList.toggle('voice-selected', button.dataset.action === ({ eating: 'eat', showering: 'bathe', take_meds: 'meds' }[data.command.category]));
    });
    status('Tap to speak again.');
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
    $('#voice-results').hidden = false;
    $('#voice-transcript').textContent = text || '…';
    $('#voice-response').textContent = '…';
    try {
      if (audio) {
        status('Listening back…');
        const data = await api('/api/transcribe', { method: 'POST', headers: { 'Content-Type': audio.type }, body: audio }, signal);
        text = data.transcript;
      }
      await processTranscript(text, id, signal);
    } catch (error) {
      if (!closed && id === sequence) {
        $('#voice-response').textContent = 'I couldn’t complete that request. Please try again.';
        status(error.name === 'TimeoutError' ? 'That took too long. Please try again.' : error.message);
      }
    } finally { if (!closed && id === sequence) setBusy(false); }
  }
  async function control(action) {
    const id = ++sequence; requestController?.abort(); cancelRecording(); setBusy(true);
    $('#voice-results').hidden = false;
    $('#voice-transcript').textContent = action;
    status(`Sending ${action}…`);
    try {
      const data = await api('/api/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }) }, AbortSignal.timeout(15000));
      if (!closed && id === sequence) showResult(data);
    } catch (error) { if (id === sequence) status(`${action} was not confirmed: ${error.message}`); }
    finally { if (!closed && id === sequence) setBusy(false); }
  }
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
      status('Microphone unavailable. You can type a message below.'); return;
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
        microphone(false);
        const blob = new Blob(chunks, { type: mimeType });
        if (!blob.size) { setBusy(false); status('Recording was empty. Please try again.'); return; }
        submit(null, blob);
      };
      recorder.start(); microphone(true);
      status('Listening… Tap to finish.');
      recordingTimer = setTimeout(() => { if (recorder?.state === 'recording') recorder.stop(); releaseMic(); }, 20000);
    } catch (error) {
      releaseMic();
      status(error.name === 'NotAllowedError' ? 'Microphone access was denied. Allow it in the browser or type a command.' : error.message);
    } finally { permissionPending = false; if (!closed) $('#record-voice').disabled = busy; }
  };
  function cleanup() {
    closed = true; ++sequence; cancelRecording(); requestController?.abort();
    dialog.classList.remove('voice-dialog');
    dialog.removeEventListener('close', cleanup);
  }
  dispose = cleanup;
  dialog.addEventListener('close', cleanup, { once: true });
}
