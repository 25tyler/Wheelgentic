import { createSpeechPlayer } from './voice-speech.js';
let history = [];
let dispose = () => {};
export function disposeVoice() { dispose(); dispose = () => {}; }

export function mountVoice(root) {
  disposeVoice();
  root.innerHTML = `<div class="companion-panel">
    <div class="companion-toolbar"><h3>Talk to Wheelgentic</h3><button id="voice-sound" class="sound-toggle" aria-pressed="true">Voice replies on</button></div>
    <div class="companion-layout">
      <div class="voice-station"><button id="record-voice" class="microphone-button" aria-pressed="false">
        <span class="microphone-symbol" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></svg><span class="microphone-stop"></span></span>
        <span id="record-label">Start microphone</span>
      </button><p id="voice-status" role="status" aria-live="polite">Tap to speak.</p></div>
      <div class="conversation-flow">
        <div class="transcript-card"><span class="voice-speaker">You said</span><p id="voice-transcript">Your words will appear here.</p></div>
        <div class="understanding-card"><span class="understanding-mark" aria-hidden="true">✦</span><div><span class="voice-speaker">I understood</span><p id="voice-understanding">I'll help you find the next step.</p></div></div>
        <div class="reply-card"><div class="reply-heading"><span class="voice-speaker">Wheelgentic</span><button id="voice-replay" class="reply-listen" disabled>Listen again</button></div><p id="voice-response" aria-live="polite">I'm here. What would you like a hand with?</p><span id="speech-status" role="status"></span>
          <div id="voice-suggestions" hidden><span id="suggestion-label"></span><button id="voice-suggestion" class="suggestion-button">Yes, help me with that ↗</button></div>
        </div>
        <form id="voice-form" class="voice-input-row"><input id="voice-text" aria-label="Message Wheelgentic" placeholder="Or type a message…" maxlength="1000" required autocomplete="off"><button id="voice-send" class="voice-send-button" aria-label="Send message">↑</button></form>
      </div>
    </div>
  </div>`;
  const $ = selector => root.querySelector(selector);
  let closed = false, busy = false, permissionPending = false, enabled = true;
  let stream, recorder, recordingTimer, requestController;
  let sequence = 0, discard = false, lastReply = '', speechState = 'idle';
  try { enabled = localStorage.getItem('wheelgentic-spoken-replies') !== 'off'; } catch {}
  const status = text => { if (!closed) $('#voice-status').textContent = text; };
  const speaker = createSpeechPlayer(state => {
    if (closed) return;
    speechState = state;
    $('#voice-replay').textContent = ['loading', 'speaking'].includes(state) ? 'Stop speaking' : 'Listen again';
    $('#speech-status').textContent = ({loading:'Preparing voice…',speaking:'Speaking…',blocked:'Tap Listen again to hear this reply.',error:'Audio unavailable. You can still read the reply.'})[state] || '';
  });
  function updateSound() {
    $('#voice-sound').textContent = enabled ? 'Voice replies on' : 'Voice replies off';
    $('#voice-sound').setAttribute('aria-pressed', String(enabled));
  }
  updateSound();
  function microphone(recording) {
    if (closed) return;
    $('#record-label').textContent = recording ? 'Stop microphone' : 'Start microphone';
    $('#record-voice').classList.toggle('is-recording', recording);
    $('#record-voice').setAttribute('aria-pressed', String(recording));
  }
  function releaseMic() { stream?.getTracks().forEach(track => track.stop()); stream = null; clearTimeout(recordingTimer); }
  function setBusy(value) {
    busy = value;
    if (closed) return;
    $('#record-voice').disabled = value || permissionPending;
    $('#voice-send').disabled = value || permissionPending || recorder?.state === 'recording';
    $('#voice-replay').disabled = value || permissionPending || recorder?.state === 'recording' || !lastReply;
    $('#voice-suggestion').disabled = value || permissionPending || recorder?.state === 'recording';
  }
  function cancelRecording() {
    discard = true;
    if (recorder?.state === 'recording') recorder.stop();
    releaseMic(); microphone(false);
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
    lastReply = data.response;
    $('#voice-response').textContent = lastReply;
    $('#voice-understanding').textContent = data.understanding || 'Your request is understood.';
    const suggestions = { eating: 'Eating & drinking', showering: 'Showering', take_meds: 'Medication assistance' };
    const suggested = data.command.action === 'none' && suggestions[data.suggestion];
    $('#voice-suggestions').hidden = !suggested;
    $('#suggestion-label').textContent = suggested || '';
    document.querySelectorAll('.main-action').forEach(button => {
      button.classList.toggle('voice-selected', button.dataset.action === ({ eating: 'eat', showering: 'bathe', take_meds: 'meds' }[data.command.category]));
    });
    status('Tap to speak again.');
    if (enabled) void speaker.speak(lastReply);
  }
  async function submit(text, audio) {
    speaker.stop();
    requestController?.abort(); cancelRecording();
    const id = ++sequence;
    requestController = new AbortController();
    const signal = AbortSignal.any([requestController.signal, AbortSignal.timeout(45000)]);
    lastReply = ''; setBusy(true);
    $('#voice-transcript').textContent = text || '…';
    $('#voice-understanding').textContent = '…';
    $('#voice-response').textContent = '…';
    $('#voice-suggestions').hidden = true;
    try {
      if (audio) {
        status('Listening back…');
        const data = await api('/api/transcribe', { method:'POST', headers:{'Content-Type':audio.type}, body:audio }, signal);
        text = data.transcript;
      }
      if (closed || id !== sequence) return;
      $('#voice-transcript').textContent = text;
      status('Thinking…');
      const data = await api('/api/command', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({transcript:text,history})},signal);
      if (closed || id !== sequence) return;
      showResult(data);
      history = [...history,{role:'user',content:text},{role:'assistant',content:data.response}].slice(-20);
    } catch (error) {
      if (!closed && id === sequence) {
        $('#voice-understanding').textContent = 'Your request hasn’t been confirmed.';
        $('#voice-response').textContent = 'I couldn’t complete that request. Please try again.';
        status(error.name === 'TimeoutError' ? 'That took too long. Please try again.' : error.message);
      }
    } finally { if (!closed && id === sequence) setBusy(false); }
  }
  $('#voice-form').onsubmit = event => {
    event.preventDefault();
    const text = $('#voice-text').value.trim();
    const control = /^(?:please\s+)?(?:stop|pause)(?:\s+(?:now|everything|the robot|the chair|please))?[.!?]*$/i.test(text);
    if (!text || (!control && (busy || permissionPending || recorder?.state === 'recording'))) return;
    $('#voice-text').value = ''; void submit(text);
  };
  $('#voice-suggestion').onclick = () => {
    if (!busy && !permissionPending && recorder?.state !== 'recording' && !$('#voice-suggestions').hidden) void submit('Yes, please help me with that.');
  };
  $('#voice-sound').onclick = () => {
    enabled = !enabled; updateSound();
    try { localStorage.setItem('wheelgentic-spoken-replies',enabled?'on':'off'); } catch {}
    if (!enabled) speaker.stop();
  };
  $('#voice-replay').onclick = () => {
    if (busy || permissionPending || recorder?.state === 'recording' || !lastReply) return;
    if (['loading','speaking'].includes(speechState)) speaker.stop();
    else void speaker.speak(lastReply);
  };
  $('#record-voice').onclick = async () => {
    if (recorder?.state === 'recording') { setBusy(true); recorder.stop(); releaseMic(); return; }
    if (busy || permissionPending) return;
    speaker.stop();
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      status('Microphone unavailable. You can type a message below.'); return;
    }
    const startSequence = ++sequence;
    permissionPending = true; setBusy(false);
    status('Waiting for microphone access…');
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio:true});
      if (closed || startSequence !== sequence) { acquired.getTracks().forEach(t=>t.stop()); return; }
      stream = acquired;
      const mimeType = ['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus'].find(type=>MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error('Microphone unavailable. Please type a message.');
      recorder = new MediaRecorder(stream,{mimeType});
      const chunks = []; discard = false;
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => { cancelRecording(); setBusy(false); status('Recording failed. Please try again.'); };
      recorder.onstop = () => {
        if (closed || discard || startSequence !== sequence) return;
        releaseMic(); microphone(false);
        const blob = new Blob(chunks,{type:mimeType});
        if (!blob.size) { setBusy(false); status('Recording was empty. Please try again.'); return; }
        void submit(null,blob);
      };
      recorder.start(); microphone(true); status('Listening… Tap to finish.');
      recordingTimer = setTimeout(()=>{if(recorder?.state==='recording')recorder.stop();releaseMic();},20000);
    } catch (error) {
      releaseMic();
      status(error.name==='NotAllowedError'?'Microphone access was denied. Allow it in the browser or type a message.':error.message);
    } finally { permissionPending=false; if(!closed)setBusy(busy); }
  };
  function suspend() {
    ++sequence; requestController?.abort(); cancelRecording(); speaker.stop();
    if (busy) { $('#voice-response').textContent='Your conversation is paused. Try your request again when you’re ready.'; lastReply=''; }
    setBusy(false); status('Tap to speak.');
  }
  function cleanup() { if (closed) return; suspend(); speaker.dispose(); closed=true; }
  dispose=cleanup;
  return { suspend, dispose:cleanup, focus(){ $('#record-voice').focus({preventScroll:true}); } };
}
