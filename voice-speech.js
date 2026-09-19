// A reply can play only while it is the current reply. Starting the mic cancels it.
export function createSpeechPlayer(onState, dependencies = {}) {
  const fetchFn = dependencies.fetch || fetch;
  const makeAudio = dependencies.makeAudio || (() => new Audio());
  const urls = dependencies.urls || URL;
  let generation = 0, controller, audio, objectUrl, disposed = false;
  function clear() {
    controller?.abort(); controller = null;
    if (audio) { audio.onended = null; audio.onerror = null; audio.pause(); audio.removeAttribute('src'); audio.load(); audio = null; }
    if (objectUrl) { urls.revokeObjectURL(objectUrl); objectUrl = null; }
  }
  function stop() { generation++; clear(); if (!disposed) onState('idle'); }
  async function speak(text) {
    if (disposed || !text) return;
    stop(); const id = generation;
    controller = new AbortController(); onState('loading');
    try {
      const response = await fetchFn('/api/speak', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
      });
      if (!response.ok || !response.headers.get('content-type')?.startsWith('audio/')) throw new Error('Speech unavailable');
      const blob = await response.blob();
      if (disposed || id !== generation) return;
      if (!blob.size) throw new Error('Empty speech');
      objectUrl = urls.createObjectURL(blob); audio = makeAudio(); audio.src = objectUrl;
      const finish = state => { if (!disposed && id === generation) { clear(); onState(state); } };
      audio.onended = () => finish('idle');
      audio.onerror = () => finish('error');
      await audio.play();
      if (!disposed && id === generation && audio) onState('speaking');
    } catch (error) {
      if (!disposed && id === generation) { clear(); onState(error.name === 'NotAllowedError' ? 'blocked' : 'error'); }
    }
  }
  return { speak, stop, dispose() { stop(); disposed = true; } };
}
