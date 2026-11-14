// Tests the in-game behaviour (fighting, fleeing, collecting, modes, events) against a simulated bot,
// so the logic that a real server can't easily provoke (monsters, low health, death) is still exercised.
import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import minecraftData from 'minecraft-data'
import { Vec3 } from 'vec3'
import { createRunner, plural } from '../src/runner.js'
import { EventQueue } from '../src/events.js'

const registry = minecraftData('1.21.4')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function makeWorld ({ items = [] } = {}) {
  const bot = new EventEmitter()
  Object.assign(bot, {
    username: 'Remielle',
    version: '1.21.4',
    registry,
    health: 20,
    food: 20,
    isRaining: false,
    time: { isDay: true, timeOfDay: 1000, day: 1 },
    game: { dimension: 'overworld' },
    entity: { id: 1, position: new Vec3(0, 64, 0) },
    entities: {},
    players: {},
    heldItem: null,
    attacks: [],
    chats: [],
    goals: [],
    findBlocksResult: [],
    blocksAt: new Map(),
    pathfinder: {
      goal: null,
      // like the real plugin: a new goal throws away the route AND releases every key
      setGoal (g) { this.goal = g; bot.goals.push(g); bot.clearControlStates() },
      setMovements () {},
      async goto () {}
    },
    collectBlock: { async collect () {}, cancelTask () { return Promise.resolve() } },
    inventory: { items: () => bot._items, slots: [] },
    _items: items,
    attack (e) { bot.attacks.push(e.id) },
    async lookAt () {},
    async equip () {},
    getEquipmentDestSlot () { return 5 },
    chat (m) { bot.chats.push(m) },
    controlLog: [],
    controlState: { forward: false, back: false, left: false, right: false, jump: false, sprint: false, sneak: false },
    setControlState (name, value) { bot.controlLog.push([name, value]); bot.controlState[name] = value },
    getControlState (name) { return bot.controlState[name] },
    clearControlStates () { for (const k of Object.keys(bot.controlState)) bot.setControlState(k, false) },
    // the real pathfinder does this on every physics tick while it owns a route
    simulatePathfinderTick () { bot.setControlState('forward', true); bot.setControlState('jump', false) },
    stopDigging () {},
    findBlocks () { return bot.findBlocksResult },
    findBlock () { return null },
    blockAt (p) { return bot.blocksAt.get(`${p.x},${p.y},${p.z}`) || null },
    // prismarine-physics 1.11.1 ships the double 0.3, which is not the game's float player width
    physics: { playerHalfWidth: 0.3 },
    isABed () { return false },
    async toss () {}
  })
  return bot
}

function setup (opts = {}) {
  const bot = makeWorld(opts)
  const events = new EventQueue()
  const shared = { owner: 'Faazil', home: null, save () {} }
  const settings = { owner: 'Faazil', maxCollect: 64 }
  const runner = createRunner({ bot, settings, events, shared, log: () => {} })
  runner.onSpawn()
  return { bot, events, shared, runner }
}

const item = (name, count, extra = {}) => ({ name, count, type: registry.itemsByName[name]?.id ?? 9999, ...extra })

function addOwner (bot, pos = new Vec3(3, 64, 3)) {
  const entity = { id: 50, type: 'player', username: 'Faazil', position: pos, isValid: true, height: 1.8 }
  bot.entities[50] = entity
  bot.players.Faazil = { username: 'Faazil', entity, uuid: 'u-1' }
  return entity
}

function addMob (bot, id, name, pos, type = 'hostile') {
  const e = { id, name, type, position: pos, isValid: true, height: 1.9 }
  bot.entities[id] = e
  return e
}

test('plural() reads naturally', () => {
  assert.equal(plural('oak_log', 1), 'oak log')
  assert.equal(plural('oak_log', 5), 'oak logs')
  assert.equal(plural('cobblestone', 8), 'cobblestone')
  assert.equal(plural('torch', 2), 'torches')
  assert.equal(plural('coal', 3), 'coal')
})

test('commands never throw, even for nonsense', async () => {
  const { runner } = setup()
  for (const text of ['', '   ', 'blah', 'collect', 'craft', 'goto', 'goto a b c', 'give', 'say', 'attack', 'hunt', 'home', 'come']) {
    const r = await runner.run(text)
    assert.equal(typeof r.message, 'string', text)
    assert.equal(typeof r.ok, 'boolean', text)
  }
})

