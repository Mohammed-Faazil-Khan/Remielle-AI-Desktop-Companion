// Remielle's Minecraft body. Start it with:  node bot.js   (the launcher does this for you)
//
// It joins your local server as a player, listens to commands on http://127.0.0.1:3100, and keeps trying to
// reconnect if the server restarts. Settings come from the project's .env file (MC_HOST, MC_PORT, MC_OWNER, ...).
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDotEnv, readSettings } from './src/env.js'
import { EventQueue } from './src/events.js'
import { createApi } from './src/api.js'
import { createSession } from './src/session.js'

loadDotEnv()
const settings = readSettings()

const here = path.dirname(fileURLToPath(import.meta.url))
const STATE_FILE = path.join(here, 'bot_state.json')

const stamp = () => new Date().toTimeString().slice(0, 8)
const log = (...args) => console.log(`[${stamp()}]`, ...args)

// things worth remembering between runs: who the owner is and where "home" is
const shared = { owner: settings.owner || null, home: null }
try {
  const saved = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'))
  if (saved.home && Number.isFinite(saved.home.x)) shared.home = saved.home
  if (!shared.owner && saved.owner) shared.owner = saved.owner
} catch { /* first run */ }
shared.save = () => {
  try { fs.writeFileSync(STATE_FILE, JSON.stringify({ owner: shared.owner, home: shared.home }, null, 2)) } catch (e) { log('could not save state:', e.message) }
}

const events = new EventQueue()
let current = null
let stopping = false

// a bug inside a plugin must never take her down
process.on('uncaughtException', (e) => log('uncaught exception:', e?.stack || e))
process.on('unhandledRejection', (e) => log('unhandled rejection:', e?.stack || e))

const api = createApi({ settings, events, getSession: () => current, log })

async function main () {
  try {
    await api.listen()
  } catch (e) {
    if (e.code === 'EADDRINUSE') {
      log(`Port ${settings.apiPort} is already in use. Is another Remielle bot window open? Close it and try again.`)
    } else {
      log('could not start the control API:', e.message)
    }
    process.exit(1)
  }
  log(`Control API ready on http://${settings.apiHost}:${settings.apiPort}`)
  log(`Looking for the Minecraft server at ${settings.host}:${settings.port} (the bot's name: ${settings.username}, owner: ${shared.owner || 'first player to join'})`)

  let delay = settings.reconnectMinMs
  let firstTime = true
  let announcedWaiting = false

  while (!stopping) {
    let session
    try {
      session = createSession({ settings, events, shared, log, firstTime })
      current = session
    } catch (e) {
      log('could not start a session:', e?.stack || e)
      await new Promise((r) => setTimeout(r, delay))
      continue
    }

    const reason = await session.ended
    const wasUp = session.spawned
    current = null
    try { session.bot.end() } catch { /* ignore */ }

    if (wasUp) {
      firstTime = false
      delay = settings.reconnectMinMs
      announcedWaiting = false
      log(`Disconnected (${reason}). Reconnecting...`)
      events.push('disconnected', { message: 'I lost my connection to the world. I will try to get back.', mood: 'sad', priority: 2, speak: true })
    } else {
      if (!announcedWaiting) {
        log(`Waiting for the server (${reason}). I will keep trying every few seconds. Start the Minecraft server if you haven't.`)
        announcedWaiting = true
      }
      delay = Math.min(Math.round(delay * 1.5), settings.reconnectMaxMs)
    }
    await new Promise((r) => setTimeout(r, delay))
  }
}

function shutdown () {
  if (stopping) return
  stopping = true
  log('Shutting down...')
  try { current?.bot?.quit() } catch { /* ignore */ }
  api.close().finally(() => process.exit(0))
  setTimeout(() => process.exit(0), 1500).unref()
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)

main()
