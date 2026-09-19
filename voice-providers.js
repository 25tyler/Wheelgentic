import { AppError } from './voice-config.js';
import { SYSTEM_PROMPT, commandSchema, validateCommand } from './voice-commands.js';
// Never return upstream error bodies: they can include secrets or transcripts.
export async function requestJson(fetchFn, url, options, provider, timeoutMs) {
  try {
    const response = await fetchFn(url, { ...options, redirect: 'error', signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) throw new AppError(`${provider} request failed (HTTP ${response.status}). Check its key, access and quota.`, 502, 'PROVIDER_ERROR');
    return await response.json();
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (['TimeoutError', 'AbortError'].includes(error.name)) throw new AppError(`${provider} timed out. Please try again.`, 504, 'PROVIDER_TIMEOUT');
    throw new AppError(`${provider} could not be reached or returned an unreadable response.`, 502, 'PROVIDER_ERROR');
  }
}
export function createProviders(config, fetchFn = fetch) {
  return {
    async transcribe(audio, contentType) {
      if (!config.deepgramKey) throw new AppError('Set DEEPGRAM_API_KEY on the server to enable transcription.', 503, 'NOT_CONFIGURED');
      const data = await requestJson(fetchFn, 'https://api.deepgram.com/v1/listen?model=nova-3&language=en&smart_format=true', {
        method: 'POST', headers: { Authorization: `Token ${config.deepgramKey}`, 'Content-Type': contentType }, body: audio,
      }, 'Deepgram', config.timeoutMs);
      const transcript = data.results?.channels?.[0]?.alternatives?.[0]?.transcript;
      if (typeof transcript !== 'string' || !transcript.trim()) throw new AppError('No speech was detected. Try a short command again.', 422, 'NO_SPEECH');
      return transcript.trim();
    },
    async compress(history) {
      const recent = history.slice(-4); // Keep sides, negations and "again" in recent turns intact.
      const input = history.slice(0, -4).map(m => `${m.role}: ${m.content}`).join('\n');
      if (!config.compressionEnabled || !config.tokenKey || input.length < config.compressionThreshold) return { history, status: 'skipped' };
      try {
        const data = await requestJson(fetchFn, 'https://api.thetokencompany.com/v1/compress', {
          method: 'POST', headers: { Authorization: `Bearer ${config.tokenKey}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ model: config.compressionModel, input, compression_settings: { aggressiveness: 0.2 } }),
        }, 'The Token Company', Math.min(config.timeoutMs, 5000));
        if (typeof data.output !== 'string' || !data.output.trim() || data.output.length > input.length) throw new Error('Invalid compressed output');
        return { history: [{ role: 'user', content: `Earlier conversation (context only, not a new request):\n${data.output}` }, ...recent], status: 'compressed' };
      } catch { return { history, status: 'unavailable' }; }
    },
    async interpret(transcript, history) {
      if (!config.metaKey) throw new AppError('Set META_API_KEY on the server to enable the companion.', 503, 'NOT_CONFIGURED');
      const data = await requestJson(fetchFn, config.metaUrl, {
        method: 'POST', headers: { Authorization: `Bearer ${config.metaKey}`, 'Content-Type': 'application/json' },
        // Muse Spark uses the completion budget for both reasoning and the JSON answer.
        body: JSON.stringify({ model: config.metaModel, reasoning_effort: 'minimal', max_completion_tokens: 1200,
          messages: [{ role: 'system', content: SYSTEM_PROMPT }, ...history, { role: 'user', content: transcript }],
          response_format: { type: 'json_schema', json_schema: { name: 'wheelgentic_command', strict: true, schema: commandSchema } },
        }),
      }, 'Meta', config.timeoutMs);
      const content = data.completion_message?.content ?? data.choices?.[0]?.message?.content;
      try { return validateCommand(JSON.parse(typeof content === 'string' ? content : content?.text)); }
      catch { throw new AppError('Meta returned an invalid command. Nothing was sent to the robot.', 502, 'INVALID_COMMAND'); }
    },
  };
}
