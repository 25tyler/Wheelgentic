import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { AppError, getConfig, loadEnvironment } from './voice-config.js';
import { createProviders } from './voice-providers.js';
import { createRobotAdapter } from './robot-adapter.js';
import { createVoiceService } from './voice-service.js';

// Never serve .env, server modules, tests, or .git.
const assets = new Map([
  ['/', ['index.html', 'text/html']], ['/index.html', ['index.html', 'text/html']],
  ['/style.css', ['style.css', 'text/css']], ['/app.js', ['app.js', 'text/javascript']],
  ['/voice-client.js', ['voice-client.js', 'text/javascript']],
  ['/voice-speech.js', ['voice-speech.js', 'text/javascript']],
]);
const audioTypes = new Set(['audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/x-wav']);
async function readBody(req, maxBytes) {
  let size = 0; const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > maxBytes) throw new AppError('Request is too large.', 413, 'TOO_LARGE');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}
async function jsonBody(req) {
  if (req.headers['content-type']?.split(';')[0] !== 'application/json') throw new AppError('Use application/json.', 415);
  try { return JSON.parse((await readBody(req, 32768)).toString()); }
  catch (error) { if (error instanceof AppError) throw error; throw new AppError('Invalid JSON body.'); }
}
function json(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  res.end(JSON.stringify(data));
}
export function createApp(config = getConfig(), dependencies = {}) {
  const providers = dependencies.providers || createProviders(config);
  const robot = dependencies.robot || createRobotAdapter(config);
  const voice = createVoiceService(providers, robot);
  const server = http.createServer(async (req, res) => {
    try {
      const host = req.headers.host || '';
      if (!/^(localhost|127\.0\.0\.1)(:\d+)?$/.test(host)) throw new AppError('Unrecognized host.', 403);
      if (req.headers.origin && req.headers.origin !== `http://${host}`) throw new AppError('Cross-origin requests are not allowed.', 403);
      const url = new URL(req.url, `http://${host}`);
      if (req.method === 'GET' && url.pathname === '/api/voice/status') return json(res, 200, {
        deepgram: Boolean(config.deepgramKey), meta: Boolean(config.metaKey),
        compression: Boolean(config.tokenKey && config.compressionEnabled), robotMode: config.robotMode,
      });
      if (req.method === 'POST' && url.pathname === '/api/transcribe') {
        const contentType = req.headers['content-type'] || '';
        if (!audioTypes.has(contentType.split(';')[0])) throw new AppError('Unsupported recording format.', 415);
        const audio = await readBody(req, 5 * 1024 * 1024);
        if (!audio.length) throw new AppError('The recording is empty.', 422);
        return json(res, 200, { transcript: await providers.transcribe(audio, contentType) });
      }
      if (req.method === 'POST' && url.pathname === '/api/command') return json(res, 200, await voice.process(await jsonBody(req)));
      if (req.method === 'POST' && url.pathname === '/api/speak') {
        const body = await jsonBody(req);
        const audio = await providers.synthesize(body?.text);
        res.writeHead(200, { 'Content-Type': 'audio/mpeg', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
        return res.end(audio);
      }
      if (req.method === 'POST' && url.pathname === '/api/stop') {
        const body = await jsonBody(req);
        return json(res, 200, await voice.control(body?.action || 'stop'));
      }
      if (req.method === 'POST' && url.pathname === '/api/task') {
        const body = await jsonBody(req);
        return json(res, 200, { delivery: await voice.task(body?.command) });
      }
      if (req.method === 'GET' && url.pathname === '/api/vitals') return json(res, 200, await robot.vitals());
      if (req.method !== 'GET' || !assets.has(url.pathname)) throw new AppError('Not found.', 404);
      const [file, type] = assets.get(url.pathname);
      const data = await readFile(new URL(file, import.meta.url));
      res.writeHead(200, { 'Content-Type': `${type}; charset=utf-8`, 'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff' });
      res.end(data);
    } catch (error) {
      json(res, error instanceof AppError ? error.status : 500, {
        error: error instanceof AppError ? error.message : 'The server could not complete this request.',
        code: error instanceof AppError ? error.code : 'SERVER_ERROR',
      });
    }
  });
  server.requestTimeout = 30000;
  return server;
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  loadEnvironment(); const config = getConfig();
  createApp(config).listen(config.port, '127.0.0.1', () => console.log(`carechair: http://127.0.0.1:${config.port} (robot: ${config.robotMode})`));
}
