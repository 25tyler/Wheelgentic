import { loadEnvFile } from 'node:process';
export function loadEnvironment() {
  try { loadEnvFile(); } catch (error) { if (error.code !== 'ENOENT') throw error; }
}
export function getConfig(env = process.env) {
  return {
    deepgramKey: env.DEEPGRAM_API_KEY, metaKey: env.META_API_KEY, tokenKey: env.TOKEN_COMPANY_API_KEY,
    speechModel: env.DEEPGRAM_TTS_MODEL || 'aura-2-thalia-en',
    metaUrl: env.META_API_URL || 'https://api.meta.ai/v1/chat/completions',
    metaModel: env.META_MODEL || 'muse-spark-1.3',
    compressionEnabled: env.TOKEN_COMPANY_ENABLED !== 'false',
    compressionModel: env.TOKEN_COMPANY_MODEL || 'bear-2', compressionThreshold: 3000,
    robotMode: env.ROBOT_MODE || 'demo', robotUrl: env.ROBOT_BACKEND_URL || '', robotKey: env.ROBOT_API_KEY,
    timeoutMs: 15000, port: Number(env.PORT || 5173),
  };
}
export class AppError extends Error {
  constructor(message, status = 400, code = 'INVALID_REQUEST') {
    super(message); this.status = status; this.code = code;
  }
}
