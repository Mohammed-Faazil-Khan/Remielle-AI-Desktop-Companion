// One connection to the Minecraft server. When it ends (kicked, server closed, network), bot.js starts a new one.
import mineflayer from 'mineflayer'
import pathfinderPkg from 'mineflayer-pathfinder'
import collectPkg from 'mineflayer-collectblock'
import { loader as autoEat } from 'mineflayer-auto-eat'
import { createRunner } from './runner.js'
import { createInputSync } from './inputs.js'

const { pathfinder } = pathfinderPkg
const { plugin: collectBlock } = collectPkg

const LOGIN_TIMEOUT_MS = 90000

export function createSession ({ settings, events, shared, log, firstTime }) {
  const options = {
    host: settings.host,
    port: settings.port,
    username: settings.username,
    auth: settings.auth,
    checkTimeoutInterval: 60000
  }
  if (settings.version) options.version = settings.version

  const bot = mineflayer.createBot(options)
  bot.loadPlugin(pathfinder)
  bot.loadPlugin(collectBlock)

  const session = { bot, runner: null, spawned: false, endedReason: null, ended: null }
  let tickTimer = null
  let loginTimer = null
  let inputSync = null
  let finished = false
  let greeted = false

  const ownerName = () => shared.owner || settings.owner || null
  const isOwner = (name) => !!ownerName() && String(name).toLowerCase() === ownerName().toLowerCase()

  session.ended = new Promise((resolve) => {
    const finish = (reason) => {
      if (finished) return
      finished = true
      session.endedReason = String(reason || 'ended')
      clearInterval(tickTimer)
      clearTimeout(loginTimer)
      try { inputSync?.stop() } catch { /* ignore */ }
      try { session.runner?.dispose() } catch { /* ignore */ }
      resolve(session.endedReason)
    }
    session.finish = finish
    bot.once('end', (reason) => finish(reason))
    bot.on('error', (e) => {
      log(`bot error: ${e?.code || ''} ${e?.message || e}`)
      // a refused / reset connection is followed by 'end' on most paths; make sure we never hang on it
      if (['ECONNREFUSED', 'ECONNRESET', 'ENOTFOUND', 'ETIMEDOUT', 'EHOSTUNREACH'].includes(e?.code)) {
        setTimeout(() => finish(e.code), 500)
      }
    })
    loginTimer = setTimeout(() => {
      if (!session.spawned) {
        log('login timed out, trying again')
        try { bot.end('login timeout') } catch { /* ignore */ }
        finish('login timeout')
      }
    }, LOGIN_TIMEOUT_MS)
  })

  bot.on('kicked', (reason) => {
    let text = ''
    try { text = typeof reason === 'string' ? reason : JSON.stringify(reason) } catch { /* ignore */ }
    log(`kicked: ${text}`)
    events.push('kicked', { message: 'I got kicked out of the world.', mood: 'sad', priority: 2, speak: true, reason: text.slice(0, 300) })
  })

  bot.once('spawn', () => {
    session.spawned = true
    clearTimeout(loginTimer)
    try {
      bot.loadPlugin(autoEat)
      bot.autoEat.setOpts({
        priority: 'foodPoints',
        bannedFood: ['rotten_flesh', 'pufferfish', 'spider_eye', 'poisonous_potato', 'chorus_fruit', 'suspicious_stew']
      })
      bot.autoEat.enableAuto()
      bot.autoEat.on('eatFail', (e) => log('eat failed:', e?.message || e))
    } catch (e) {
      log('auto-eat is unavailable:', e?.message || e)
    }

    const runner = createRunner({ bot, settings, events, shared, log })
    session.runner = runner
    runner.onSpawn()
    inputSync = createInputSync({ bot, log })
    tickTimer = setInterval(() => {
      try { runner.tick() } catch (e) { log('tick failed:', e?.stack || e) }
    }, 400)

    log(`spawned in the world as ${bot.username} (Minecraft ${bot.version})`)
    const present = Object.keys(bot.players || {}).filter((n) => n !== bot.username)
    if (!ownerName() && present.length) { shared.owner = present[0]; shared.save?.() }
    const ownerHere = ownerName() && present.some((n) => n.toLowerCase() === ownerName().toLowerCase())
    greeted = !!ownerHere
    events.push('spawned', {
      message: ownerHere
        ? (firstTime ? `I'm in the world, and I can see you! What should we do first?` : "I'm back in the world! Where were we?")
        : (firstTime ? "I'm in the world now! Come and join me." : "I'm back in the world."),
      mood: 'happy', priority: 2, speak: true
    })
  })

  bot.on('playerJoined', (player) => {
    if (!player || player.username === bot.username) return
    if (!ownerName()) { shared.owner = player.username; shared.save?.() }
    if (isOwner(player.username) && !greeted) {
      greeted = true
      events.push('owner_joined', { message: `You're here! I've been waiting for you, ${player.username}!`, mood: 'happy', priority: 2, speak: true })
    }
  })

  bot.on('playerLeft', (player) => {
    if (!player || !isOwner(player.username)) return
    greeted = false
    if (session.runner) { session.runner.cancelTask(); session.runner.state.mode = 'stay' }
    events.push('owner_left', { message: 'You left the world... I will wait for you here.', mood: 'sad', priority: 1, speak: false })
  })

  const onChat = async (username, message) => {
    try {
      if (!username || username === bot.username) return
      const text = String(message || '').trim()
      if (!text) return
      if (!ownerName()) { shared.owner = username; shared.save?.() }
      const fromOwner = isOwner(username)

      // "!follow", "!collect oak_log 10": typed commands, no voice or AI needed
      if (text.startsWith('!')) {
        if (!fromOwner || !session.runner) return
        const result = await session.runner.run(text.slice(1))
        session.runner.say(result.message)
        return
      }

      const addressed = /\b(?:remielle|remi)\b/i.test(text) || settings.chatAlways
      if (addressed) events.push('chat', { player: username, text, isOwner: fromOwner, priority: 3, speak: false })
    } catch (e) {
      log('chat handler failed:', e?.stack || e)
    }
  }
  bot.on('chat', onChat)
  bot.on('whisper', onChat)

  return session
}
