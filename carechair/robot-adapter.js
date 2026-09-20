import { randomUUID } from 'node:crypto';
import { AppError } from './voice-config.js';
import { validateCommand } from './voice-commands.js';
import { requestJson } from './voice-providers.js';
export function createRobotAdapter(config, fetchFn = fetch) {
  function robotUrl(path) {
    if (config.robotMode !== 'live' || !config.robotUrl) throw new AppError('Robot backend is not configured.', 503, 'ROBOT_NOT_CONFIGURED');
    const url = new URL(path, config.robotUrl);
    if (!['http:', 'https:'].includes(url.protocol)) throw new AppError('Invalid robot backend URL.', 503);
    return url;
  }
  return {
    async dispatch(raw) {
      const command = validateCommand(raw);
      if (command.action === 'none') return { status: 'not_sent', mode: config.robotMode };
      const { category, action, target, item } = command;
      const endpoint = ['stop', 'pause'].includes(action) ? '/api/stop' : '/api/task';
      const payload = { command_id: randomUUID(), category, action, target, item, source: 'voice' };
      if (config.robotMode === 'demo') return { status: 'simulated', mode: 'demo', endpoint, payload };
      const headers = { 'Content-Type': 'application/json', 'Idempotency-Key': payload.command_id };
      if (config.robotKey) headers.Authorization = `Bearer ${config.robotKey}`;
      // No automatic retries: a lost response must not duplicate a physical task.
      const reply = await requestJson(fetchFn, robotUrl(endpoint), { method: 'POST', headers, body: JSON.stringify(payload) }, 'Robot backend', config.timeoutMs);
      // The backend does one thing at a time, and says so rather than failing.
      if (reply?.status === 'refused') return { status: 'refused', mode: 'live', endpoint, payload, reason: String(reply.reason || '').slice(0, 120) };
      return { status: 'accepted', mode: 'live', endpoint, payload };
    },
    async status() {
      if (config.robotMode === 'demo') return { state: 'demo', mode: 'demo', said: [], views: null };
      try {
        return { mode: 'live', ...(await requestJson(fetchFn, robotUrl('/api/status'), { headers: config.robotKey ? { Authorization: `Bearer ${config.robotKey}` } : {} }, 'Robot backend', 3000)) };
      } catch { return { state: 'unreachable', mode: 'live', said: [], views: null }; }
    },
    async vitals() {
      if (config.robotMode === 'demo') return { status: 'disconnected', readings: null, mode: 'demo' };
      return requestJson(fetchFn, robotUrl('/api/vitals'), { headers: config.robotKey ? { Authorization: `Bearer ${config.robotKey}` } : {} }, 'Robot backend', config.timeoutMs);
    },
  };
}
