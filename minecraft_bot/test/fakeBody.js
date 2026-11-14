// A fake Minecraft body for the perception and action tests.
//
// It is deliberately picky about WHEN things become true. mount() does not put her on the boat - the
// server does, a moment later, and an action that assumed the packet worked is exactly the bug that
// made 43 tests pass while she hung in mid-air. Same for digging, placing and walking: the world map
// only changes when the server would say so.
import { EventEmitter } from 'node:events'
import { createRequire } from 'node:module'
import minecraftData from 'minecraft-data'
import { Vec3 } from 'vec3'

const require = createRequire(import.meta.url)
export const registry = minecraftData('1.21.11')
const { Recipe } = require('prismarine-recipe')(registry)

const key = (p) => `${Math.floor(p.x)},${Math.floor(p.y)},${Math.floor(p.z)}`

export function makeBody ({ items = [], blocks = {}, version = '1.21.11' } = {}) {
  const bot = new EventEmitter()
  const world = new Map(Object.entries(blocks))
  const log = { mounts: [], uses: [], digs: [], places: [], chats: [], steers: [], looks: [], attacks: [], activated: [] }
  const held = { current: null }

  Object.assign(bot, {
    username: 'Remielle',
    version,
    registry,
    health: 20,
    food: 20,
    isRaining: false,
    time: { isDay: true, timeOfDay: 1000, day: 1 },
    game: { dimension: 0 },
    entity: { id: 1, position: new Vec3(0, 64, 0), yaw: 0, onGround: true, isInWater: false },
    entities: {},
    players: {},
    vehicle: null,
    controlState: { forward: false, back: false, left: false, right: false, jump: false, sprint: false, sneak: false },
    inventory: {
      items: () => held.items,
      slots: {},
      count: (id) => held.items.filter((i) => registry.itemsByName[i.name]?.id === id).reduce((a, b) => a + b.count, 0)
    },
    log,
    world
  })
  held.items = items.map((i) => ({ ...i }))
  Object.defineProperty(bot, 'heldItem', { get: () => held.current })

  bot.blockAt = (p) => {
    if (!p) return null
    const name = world.get(key(p))
    const id = name ? registry.blocksByName[name]?.id : registry.blocksByName.air?.id
    return { name: name || 'air', position: new Vec3(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z)), id, states: {} }
  }
  bot.findBlock = ({ matching, maxDistance = 32 } = {}) => {
    for (const [k, name] of world) {
      if (typeof matching === 'function' && !matching(bot.blockAt(new Vec3(...k.split(',').map(Number))))) continue
      if (typeof matching === 'number' && registry.blocksByName[name]?.id !== matching) continue
      const p = new Vec3(...k.split(',').map(Number))
      if (p.distanceTo(bot.entity.position) <= maxDistance) return p
    }
    return null
  }
  bot.findBlocks = (opts = {}) => {
    const out = []
    for (const [k, name] of world) {
      const p = new Vec3(...k.split(',').map(Number))
      if (p.distanceTo(bot.entity.position) > (opts.maxDistance || 16)) continue
      if (typeof opts.matching === 'function' && !opts.matching(bot.blockAt(p))) continue
      if (typeof opts.matching === 'number' && registry.blocksByName[name]?.id !== opts.matching) continue
      out.push(p)
      if (out.length >= (opts.count || 64)) break
    }
    return out
  }
  bot.setControlState = (name, value) => { bot.controlState[name] = value }
  bot.getControlState = (name) => bot.controlState[name]
  bot.clearControlStates = () => { for (const k of Object.keys(bot.controlState)) bot.controlState[k] = false }
  bot.getEquipmentDestSlot = (slot) => ({ head: 103, torso: 102, legs: 101, feet: 100 })[slot] || 36
  bot.chat = (m) => log.chats.push(m)
  bot.swingArm = () => {}
  bot.attack = (e) => { log.attacks.push(e?.name || e) }
  bot.look = async (yaw) => { log.looks.push(yaw); bot.entity.yaw = yaw }
  bot.lookAt = async (p) => { log.looks.push(p) }
  bot.toss = async () => {}
  bot.isABed = (b) => /_bed$/.test(b?.name || '')

  bot.equip = async (item, dest = 'hand') => {
    if (!item) throw new Error('Nothing to equip')
    if (dest === 'hand') held.current = { name: item.name, count: item.count || 1 }
    else bot.inventory.slots[bot.getEquipmentDestSlot(dest)] = { name: item.name }
  }
  bot.recipesAll = (id, meta = 0, table = false) => Recipe.find(id, meta).filter((r) => !r.requiresTable || table)
  bot.recipesFor = (id, meta = 0, count = 1, table = false) => bot.recipesAll(id, meta, table).filter((r) => {
    if (r.requiresTable && !table) return false
    return r.delta.every((d) => bot.inventory.count(d.id) + d.count * Math.ceil(count / r.result.count) >= 0)
  })

  // The server, not the bot, decides when she is riding. Confirm on the next tick, like a real one.
  bot.mount = (e) => {
    log.mounts.push(e?.name || e)
    if (!e || e.noSit) return
    setTimeout(() => { bot.vehicle = e; bot.emit('mount') }, 5)
  }
  bot.dismount = () => {
    if (!bot.vehicle) return
    const v = bot.vehicle
    setTimeout(() => { bot.vehicle = null; bot.emit('dismount', v) }, 5)
  }
  bot.useOn = (e) => log.uses.push(e?.name || String(e))
  bot.moveVehicle = (left, forward) => log.steers.push([left, forward])
  bot.activateBlock = async (b) => { log.activated.push(b?.name); return { err: null } }
  bot.activateItem = async () => { log.activated.push(held.current?.name || 'hand') }
  bot.digTime = (b) => (b?.name === 'diamond_ore' ? 60000 : 900)
  bot.dig = async (b) => {
    if (b?.name === 'bedrock') throw new Error('Cannot harvest bedrock')
    log.digs.push(b?.name)
    world.delete(key(b.position))
  }
  bot.placeBlock = async (reference, face) => {
    if (!held.current) throw new Error('Dont have item')
    const at = reference.position.plus(face)
    world.set(key(at), held.current.name)
    log.places.push(held.current.name)
    held.current.count -= 1
    if (held.current.count <= 0) held.current = null
  }
  bot.placeEntity = async (reference, face) => { log.places.push('entity:' + (held.current?.name || '')) }

  bot.pathfinder = {
    goal: null,
    setGoal (g) { this.goal = g },
    setMovements () {},
    bestHarvestTool: () => null,
    // walking is the thing the planner does most, so make it real: she arrives, or there is no path
    async goto (goal) {
      const to = goal?.x !== undefined ? new Vec3(goal.x, goal.y ?? 64, goal.z) : goal?.entity?.position
      if (goal?.blocked) throw new Error('no path')
      if (to) {
        const d = to.distanceTo(bot.entity.position)
        const range = goal.range || 1
        if (d > range) bot.entity.position = bot.entity.position.plus(to.minus(bot.entity.position).normalize().scaled(Math.max(0, d - range)))
      }
    }
  }

  return bot
}

/** The slice of the runner that the action layer borrows; records the commands it is asked to run. */
export function makeRunnerStub (bot, replies = {}) {
  const calls = []
  return {
    calls,
    state: { dead: false, mode: 'idle', task: null },
    ownerEntity: () => Object.values(bot.entities || {}).find((e) => e.type === 'player') || null,
    status: () => ({ health: bot.health, food: bot.food, task: null, mode: 'idle' }),
    async run (command) {
      calls.push(command)
      return replies[command] || { ok: true, message: `ran ${command}` }
    }
  }
}

export const at = (x, y, z) => new Vec3(x, y, z)
