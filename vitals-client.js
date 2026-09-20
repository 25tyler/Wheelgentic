export function explainTemperature(celsius) {
  if (typeof celsius !== 'number' || !Number.isFinite(celsius)) return {title:'Waiting for a reading',text:'Your room temperature will appear when the chair is connected.',tone:'neutral'};
  if(celsius < 20) return {title:'On the cooler side',text:'If you feel chilly, an extra layer or a warmer room may help you feel more comfortable.',tone:'cool'};
  if(celsius > 26) return {title:'On the warmer side',text:'If you feel warm, a cooler spot or gentle airflow may be more comfortable.',tone:'warm'};
  return {title:'Within the comfort guide',text:'The air around your chair is within our 20–26°C comfort guide. How you feel matters too.',tone:'mild'};
}

export function mountVitals(root, onMicrophoneToggle, dependencies = {}) {
  const EventStream = dependencies.EventSource || globalThis.EventSource;
  const doc = dependencies.document || document;
  const now = dependencies.now || Date.now;
  root.innerHTML = `<div class="temperature-panel">
    <div class="temperature-top"><div><span class="temperature-label">Around your chair</span><h3>Room temperature</h3></div><span id="temperature-status" class="sensor-status">Waiting for chair</span></div>
    <div class="temperature-body"><div class="temperature-reading"><div><span id="temperature-value">—</span><span class="temperature-unit">°C</span></div><p><span id="temperature-fahrenheit">—</span> °F <span class="temperature-divider">·</span> <span id="temperature-humidity">—</span>% humidity</p><small id="temperature-updated">No reading yet</small></div>
    <div class="temperature-explanation"><span class="temperature-icon" aria-hidden="true">∿</span><div><h4 id="temperature-meaning">Waiting for a reading</h4><p id="temperature-advice">Connect your chair by USB to see the temperature around you.</p></div></div></div>
    <div class="temperature-bottom"><p>Measures the air around you, not body temperature. The 20–26°C guide describes comfort, not a medical range.</p><span id="hardware-button-hint">Button A · microphone on / off</span></div>
    <details class="sensor-details"><summary>Chair connection</summary><p id="sensor-connection">Checking your chair…</p><div id="sensor-modules"></div><div class="physical-buttons"><span id="physical-button-1">A · microphone</span><span id="physical-button-2">B · unassigned</span><span id="physical-button-3">C · unassigned</span></div></details>
  </div>`;
  const $ = selector => root.querySelector(selector);
  let stream, disposed = false, lastButtonTime = 0, lastSnapshot = null, lastReceived = 0;
  function unavailable(message) {
    $('#temperature-status').textContent = message;
    $('#temperature-status').className = 'sensor-status';
    $('#temperature-value').textContent = '—'; $('#temperature-fahrenheit').textContent = '—'; $('#temperature-humidity').textContent = '—';
    $('#temperature-meaning').textContent='Waiting for a fresh reading';
    $('#temperature-advice').textContent='Check the USB cable and sensor connections.';
    for(let i=1;i<=3;i++)$('#physical-button-'+i).classList.toggle('pressed',false);
  }
  function render(value) {
    const {temperature,connection,buttons,addresses} = value;
    if(!temperature || !connection || !buttons || !Array.isArray(addresses))return;
    lastSnapshot=value; lastReceived=now();
    const live=temperature.status==='live' && Number.isFinite(temperature.celsius);
    const meaning=explainTemperature(live?temperature.celsius:null);
    if(live) {
      $('#temperature-value').textContent=temperature.celsius.toFixed(1);
      $('#temperature-fahrenheit').textContent=temperature.fahrenheit.toFixed(1);
      $('#temperature-humidity').textContent=Number.isFinite(temperature.humidity)?temperature.humidity.toFixed(0):'—';
      $('#temperature-status').textContent='Live'; $('#temperature-status').className='sensor-status live';
      $('#temperature-meaning').textContent=meaning.title; $('#temperature-advice').textContent=meaning.text;
    } else unavailable(temperature.status==='stale'?'Reading paused':connection.status==='connected'?'Waiting for sensor':'Chair disconnected');
    $('#temperature-updated').textContent=temperature.measuredAt?'Last reading '+new Date(temperature.measuredAt).toLocaleTimeString([],{hour:'numeric',minute:'2-digit',second:'2-digit'}):'No reading yet';
    $('#sensor-connection').textContent=connection.message+(connection.port?' · '+connection.port:'')+(connection.board?' · '+connection.board:'');
    $('#sensor-modules').textContent=`Thermo: ${live?'reading':addresses.includes(68)?'responding; waiting for a valid reading':'not detected'} · Buttons: ${buttons.connected?'reading':'not detected'}`;
    for(let i=1;i<=3;i++)$('#physical-button-'+i).classList.toggle('pressed',Boolean(buttons.pressed?.[i-1]));
  }
  function connect() {
    stream?.close(); stream=null;
    if(disposed || doc.hidden)return;
    stream=new EventStream('/api/hardware/events');
    stream.addEventListener('snapshot',event=>{try{render(JSON.parse(event.data));}catch{unavailable('Waiting for sensor');}});
    stream.addEventListener('button',event=>{
      if(doc.hidden || disposed)return;
      let value;try{value=JSON.parse(event.data);}catch{return;}
      if(value.button!==1 || !Number.isFinite(value.receivedAt) || Math.abs(now()-value.receivedAt)>2000 || value.receivedAt-lastButtonTime<250)return;
      lastButtonTime=value.receivedAt; onMicrophoneToggle();
    });
    stream.onerror=()=>{if(!disposed)unavailable('Connection interrupted');};
  }
  const timer=setInterval(()=>{
    if(lastSnapshot && (now()-lastReceived>5000 || (lastSnapshot.temperature.measuredAt && now()-Date.parse(lastSnapshot.temperature.measuredAt)>5000))) unavailable('Reading paused');
  },1000);
  doc.addEventListener('visibilitychange',connect);
  connect();
  return {dispose(){disposed=true;clearInterval(timer);stream?.close();doc.removeEventListener('visibilitychange',connect);}};
}
