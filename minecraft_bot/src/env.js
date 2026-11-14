// Settings. Real environment variables win over the project's .env file (same rule as the Python side).
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
export const PROJECT_ROOT = path.resolve(here, '..', '..')

export function parseDotEnv (text) {
  const out = {}
  for (const raw of String(text || '').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    const i = line.indexOf('=')
    const key = line.slice(0, i).trim()
    let value = line.slice(i + 1).trim()
    if (value.length >= 2 && value[0] === value[value.length - 1] && '"\''.includes(value[0])) {
      value = value.slice(1, -1)
    }
    if (key && value) out[key] = value
  }
  return out
}

export function loadDotEnv (file = path.join(PROJECT_ROOT, '.env'), env = process.env) {
  let text = ''
  try { text = fs.readFileSync(file, 'utf8').replace(/^﻿/, '') } catch { return }
  for (const [k, v] of Object.entries(parseDotEnv(text))) {
    if (env[k] === undefined || env[k] === '') env[k] = v
  }
}

const truthy = (v) => ['1', 'true', 'yes', 'on'].includes(String(v ?? '').trim().toLowerCase())

export function readSettings (env = process.env) {
  const num = (key, fallback) => {
    const raw = env[key]
    if (raw === undefined || String(raw).trim() === '') return fallback
    const n = Number(raw)
    return Number.isFinite(n) ? n : fallback
  }
  return {
    host: String(env.MC_HOST || '127.0.0.1').trim(),
    port: num('MC_PORT', 25565),
    username: String(env.MC_BOT_NAME || 'Remielle').trim(),
    auth: String(env.MC_AUTH || 'offline').trim(),
    version: env.MC_VERSION ? String(env.MC_VERSION).trim() : false, // false = detect from the server
    owner: String(env.MC_OWNER || '').trim(),
    apiHost: '127.0.0.1', // never exposed beyond this PC
    apiPort: num('MC_API_PORT', 3100),
    chatAlways: truthy(env.MC_CHAT_ALWAYS),
    reconnectMinMs: num('MC_RECONNECT_MIN_MS', 3000),
    reconnectMaxMs: num('MC_RECONNECT_MAX_MS', 15000),
    maxCollect: num('MC_MAX_COLLECT', 64)
  }
}