test('follow needs the owner to be visible, then keeps a follow goal', async () => {
  const { bot, runner } = setup()
  let r = await runner.run('follow')
  assert.equal(r.ok, false)
  assert.match(r.message, /can't see Faazil/)

  addOwner(bot)
  r = await runner.run('follow')
  assert.equal(r.ok, true)
  assert.equal(runner.state.mode, 'follow')
  assert.ok(bot.pathfinder.goal, 'a follow goal is set')

  bot.pathfinder.goal = null // pretend the path was lost
  runner.tick()
  assert.ok(bot.pathfinder.goal, 'tick() re-applies the follow goal')

  r = await runner.run('stay')
  assert.equal(runner.state.mode, 'stay')
  assert.equal(bot.pathfinder.goal, null)
  r = await runner.run('stop')
  assert.equal(runner.state.mode, 'idle')
})

test('attack: swings at a zombie with a cooldown, then reports it gone', async () => {
  const { bot, events, runner } = setup()
  const zombie = addMob(bot, 7, 'zombie', new Vec3(1.5, 64, 0))
  const r = await runner.run('attack zombie')
  assert.equal(r.ok, true)
  assert.match(r.message, /zombie/)
  await sleep(1300)
  assert.ok(bot.attacks.length >= 2 && bot.attacks.length <= 4, `swings: ${bot.attacks.length}`) // ~450 ms apart with bare hands
  assert.ok(bot.attacks.every((id) => id === 7))
  zombie.isValid = false
  delete bot.entities[7]
  await sleep(1500)
  const done = events.items.find((e) => e.type === 'task_done')
  assert.ok(done, JSON.stringify(events.items))
  assert.match(done.message, /zombie is gone/)
  assert.equal(runner.state.task, null)
})

test('attack: a far-away zombie is chased with a follow goal', async () => {
  const { bot, runner } = setup()
  addMob(bot, 8, 'zombie', new Vec3(15, 64, 0))
  await runner.run('attack nearest')
  await sleep(300)
  assert.ok(bot.goals.some((g) => g && g.constructor.name === 'GoalFollow'))
  await runner.run('stop')
})

test('attack refuses creepers and unknown targets politely', async () => {
  const { bot, runner } = setup()
  addMob(bot, 9, 'creeper', new Vec3(4, 64, 0))
  let r = await runner.run('attack creeper')
  assert.equal(r.ok, false)
  assert.match(r.message, /too dangerous/)
  r = await runner.run('attack skeleton')
  assert.equal(r.ok, false)
  assert.match(r.message, /can't see any skeleton/)
  r = await runner.run('attack nearest') // the only monster around is a creeper: not an auto target
  assert.equal(r.ok, false)
})

test('a creeper nearby makes her run away and tell you', async () => {
  const { bot, events, runner } = setup()
  addMob(bot, 10, 'creeper', new Vec3(4, 64, 0))
  runner.tick()
  await sleep(20) // tasks start on the next microtask
  const danger = events.items.find((e) => e.type === 'danger')
  assert.ok(danger, JSON.stringify(events.items))
  assert.equal(danger.speak, true)
  assert.equal(danger.priority, 3)
  assert.equal(runner.state.task?.type, 'flee')
  assert.ok(bot.goals.some((g) => g && g.constructor.name === 'GoalInvert'))
  await runner.run('stop')
})

test('guard mode starts fighting a monster near her owner', async () => {
  const { bot, runner } = setup()
  addOwner(bot)
  await runner.run('guard')
  addMob(bot, 11, 'skeleton', new Vec3(6, 64, 6))
  runner.tick()
  assert.equal(runner.state.task?.type, 'fight')
  await runner.run('stop')
})

test('follow mode only fights when she was just hurt', async () => {
  const { bot, runner } = setup()
  addOwner(bot)
  await runner.run('follow')
  addMob(bot, 12, 'zombie', new Vec3(4, 64, 0))
  runner.tick()
  assert.equal(runner.state.task, null)
  bot.health = 15
  bot.emit('health')
  runner.tick()
  assert.equal(runner.state.task?.type, 'fight')
  await runner.run('stop')
})

test('hostile sightings are announced once, with a speakable warning', () => {
  const { bot, events, runner } = setup()
  addMob(bot, 13, 'zombie', new Vec3(8, 64, 0))
  runner.tick()
  runner.tick()
  const warnings = events.items.filter((e) => e.type === 'hostile_near')
  assert.equal(warnings.length, 1)
  assert.match(warnings[0].message, /zombie/)
})

test('health events: hurt, then low health, are reported (and throttled)', () => {
  const { bot, events } = setup()
  bot.health = 17
  bot.emit('health')
  bot.health = 14
  bot.emit('health')
  assert.equal(events.items.filter((e) => e.type === 'hurt').length, 1) // second hit within 10 s is not repeated
  bot.health = 5
  bot.emit('health')
  const low = events.items.find((e) => e.type === 'low_health')
  assert.ok(low)
  assert.equal(low.priority, 3)
  assert.match(low.message, /5 health/)
})

test('death is reported, commands are refused until she respawns, then she says she is back', async () => {
  const { bot, events, runner } = setup()
  bot.emit('death')
  assert.ok(events.items.find((e) => e.type === 'death'))
  const r = await runner.run('follow')
  assert.equal(r.ok, false)
  bot.emit('spawn')
  assert.ok(events.items.find((e) => e.type === 'respawned'))
  assert.equal((await runner.run('where')).ok, true)
})

test('collect: reports when there is nothing to collect', async () => {
  const { events, runner } = setup()
  const r = await runner.run('collect iron 3')
  assert.equal(r.ok, true)
  assert.match(r.message, /3 raw iron/)
  await sleep(200)
  const failed = events.items.find((e) => e.type === 'task_failed')
  assert.ok(failed, JSON.stringify(events.items))
  assert.match(failed.message, /can't see any raw iron/)
})

test('collect: explains when she needs a pickaxe', async () => {
  const { bot, events, runner } = setup()
  const ironOre = registry.blocksByName.iron_ore
  const pos = new Vec3(5, 60, 5)
  bot.findBlocksResult = [pos]
  bot.blocksAt.set('5,60,5', { name: 'iron_ore', position: pos, harvestTools: { [registry.itemsByName.stone_pickaxe.id]: true, [registry.itemsByName.iron_pickaxe.id]: true }, id: ironOre.id })
  await runner.run('collect iron 2')
  await sleep(200)
  const failed = events.items.find((e) => e.type === 'task_failed')
  assert.ok(failed)
  assert.match(failed.message, /tool that can break/)
})

test('collect: counts what really ended up in the inventory', async () => {
  const { bot, events, runner } = setup()
  const logPos = new Vec3(4, 64, 4)
  bot.findBlocksResult = [logPos]
  bot.blocksAt.set('4,64,4', { name: 'oak_log', position: logPos })
  bot.collectBlock.collect = async () => { bot._items = [item('oak_log', 3)] } // "mining" put 3 logs in the bag
  await runner.run('collect wood 3')
  await sleep(300)
  const done = events.items.find((e) => e.type === 'task_done')
  assert.ok(done, JSON.stringify(events.items))
  assert.match(done.message, /collected 3 oak logs/)
})

test('collect: gives up cleanly when mining makes no progress', async () => {
  const { bot, events, runner } = setup()
  const p = new Vec3(4, 64, 4)
  bot.findBlocksResult = [p]
  bot.blocksAt.set('4,64,4', { name: 'oak_log', position: p })
  let calls = 0
  bot.collectBlock.collect = async () => { calls++ } // never adds anything
  await runner.run('collect wood 3')
  await sleep(400)
  assert.equal(calls, 3)
  const failed = events.items.find((e) => e.type === 'task_failed')
  assert.ok(failed)
  assert.match(failed.message, /can't reach any logs/)
})

test('a new command cancels the running task', async () => {
  const { bot, events, runner } = setup()
  const p = new Vec3(4, 64, 4)
  bot.findBlocksResult = [p]
  bot.blocksAt.set('4,64,4', { name: 'oak_log', position: p })
  bot.collectBlock.collect = () => new Promise((resolve) => setTimeout(resolve, 5000))
  await runner.run('collect wood 5')
  await sleep(100)
  assert.equal(runner.state.task.type, 'collect')
  await runner.run('stop')
  assert.equal(runner.state.task, null)
  await sleep(200)
  assert.equal(events.items.filter((e) => e.type === 'task_failed' || e.type === 'task_done').length, 0, 'a cancelled task stays silent')
})

test('inventory and status read naturally', async () => {
  const { bot, runner } = setup({ items: [item('oak_log', 12), item('cobblestone', 8), item('coal', 3), item('apple', 1)] })
  let r = await runner.run('inventory')
  assert.match(r.message, /12 oak logs/)
  assert.match(r.message, /8 cobblestone/)
  assert.match(r.message, /1 apple/)
  bot._items = []
  r = await runner.run('inventory')
  assert.match(r.message, /empty/)
  addMob(bot, 20, 'zombie', new Vec3(5, 64, 0))
  r = await runner.run('status')
  assert.match(r.message, /20 of 20 health/)
  assert.match(r.message, /zombie/)
  assert.equal(r.data.hostiles[0].name, 'zombie')
})

test('sethome saves and home walks there', async () => {
  const { bot, shared, runner } = setup()
  let saved = 0
  shared.save = () => { saved++ }
  let r = await runner.run('home')
  assert.equal(r.ok, false)
  r = await runner.run('sethome')
  assert.equal(r.ok, true)
  assert.deepEqual(shared.home, { x: 0, y: 64, z: 0 })
  assert.equal(saved, 1)
  let target = null
  bot.pathfinder.goto = async (goal) => { target = goal }
  await runner.run('home')
  await sleep(100)
  assert.ok(target)
})

test('give: walks to the owner and tosses the right amount', async () => {
  const { bot, events, runner } = setup({ items: [item('cobblestone', 10)] })
  addOwner(bot)
  const tossed = []
  bot.toss = async (type, meta, n) => { tossed.push([type, n]) }
  await runner.run('give cobblestone 4')
  await sleep(500)
  assert.deepEqual(tossed, [[registry.itemsByName.cobblestone.id, 4]])
  assert.match(events.items.find((e) => e.type === 'task_done').message, /4 cobblestone/)
  const r = await runner.run('give diamond 1')
  assert.equal(r.ok, false)
})

test('say splits long messages for the chat limit', async () => {
  const { bot, runner } = setup()
  await runner.run('say ' + 'word '.repeat(120))
  assert.ok(bot.chats.length >= 3)
  assert.ok(bot.chats.every((m) => m.length <= 230))
})

test('dusk is announced once per day', () => {
  const { bot, events, runner } = setup()
  bot.time = { isDay: false, timeOfDay: 13200, day: 3 }
  runner.tick()
  runner.tick()
  assert.equal(events.items.filter((e) => e.type === 'night').length, 1)
})

test('unstick: a goal she cannot reach makes her hop, re-plan, then climb over it', async () => {
  const { bot, runner } = setup()
  bot.pathfinder.goal = { x: 10, y: 64, z: 0, range: 1 } // far away, and no path there
  runner.tick() // first look just records where she is
  await sleep(1150)
  runner.tick() // stuck, round 1: ask for a fresh route, then hop
  assert.ok(bot.goals.length >= 1, 'the goal was re-applied for a fresh route')
  // re-planning releases every key, so the hop has to come after it or she never leaves the ground
  assert.equal(bot.getControlState('jump'), true, 'the hop outlasts the re-plan')
  await sleep(1200)
  runner.tick() // still stuck, round 2: face the goal and push over the obstacle
  assert.equal(bot.getControlState('forward'), true, 'she pushes forward')
  assert.equal(bot.getControlState('jump'), true, 'and jumps while pushing')
  await runner.run('stop')
})

test('unstick: a push keeps the keys even while the pathfinder fights her for them', async () => {
  const { bot, runner } = setup()
  bot.pathfinder.goal = { x: 10, y: 64, z: 0, range: 1 }
  runner.tick()
  await sleep(1150)
  runner.tick() // round 1: hop
  await sleep(1200)
  runner.tick() // round 2: the climb push
  bot.simulatePathfinderTick() // the pathfinder releases forward/jump on its own tick
  assert.equal(bot.getControlState('jump'), false, 'the pathfinder did clear her keys')
  await sleep(60) // she presses them again well within the push window
  assert.equal(bot.getControlState('jump'), true, 'the push re-asserts the jump')
  assert.equal(bot.getControlState('forward'), true, 'and the forward')
  await runner.run('stop')
  assert.equal(bot.getControlState('forward'), false, 'stop lets go of everything')
})

test('unstick: standing near the goal, or standing still with no goal, is not stuck', async () => {
  const { bot, runner } = setup()
  runner.tick()
  await sleep(1150)
  runner.tick() // no goal at all: nothing should happen
  bot.pathfinder.goal = { x: 1, y: 64, z: 0, range: 2 } // basically already there
  runner.tick()
  await sleep(1150)
  runner.tick()
  assert.equal(bot.controlLog.length, 0, 'no hops or pushes when she is not stuck')
})

test('unstick: after many failed tries she says she is stuck, once', async () => {
  const { bot, events, runner } = setup()
  bot.pathfinder.goal = { x: 10, y: 64, z: 0, range: 1 }
  runner.tick()
  for (let i = 0; i < 9; i++) { // 8 fixes fail, the 9th tick gives up and speaks
    await sleep(1150)
    runner.tick()
  }
  const stuckEvents = events.items.filter((e) => e.type === 'stuck')
  assert.equal(stuckEvents.length, 1)
  assert.equal(stuckEvents[0].speak, true)
  assert.match(stuckEvents[0].message, /stuck/i)
  await runner.run('stop')
})

test('hitbox: her width matches the server, or the server corrects every jump away', async () => {
  const { bot, runner } = setup()
  assert.equal(bot.physics.playerHalfWidth, Math.fround(0.6) / 2, 'the double 0.3 is replaced by the game float')
  runner.onSpawn() // respawn must not put the wrong width back
  assert.equal(bot.physics.playerHalfWidth, Math.fround(0.6) / 2)
  await runner.run('stop')
})

test('dispose removes the listeners', () => {
  const { bot, runner } = setup()
  const before = bot.listenerCount('health')
  runner.dispose()
  assert.equal(bot.listenerCount('health'), before - 1)
})
