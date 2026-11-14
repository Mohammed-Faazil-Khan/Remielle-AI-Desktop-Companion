import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { createInputSync, inputsOf } from '../src/inputs.js'

const IDLE = { forward: false, back: false, left: false, right: false, jump: false, sprint: false, sneak: false }

function makeBot ({ inputs = true, throws = false } = {}) {
  const bot = new EventEmitter()
  bot.controlState = { ...IDLE }
  bot.writes = []
  bot.supportFeature = (name) => name === 'newPlayerInputPacket' ? inputs : false
  bot._client = {
    write (name, packet) {
      if (throws) throw new Error('not connected')
      bot.writes.push({ name, packet })
    }
  }
  // like mineflayer: the key state is owned by setControlState, and anyone may release it at any moment
  bot.setControlState = (name, value) => { bot.controlState[name] = value }
  return bot
}

const jumpPackets = (bot) => bot.writes.filter((w) => w.packet.inputs.jump)

test('input names map to the 1.21.3+ packet flags', () => {
  const inputs = inputsOf({ ...IDLE, forward: true, jump: true, sneak: true, back: true })
  assert.equal(inputs.forward, true)
  assert.equal(inputs.backward, true)
  assert.equal(inputs.jump, true)
  assert.equal(inputs.shift, true)
  assert.equal(inputs.sprint, false)
})

test('older versions need the old packets, so nothing is sent', () => {
  const bot = makeBot({ inputs: false })
  const sync = createInputSync({ bot })
  assert.equal(sync.active, false)
  bot.setControlState('jump', true)
  bot.emit('physicsTick')
  assert.equal(bot.writes.length, 0)
})

test('a jump is reported the instant the key goes down, without waiting for a tick', () => {
  const bot = makeBot()
  createInputSync({ bot })
  bot.setControlState('jump', true)
  assert.equal(jumpPackets(bot).length, 1)
})

test('a press the pathfinder releases before the next tick still reaches the server', () => {
  const bot = makeBot()
  createInputSync({ bot })
  bot.setControlState('jump', true)
  bot.setControlState('forward', true)
  bot.setControlState('jump', false) // pathfinder's monitorMovement does exactly this every tick
  assert.equal(bot.writes[0].packet.inputs.jump, true, 'the server learns she jumped even though the key lasted no ticks')
  assert.equal(bot.writes.length, 3)
  assert.equal(bot.writes.at(-1).packet.inputs.forward, true)
  assert.equal(bot.writes.at(-1).packet.inputs.jump, false)
})

test('a held key repeats on every tick, like a real client', () => {
  const bot = makeBot()
  createInputSync({ bot })
  bot.setControlState('jump', true)
  for (let i = 0; i < 3; i++) bot.emit('physicsTick')
  assert.equal(jumpPackets(bot).length, 4)
  assert.equal(bot.writes.every((w) => w.name === 'player_input'), true)
})

test('she keeps saying she is moving while a key is down', () => {
  const bot = makeBot()
  createInputSync({ bot })
  bot.setControlState('forward', true)
  for (let i = 0; i < 5; i++) bot.emit('physicsTick')
  assert.equal(bot.writes.length, 6)
  assert.equal(bot.writes.at(-1).packet.inputs.forward, true)
})

test('standing still is quiet: press, release, then nothing', () => {
  const bot = makeBot()
  createInputSync({ bot })
  bot.setControlState('jump', true)
  bot.setControlState('jump', false)
  for (let i = 0; i < 3; i++) bot.emit('physicsTick')
  assert.equal(bot.writes.length, 2)
  assert.equal(bot.writes.at(-1).packet.inputs.jump, false)
})

test('a broken connection is logged, never thrown', () => {
  const bot = makeBot({ throws: true })
  const seen = []
  createInputSync({ bot, log: (m) => seen.push(m) })
  bot.setControlState('jump', true)
  assert.match(seen.join('\n'), /player_input failed/)
})

test('stop gives back the keys and stops talking', () => {
  const bot = makeBot()
  const original = bot.setControlState
  const sync = createInputSync({ bot })
  sync.stop()
  assert.equal(bot.setControlState, original)
  bot.setControlState('jump', true)
  bot.emit('physicsTick')
  assert.equal(bot.writes.length, 0)
})
