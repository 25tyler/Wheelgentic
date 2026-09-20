import { EventEmitter } from 'node:events';

export const STALE_MS = 5000;
export function createHardwareState(now = Date.now) {
  const events = new EventEmitter();
  let connection = { status: 'disconnected', port: null, board: null, message: 'Connect your chair by USB.' };
  let sample = null, receivedAt = null, measuredAt = null, previousButtons = null, sequence = null, uptime = null;
  let buttonCount = [0, 0, 0];
  function snapshot() {
    const current = now();
    const streaming = connection.status === 'connected' && receivedAt !== null && current - receivedAt < STALE_MS;
    const fresh = streaming && measuredAt !== null && current - measuredAt < STALE_MS && sample?.temperature_c !== null;
    return {
      connection: { ...connection, streaming },
      temperature: {
        kind: 'ambient', sensor: 'Modulino Thermo (HS3003)', status: fresh ? 'live' : measuredAt !== null ? 'stale' : 'waiting',
        celsius: fresh ? sample.temperature_c : null,
        fahrenheit: fresh ? sample.temperature_c * 9 / 5 + 32 : null,
        humidity: fresh ? sample.humidity_pct : null,
        measuredAt: measuredAt === null ? null : new Date(measuredAt).toISOString(),
      },
      buttons: { connected: streaming && Array.isArray(sample?.buttons), pressed: streaming ? sample?.buttons ?? null : null, counts: [...buttonCount] },
      addresses: streaming ? sample?.i2c_addresses ?? [] : [],
    };
  }
  function setConnection(status, detail = {}) {
    connection = { ...connection, ...detail, status };
    if (status !== 'connected') {
      previousButtons = null; sequence = null; uptime = null;
      sample = null; receivedAt = null; measuredAt = null;
    }
    events.emit('snapshot', snapshot());
  }
  function ingest(value) {
    if (!value || value.protocol !== 'carechair/1') return false;
    if (value.type === 'boot' && value.firmware === 'carechair-sensors/1') {
      previousButtons=null; sequence=null; uptime=null; receivedAt=null; measuredAt=null; sample=null;
      events.emit('snapshot',snapshot()); return true;
    }
    if (value.type !== 'sample') return false;
    const { seq, uptime_ms, temperature_c, humidity_pct, temperature_age_ms, buttons, i2c_addresses } = value;
    const uint = n => Number.isInteger(n) && n >= 0 && n <= 0xffffffff;
    const number = (n, min, max) => n === null || (typeof n === 'number' && Number.isFinite(n) && n >= min && n <= max);
    if (!uint(seq) || !uint(uptime_ms) || !number(temperature_c, -40, 125) || !number(humidity_pct, 0, 100) ||
        !(temperature_age_ms === null || uint(temperature_age_ms)) ||
        !(buttons === null || (Array.isArray(buttons) && buttons.length === 3 && buttons.every(b => typeof b === 'boolean'))) ||
        !Array.isArray(i2c_addresses) || i2c_addresses.length > 112 || !i2c_addresses.every(n => Number.isInteger(n) && n >= 8 && n <= 119) ||
        (temperature_c !== null && (temperature_age_ms === null || !i2c_addresses.includes(0x44))) ||
        (buttons !== null && !i2c_addresses.includes(0x3e))) return false;
    const current = now();
    // Ordered USB data: repeated/old frames cannot replay a button press. A reset establishes a new baseline.
    if (sequence !== null && ((seq - sequence) >>> 0) > 0x7fffffff) return false;
    if (sequence === seq) return false;
    if (receivedAt === null || current - receivedAt >= STALE_MS) previousButtons = null;
    sequence = seq; uptime = uptime_ms; receivedAt = current;
    measuredAt = temperature_c === null ? null : current - temperature_age_ms;
    sample = { temperature_c, humidity_pct, buttons, i2c_addresses: [...new Set(i2c_addresses)] };
    const pressed = [];
    if (buttons && previousButtons) buttons.forEach((down, index) => {
      if (down && !previousButtons[index]) { buttonCount[index]++; pressed.push(index + 1); }
    });
    previousButtons = buttons ? [...buttons] : null;
    events.emit('snapshot', snapshot());
    pressed.forEach(button => events.emit('button', { button, receivedAt: current }));
    return true;
  }
  return { snapshot, setConnection, ingest, events };
}

export function createLineDecoder(onMessage, maxLength = 2048) {
  let buffer = '', dropping = false;
  return chunk => {
    for (const character of chunk.toString('utf8')) {
      if (character === '\n') {
        if (!dropping && buffer.trim()) { try { onMessage(JSON.parse(buffer)); } catch {} }
        buffer = ''; dropping = false;
      } else if (!dropping) {
        if (buffer.length >= maxLength) { buffer = ''; dropping = true; }
        else buffer += character;
      }
    }
  };
}
