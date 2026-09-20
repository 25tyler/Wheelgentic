import { SerialPort } from 'serialport';
import { createHardwareState, createLineDecoder } from './hardware-state.js';

export function identifyBoard(port) {
  if (port.vendorId?.toLowerCase() !== '2341') return null;
  return ({ '1002': 'Arduino UNO R4 WiFi', '006d': 'Arduino UNO R4 WiFi', '0078': 'Arduino UNO Q' })[port.productId?.toLowerCase()] || null;
}

export function createSerialHardware({ path = '', Port = SerialPort, retryMs = 3000, state = createHardwareState() } = {}) {
  let port, timer, running = false, connecting = false;
  async function connect() {
    if (!running || port || connecting) return;
    connecting = true;
    try {
      const ports = await Port.list();
      const candidates = ports.filter(p => identifyBoard(p) && (!path || p.path === path));
      if (!running) return;
      if (candidates.length !== 1) {
        state.setConnection('disconnected', { port: path || null, board: null, message: candidates.length > 1 ? 'More than one chair is connected. Select ARDUINO_PORT in your server environment.' : 'Connect your chair by USB.' });
        return;
      }
      const chosen = candidates[0];
      state.setConnection('connecting', {port:chosen.path,board:identifyBoard(chosen),message:'Connecting to your chair…'});
      const active = new Port({path:chosen.path,baudRate:115200,autoOpen:false});
      port = active;
      const disconnected = error => {
        if (port !== active) return;
        port = null;
        state.setConnection('disconnected', {message:error ? 'USB connection unavailable. Close Serial Monitor and check the cable.' : 'Chair disconnected. Reconnect the USB cable.'});
        if (active.isOpen) active.close(() => {});
      };
      active.on('data', createLineDecoder(message => {
        if (port === active && state.ingest(message)) state.setConnection('connected', {message:'Chair connected by USB.'});
      }));
      active.on('error', disconnected);
      active.on('close', () => disconnected());
      active.open(error => {
        if (error) { disconnected(error); return; }
        if (!running || port !== active) { if(active.isOpen)active.close(() => {}); return; }
        state.setConnection('connected', {message:'USB connected. Waiting for carechair sensor firmware.'});
      });
    } catch {
      state.setConnection('disconnected', {message:'Could not read the USB device. Check your connection.'});
    } finally { connecting = false; }
  }
  return {
    ...state,
    start() { if(running)return; running=true; void connect(); timer=setInterval(()=>void connect(),retryMs); timer.unref?.(); },
    async stop() {
      running=false; clearInterval(timer); const active=port; port=null;
      if(active?.isOpen) await new Promise(resolve=>active.close(resolve));
      state.setConnection('disconnected', {message:'Sensor connection stopped.'});
    },
  };
}
