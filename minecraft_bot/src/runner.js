// Everything Remielle can DO in the game. One runner exists per connection to the server.
//
// Design rules (so she is hard to break):
//  - run() never throws: it always returns { ok, message } where message is safe to say out loud.
//  - long jobs (collect, craft, fight...) run as a "task": run() returns at once, and a task_done / task_failed
//    event is posted when the job ends. Only one task runs at a time; a new one cancels the old one.
//  - "modes" (follow, guard, stay) are re-applied by tick() every ~400 ms, so a lost path never leaves her stuck.
import pathfinderPkg from 'mineflayer-pathfinder'
import { Vec3 } from 'vec3'
import { parseCommand, normalizeName, COMMAND_HELP } from './parser.js'
import { createObserver } from './observe.js'
import { createActions } from './primitives.js'
import {
  resolveBlockTarget, resolveMobName, craftCandidates,
  HOSTILE_NAMES, RUN_FROM, LEAVE_ALONE, ANIMALS
} from './aliases.js'

const { Movements, goals } = pathfinderPkg

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

class UserError extends Error {} // the message is safe to speak
class Aborted extends Error {}

const ok = (message, data) => (data ? { ok: true, message, data } : { ok: true, message })
const fail = (message) => ({ ok: false, message })

const round1 = (n) => Math.round(n * 10) / 10
const nice = (name) => String(name || '').replace(/_/g, ' ')

const UNCOUNTABLE = /(?:stone|coal|dirt|sand|gravel|redstone|lapis_lazuli|obsidian|flint|string|leather|sugar|bread|beef|porkchop|mutton|chicken|cod|salmon|wool|glass|clay|netherrack|cobbled_deepslate|raw_iron|raw_gold|raw_copper|iron|gold|copper|diamond|emerald)$/
export function plural (name, n) {
  const base = nice(name)
  if (n === 1 || UNCOUNTABLE.test(name)) return base
  return /(?:ch|sh|x)$/.test(base) ? `${base}es` : `${base}s`
}

const MATERIALS = ['netherite', 'diamond', 'iron', 'stone', 'golden', 'wooden']
const ARMOR_MATERIALS = ['netherite', 'diamond', 'iron', 'chainmail', 'golden', 'leather', 'turtle']
const ARMOR_SLOTS = { head: '_helmet', torso: '_chestplate', legs: '_leggings', feet: '_boots' }
const rankIn = (list, name) => {
  const i = list.findIndex((m) => name.startsWith(m))
  return i === -1 ? list.length : i
}

export function createRunner ({ bot, settings, events, shared, log = () => {} }) {
  const state = {
    mode: 'idle', // idle | follow | guard | stay
    followTarget: null,
    task: null,
    lastHurtAt: 0,
    lastHostileEventAt: 0,
    lastLostOwnerAt: 0,
    lastFleeAt: 0,
    lastHurtEventAt: 0,
    announced: new Map(), // entity id -> time, so one zombie is announced once
    nightAnnouncedDay: -1,
    lastArmorCheck: 0,
    armorBusy: false,
    dead: false,
    taskSeq: 0,
    lastPos: null,
    lastMovedAt: 0,
    lastUnstickAt: 0,
    stuckSince: 0,
    stuckRound: 0, // which rung of the unstick ladder we're on
    strafeSide: 'right', // alternates so she tries both ways around an obstacle
    lastStuckEventAt: 0,
    floating: false
  }

  // ------------------------------------------------------------------- small helpers

  const say = (text) => {
    const s = String(text || '').replace(/[\r\n]+/g, ' ').trim()
    for (let i = 0; i < s.length; i += 230) {
      try { bot.chat(s.slice(i, i + 230)) } catch (e) { log('chat failed', e.message) }
    }
  }

  const ownerName = () => shared.owner || settings.owner || null

  function findPlayerEntity (name) {
    if (!name) return null
    const wanted = String(name).toLowerCase()
    for (const [uname, player] of Object.entries(bot.players || {})) {
      if (uname.toLowerCase() !== wanted) continue
      if (player.entity) return player.entity
      // some servers spawn the player's entity under an odd type; the player's UUID still identifies it
      if (player.uuid) {
        const byUuid = Object.values(bot.entities || {}).find((e) => e && e.uuid === player.uuid && e !== bot.entity)
        if (byUuid) return byUuid
      }
    }
    return null
  }

  const ownerEntity = () => findPlayerEntity(ownerName())

  const myPos = () => bot.entity?.position

  function isHostile (e) {
    return !!e && e !== bot.entity && e.type !== 'player' && (e.type === 'hostile' || HOSTILE_NAMES.has(e.name))
  }

  function entitiesNear (center, maxDist, predicate) {
    const out = []
    for (const e of Object.values(bot.entities || {})) {
      if (!e || e === bot.entity || e.isValid === false || !e.position) continue
      const d = e.position.distanceTo(center)
      if (d <= maxDist && predicate(e)) out.push({ e, d })
    }
    out.sort((a, b) => a.d - b.d)
    return out.map((x) => x.e)
  }

  // a monster she is willing to start fighting by herself
  const isAutoTarget = (e) => isHostile(e) && !RUN_FROM.has(e.name) && !LEAVE_ALONE.has(e.name)

  function inventoryCounts () {
    const counts = new Map()
    for (const item of bot.inventory.items()) counts.set(item.name, (counts.get(item.name) || 0) + item.count)
    return counts
  }

  const countOf = (names) => {
    const set = new Set(names)
    let total = 0
    for (const item of bot.inventory.items()) if (set.has(item.name)) total += item.count
    return total
  }

  function friendlyError (e) {
    if (e instanceof UserError) return e.message
    const msg = String(e?.message || e)
    if (e?.name === 'NoPath' || /no path/i.test(msg)) return "I can't find a way to get there."
    if (/timeout|timed out/i.test(msg)) return 'That took too long.'
    return 'Something got in my way.'
  }

  function withTimeout (promise, ms, onTimeout) {
    let timer
    const timeout = new Promise((_resolve, reject) => {
      timer = setTimeout(() => {
        try { onTimeout?.() } catch { /* ignore */ }
        reject(new UserError('That took too long, so I gave up.'))
      }, ms)
    })
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
  }

  // ------------------------------------------------------------------- movement

  // Minecraft keeps the player's width as the float 0.6F, so the server's half-width is the float 0.3F,
  // which as a JavaScript double is 0.30000001192092896. prismarine-physics 1.11.1 hardcodes the double
  // 0.3 instead (mineflayer issue #3911), and on 1.21.11 that gap makes her own simulation disagree with
  // the server about where her feet and her shoulders are: she jumps, the block she was climbing turns
  // out to be inside her, the server corrects her, and she hangs at the top of it. Rounded to the game's
  // float, both agree. bot.physics is the same object the pathfinder plans with, so one fix covers both.
  const GAME_HALF_WIDTH = Math.fround(0.6) / 2

  /** Matches her hitbox to the width the server actually uses. Returns true when it had to correct it. */
  function fixHitbox () {
    if (!bot.physics || bot.physics.playerHalfWidth === GAME_HALF_WIDTH) return false
    bot.physics.playerHalfWidth = GAME_HALF_WIDTH
    return true
  }

  function setupMovements () {
    const movements = new Movements(bot)
    movements.canDig = false // she never wrecks your builds just to walk somewhere
    movements.allowSprinting = true
    movements.allowParkour = true
    movements.allow1by1towers = true
    bot.pathfinder.setMovements(movements)
  }

  // ------------------------------------------------------------------- hands on the keys
  //
  // The pathfinder writes forward/jump/sprint itself on every physics tick (~20 per second), so a
  // single manual setControlState() is overwritten within ~40 ms and the bot never actually moves.
  // A push therefore re-asserts its keys faster than the pathfinder can clear them.

  const HOLD_REPRESS_MS = 25
  let hold = null

  /** Lets go of everything a push was holding. Idempotent. */
  function stopHold () {
    if (!hold) return
    clearInterval(hold.timer)
    clearTimeout(hold.releaseAt)
    for (const c of hold.controls) {
      if (c === 'jump' && state.floating) continue // swimming keeps the jump key held
      try { bot.setControlState(c, false) } catch { /* ignore */ }
    }
    hold = null
  }

  /** Presses `controls`, keeps pressing them for `ms`, then releases. Never throws. */
  function holdControls (controls, ms) {
    stopHold()
    const press = () => { for (const c of controls) { try { bot.setControlState(c, true) } catch { /* ignore */ } } }
    press()
    hold = { controls, timer: setInterval(press, HOLD_REPRESS_MS), releaseAt: setTimeout(stopHold, ms) }
  }

  /** One quick hop. Never throws. */
  function hop (ms = 280) {
    holdControls(['jump'], ms)
  }

  // ------------------------------------------------------------------- getting unstuck
  //
  // The pathfinder walks around most things, but it has blind spots: a one-block step it decided
  // not to climb, a fence, a corner it keeps rubbing against, or a goal it can see but not reach
  // (then it just stands there with the goal still set — isMoving() is false, so a check that only
  // looks at "is a path running?" never notices). tick() calls unstick() every ~400 ms. When she
  // should be moving but isn't, it climbs a little ladder of fixes:
  //   1. throw the route away, ask for a fresh one, then hop
  //   2. face the goal and push forward over the obstacle (jumping while moving climbs one block)
  //   3. sidestep around it, alternating left and right
  // After several failed rounds she says she is stuck, takes a breath, and tries again.

  const STUCK_AFTER_MS = 1000      // no real movement for this long while she wants to move
  const STUCK_COOLDOWN_MS = 1100   // wait this long between two fixes
  const STUCK_NUDGE_MS = 650       // how long one hands-on push lasts
  const STUCK_GIVE_UP = 8          // fixes tried before she complains and pauses

  /** Where a pathfinder goal ultimately points (unwraps flee-goals, follows moving players). */
  function goalPoint (goal, depth = 0) {
    if (!goal || depth > 3) return null
    if (typeof goal.x === 'number' && typeof goal.z === 'number') {
      return { x: goal.x, y: typeof goal.y === 'number' ? goal.y : null, z: goal.z, range: goal.range || 0 }
    }
    if (goal.entity?.position) {
      const p = goal.entity.position
      return { x: p.x, y: p.y, z: p.z, range: goal.range || 0 }
    }
    if (goal.goal) return goalPoint(goal.goal, depth + 1)
    return null
  }

  /** True when she has somewhere to be but isn't getting there. */
  function wantsToMove () {
    if (bot.targetDigBlock) return false // digging in place is fine
    const goal = bot.pathfinder?.goal
    if (!goal) return false
    if (typeof bot.pathfinder.isMoving === 'function' && bot.pathfinder.isMoving()) return true
    // no path under her feet, but the goal is still out of reach: the planner gave up on it
    const pos = myPos()
    const point = goalPoint(goal)
    if (pos && point) return Math.hypot(pos.x - point.x, pos.z - point.z) - point.range > 1.2
    return typeof bot.pathfinder.isMoving !== 'function' // old/mock pathfinder: assume she means it
  }

  /** Asks the pathfinder to throw its route away and plan a fresh one. Never throws. */
  function forceRepath () {
    const goal = bot.pathfinder?.goal
    if (!goal) return
    try { bot.pathfinder.setGoal(goal, true) } catch { /* ignore */ }
  }

  /**
   * Takes the controls for a moment: face the goal and push (with jump, so a one-block step
   * becomes a climb). Never throws.
   */
  function nudge (point, controls, ms = STUCK_NUDGE_MS) {
    const pos = myPos()
    if (point && pos) {
      try {
        const looking = bot.lookAt(new Vec3(point.x, (point.y ?? pos.y) + 1.4, point.z), true)
        looking?.catch?.(() => {})
      } catch { /* ignore */ }
    }
    holdControls(controls, ms)
  }

  /**
   * Gets her moving again when she's stuck against something (a one-block step, a fence,
   * a corner, a doorway) using the ladder above. In water she keeps afloat while she waits.
   */
  function unstick (now) {
    const pos = myPos()
    if (!pos) return
    const inWater = !!(bot.entity?.isInWater || bot.entity?.isInLava)
    const moving = wantsToMove()

    if (inWater && !moving && !state.floating) {
      state.floating = true
      try { bot.setControlState('jump', true) } catch { /* ignore */ }
    } else if ((!inWater || moving) && state.floating) {
      state.floating = false
      try { bot.setControlState('jump', false) } catch { /* ignore */ }
    }

    const last = state.lastPos
    if (!last || Math.hypot(pos.x - last.x, pos.z - last.z) > 0.12 || Math.abs(pos.y - last.y) > 0.3) {
      state.lastPos = { x: pos.x, y: pos.y, z: pos.z }
      state.lastMovedAt = now
      state.stuckSince = 0
      state.stuckRound = 0
      return
    }
    if (!moving) { state.stuckSince = 0; state.stuckRound = 0; state.lastMovedAt = now; return }

    const stuckFor = now - state.lastMovedAt
    if (stuckFor < STUCK_AFTER_MS || now - state.lastUnstickAt < STUCK_COOLDOWN_MS) return
    state.lastUnstickAt = now
    if (!state.stuckSince) state.stuckSince = now

    if (state.stuckRound >= STUCK_GIVE_UP) {
      // nothing on the ladder worked: say so once, take a breath, then try the ladder again
      state.stuckRound = 0
      state.stuckSince = 0
      state.lastMovedAt = now
      if (now - state.lastStuckEventAt > 30000) {
        state.lastStuckEventAt = now
        events.push('stuck', { message: "I'm stuck! There's something in my way and I can't get past it.", mood: 'sad', priority: 1, speak: true })
      }
      return
    }

    const point = goalPoint(bot.pathfinder?.goal)
    if (state.stuckRound === 0) {
      forceRepath() // re-planning clears the keys, so the hop has to come after it
      hop()
    } else if (state.stuckRound === 1) {
      nudge(point, ['sprint', 'forward', 'jump']) // take the obstacle head-on and climb it
    } else {
      // slip around it sideways, a different side every round
      state.strafeSide = state.strafeSide === 'left' ? 'right' : 'left'
      nudge(point, ['forward', 'jump', state.strafeSide])
    }
    state.stuckRound++
  }

  function stopMovement () {
    stopHold()
    try { bot.pathfinder.setGoal(null) } catch { /* ignore */ }
    try { bot.clearControlStates() } catch { /* ignore */ }
  }

  async function gotoNear (task, pos, range = 2, timeoutMs = 60000) {
    ensureActive(task)
    const goal = new goals.GoalNear(pos.x, pos.y, pos.z, range)
    await withTimeout(bot.pathfinder.goto(goal), timeoutMs, stopMovement)
    ensureActive(task)
  }

  function ensureFollow () {
    const name = state.followTarget || ownerName()
    const entity = findPlayerEntity(name)
    if (!entity) {
      const now = Date.now()
      if (now - state.lastLostOwnerAt > 60000 && name) {
        state.lastLostOwnerAt = now
        events.push('lost_owner', { message: `I lost sight of ${name === ownerName() ? 'you' : name}! Where did you go?`, mood: 'sad', priority: 2, speak: true })
      }
      return
    }
    if (bot.pathfinder.goal == null || state.followingId !== entity.id) {
      bot.pathfinder.setGoal(new goals.GoalFollow(entity, 3), true)
      state.followingId = entity.id
    }
  }

  // ------------------------------------------------------------------- tasks

  function ensureActive (task) {
    if (task?.aborted) throw new Aborted()
  }

  function cancelTask () {
    const task = state.task
    state.task = null
    if (task) {
      task.aborted = true
      try { bot.collectBlock?.cancelTask?.(() => {})?.catch?.(() => {}) } catch { /* ignore */ }
      try { bot.stopDigging() } catch { /* ignore */ }
    }
    state.followingId = null
    stopMovement()
    return task
  }

  function startTask (type, label, fn, { announce = true } = {}) {
    cancelTask()
    const task = { id: ++state.taskSeq, type, label, aborted: false, startedAt: Date.now() }
    state.task = task
    Promise.resolve()
      .then(() => fn(task))
      .then((message) => {
        if (task.aborted || state.task !== task) return
        state.task = null
        state.followingId = null
        if (announce && message) events.push('task_done', { task: type, message, mood: 'happy', priority: 2, speak: true })
      })
      .catch((e) => {
        if (e instanceof Aborted || task.aborted || state.task !== task) return
        state.task = null
        state.followingId = null
        stopMovement()
        log(`task ${type} failed:`, e?.stack || e)
        if (announce) events.push('task_failed', { task: type, message: friendlyError(e), mood: 'sad', priority: 2, speak: true })
      })
    return task
  }

  // ------------------------------------------------------------------- equipment

  async function equipBestWeapon () {
    try {
      const items = bot.inventory.items()
      const swords = items.filter((i) => i.name.endsWith('_sword')).sort((a, b) => rankIn(MATERIALS, a.name) - rankIn(MATERIALS, b.name))
      const axes = items.filter((i) => i.name.endsWith('_axe')).sort((a, b) => rankIn(MATERIALS, a.name) - rankIn(MATERIALS, b.name))
      const best = swords[0] || axes[0]
      if (best && bot.heldItem?.name !== best.name) await bot.equip(best, 'hand')
    } catch (e) { log('equip weapon failed', e.message) }
  }

  async function equipArmor () {
    if (state.armorBusy || !bot.entity) return
    state.armorBusy = true
    try {
      for (const [slot, suffix] of Object.entries(ARMOR_SLOTS)) {
        const candidates = bot.inventory.items().filter((i) => i.name.endsWith(suffix))
          .sort((a, b) => rankIn(ARMOR_MATERIALS, a.name) - rankIn(ARMOR_MATERIALS, b.name))
        if (!candidates.length) continue
        const current = bot.inventory.slots[bot.getEquipmentDestSlot(slot)]
        if (current && rankIn(ARMOR_MATERIALS, current.name) <= rankIn(ARMOR_MATERIALS, candidates[0].name)) continue
        await bot.equip(candidates[0], slot)
      }
    } catch (e) { log('equip armor failed', e.message) } finally { state.armorBusy = false }
  }

  // ------------------------------------------------------------------- combat

  function swingCooldownMs () {
    const held = bot.heldItem?.name || ''
    if (held.endsWith('_sword')) return 650
    if (held.endsWith('_axe')) return 1100
    return 450
  }

  async function collectDrops (task, centerPos, maxMs = 6000) {
    const started = Date.now()
    while (Date.now() - started < maxMs) {
      ensureActive(task)
      const drops = entitiesNear(centerPos, 10, (e) => e.name === 'item' || e.displayName === 'Item')
      if (!drops.length) return
      try {
        await withTimeout(bot.pathfinder.goto(new goals.GoalNear(drops[0].position.x, drops[0].position.y, drops[0].position.z, 0.8)), 4000, stopMovement)
      } catch (e) {
        if (e instanceof Aborted) throw e
        return
      }
      await sleep(150)
    }
  }

  async function fight (task, target, { maxMs = 45000, label = 'monster' } = {}) {
    await equipBestWeapon()
    const started = Date.now()
    let lastSwing = 0
    let chasing = false
    let lastPos = target.position.clone()
    while (Date.now() - started < maxMs) {
      ensureActive(task)
      if (state.dead || !bot.entity) throw new Aborted()
      if (target.isValid === false || !bot.entities[target.id]) break
      lastPos = target.position.clone()
      if (bot.health !== undefined && bot.health <= 5) throw new UserError("I'm too hurt to keep fighting, I'm backing off!")
      const dist = bot.entity.position.distanceTo(target.position)
      if (dist > 24) throw new UserError(`The ${label} got away.`)
      if (dist > 2.6 && !chasing) {
        bot.pathfinder.setGoal(new goals.GoalFollow(target, 2), true)
        chasing = true
      }
      const now = Date.now()
      if (dist <= 3.4 && now - lastSwing >= swingCooldownMs()) {
        try { await bot.lookAt(target.position.offset(0, (target.height || 1.6) * 0.85, 0), true) } catch { /* ignore */ }
        try { bot.attack(target) } catch (e) { log('attack failed', e.message) }
        lastSwing = now
      }
      await sleep(100)
    }
    stopMovement()
    ensureActive(task)
    await sleep(500)
    await collectDrops(task, lastPos)
    return `The ${label} is gone!`
  }

  function nearestAutoTarget (maxDist, center = myPos()) {
    if (!center) return null
    return entitiesNear(center, maxDist, isAutoTarget)[0] || null
  }

  function startFight (target, { announce = false } = {}) {
    const label = nice(target.name || 'monster')
    startTask('fight', `fighting a ${label}`, (task) => fight(task, target, { label }), { announce })
  }

  function startFlee (threat) {
    const label = nice(threat.name || 'monster')
    startTask('flee', `running from a ${label}`, async (task) => {
      bot.pathfinder.setGoal(new goals.GoalInvert(new goals.GoalFollow(threat, 14)), true)
      const started = Date.now()
      while (Date.now() - started < 4500) {
        ensureActive(task)
        if (threat.isValid === false) break
        if (bot.entity.position.distanceTo(threat.position) > 12) break
        await sleep(150)
      }
      stopMovement()
      return null
    }, { announce: false })
  }

  // ------------------------------------------------------------------- collecting

  function canHarvest (block) {
    if (!block.harvestTools) return true // hand-breakable
    return bot.inventory.items().some((item) => block.harvestTools[item.type])
  }

  async function doCollect (task, target, wanted) {
    const ids = target.blocks.map((n) => bot.registry.blocksByName[n]?.id).filter((id) => id !== undefined)
    if (!ids.length) throw new UserError(`I don't know a block called ${target.label}.`)
    const before = countOf(target.drops)
    let gathered = 0
    let stuck = 0
    const deadline = Date.now() + 5 * 60 * 1000
    while (gathered < wanted) {
      ensureActive(task)
      if (Date.now() > deadline) throw new UserError(`I only managed to get ${gathered} ${target.label} in the time I had.`)
      const positions = bot.findBlocks({ matching: ids, maxDistance: 64, count: 32 })
      const blocks = positions.map((p) => bot.blockAt(p)).filter(Boolean)
      if (!blocks.length) {
        throw new UserError(gathered
          ? `I collected ${gathered} ${target.label}, but I can't see any more nearby.`
          : `I can't see any ${target.label} nearby. Show me where some are.`)
      }
      const usable = blocks.filter(canHarvest)
      if (!usable.length) {
        throw new UserError(gathered
          ? `I collected ${gathered} ${target.label}, but I need a better tool for the rest.`
          : `I don't have a tool that can break ${target.label}. Give me a pickaxe, or tell me to craft one.`)
      }
      const batch = usable.slice(0, Math.min(4, wanted - gathered + 1))
      try {
        await withTimeout(bot.collectBlock.collect(batch, { ignoreNoPath: true }), 90000, () => cancelTask())
      } catch (e) {
        if (e instanceof Aborted || task.aborted) throw new Aborted()
        stuck++
        log('collect batch failed:', e?.message || e)
      }
      ensureActive(task)
      const now = countOf(target.drops) - before
      stuck = now > gathered ? 0 : stuck + 1
      gathered = Math.max(gathered, now)
      if (stuck >= 3) {
        throw new UserError(gathered
          ? `I collected ${gathered} ${target.label}, then got stuck.`
          : `I can't reach any ${target.label} right now.`)
      }
    }
    return `I collected ${gathered} ${plural(target.drops[0], gathered)}!`
  }

  // ------------------------------------------------------------------- crafting

  function findCraftingTable () {
    const id = bot.registry.blocksByName.crafting_table?.id
    if (id === undefined) return null
    const pos = bot.findBlock({ matching: id, maxDistance: 24 })
    return pos || null
  }

  async function craftSimple (itemName, count) {
    const item = bot.registry.itemsByName[itemName]
    if (!item) return false
    const recipes = bot.recipesFor(item.id, null, 1, null)
    if (!recipes.length) return false
    const per = recipes[0].result?.count || 1
    await bot.craft(recipes[0], Math.max(1, Math.ceil(count / per)), null)
    return true
  }

  async function ensurePlanks (needed) {
    const have = () => bot.inventory.items().filter((i) => i.name.endsWith('_planks')).reduce((s, i) => s + i.count, 0)
    let guard = 0
    while (have() < needed && guard++ < 12) {
      const log = bot.inventory.items().find((i) => i.name.endsWith('_log') || i.name.endsWith('_stem'))
      if (!log) return false
      const planks = log.name.replace(/_(?:log|stem)$/, '_planks')
      if (!(await craftSimple(planks, 4))) return false
    }
    return have() >= needed
  }

  async function ensureSticks (needed) {
    const have = () => countOf(['stick'])
    if (have() >= needed) return true
    if (!(await ensurePlanks(2))) return false
    return craftSimple('stick', 4)
  }

  async function placeCraftingTable (task) {
    let tableItem = bot.inventory.items().find((i) => i.name === 'crafting_table')
    if (!tableItem) {
      if (!(await ensurePlanks(4))) return null
      if (!(await craftSimple('crafting_table', 1))) return null
      tableItem = bot.inventory.items().find((i) => i.name === 'crafting_table')
      if (!tableItem) return null
    }
    await bot.equip(tableItem, 'hand')
    const base = bot.entity.position.floored()
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1]]) {
      ensureActive(task)
      const ref = bot.blockAt(base.offset(dx, -1, dz))
      const space = bot.blockAt(base.offset(dx, 0, dz))
      const above = bot.blockAt(base.offset(dx, 1, dz))
      if (!ref || !space || !above) continue
      if (ref.boundingBox !== 'block' || space.name !== 'air' || above.name !== 'air') continue
      try {
        await bot.placeBlock(ref, new Vec3(0, 1, 0))
        await sleep(300)
        const placed = bot.blockAt(base.offset(dx, 0, dz))
        if (placed?.name === 'crafting_table') return placed
      } catch (e) { log('place table failed', e.message) }
    }
    return null
  }

  async function doCraft (task, word, count) {
    const names = craftCandidates(word, bot.registry)
    if (!names.length) throw new UserError(`I don't know how to make ${nice(word)}.`)

    // wooden/stone tools need sticks and planks or cobblestone: prepare what we can from logs
    const wantsTool = names.some((n) => /_(?:pickaxe|axe|shovel|sword|hoe)$/.test(n))
    for (const name of names) {
      ensureActive(task)
      const item = bot.registry.itemsByName[name]
      if (!item) continue
      if (wantsTool || name === 'torch') await ensureSticks(2)
      if (name.startsWith('wooden_') || name === 'crafting_table') await ensurePlanks(name === 'crafting_table' ? 4 : 3)

      let table = null
      let recipes = bot.recipesFor(item.id, null, 1, null)
      if (!recipes.length) {
        let tableBlock = findCraftingTable()
        let tableObj = tableBlock ? bot.blockAt(tableBlock) : null
        if (!tableObj) {
          // only build a table if a recipe would exist with one, i.e. we have the materials
          tableObj = await placeCraftingTable(task)
        }
        if (tableObj) {
          await gotoNear(task, tableObj.position, 2.5, 20000)
          table = tableObj
          recipes = bot.recipesFor(item.id, null, 1, table)
        }
      }
      if (!recipes.length) continue
      const per = recipes[0].result?.count || 1
      const times = Math.max(1, Math.ceil(count / per))
      await bot.craft(recipes[0], times, table || undefined)
      return `I made ${times * per} ${plural(name, times * per)}!`
    }
    throw new UserError(`I don't have the materials to make ${nice(word)}.`)
  }

  // ------------------------------------------------------------------- the commands

  function describeInventory () {
    const counts = [...inventoryCounts().entries()].sort((a, b) => b[1] - a[1])
    if (!counts.length) return { text: 'My inventory is empty.', counts }
    const top = counts.slice(0, 6).map(([name, n]) => `${n} ${plural(name, n)}`)
    const more = counts.length - top.length
    const list = top.length > 1 ? `${top.slice(0, -1).join(', ')} and ${top[top.length - 1]}` : top[0]
    return { text: `I have ${list}${more > 0 ? `, plus ${more} other kinds of things` : ''}.`, counts }
  }

  function statusData () {
    const pos = myPos()
    const owner = ownerEntity()
    const hostiles = pos ? entitiesNear(pos, 24, isHostile).slice(0, 5).map((e) => ({ name: e.name, distance: round1(e.position.distanceTo(pos)) })) : []
    return {
      connected: true,
      username: bot.username,
      health: bot.health,
      food: bot.food,
      position: pos ? { x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z) } : null,
      dimension: bot.game?.dimension,
      isDay: bot.time?.isDay,
      timeOfDay: bot.time?.timeOfDay,
      raining: !!bot.isRaining,
      mode: state.mode,
      task: state.task ? { type: state.task.type, label: state.task.label } : null,
      owner: ownerName(),
      ownerVisible: !!owner,
      ownerDistance: owner && pos ? round1(owner.position.distanceTo(pos)) : null,
      hostiles,
      inventory: Object.fromEntries([...inventoryCounts().entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)),
      home: shared.home ? { x: shared.home.x, y: shared.home.y, z: shared.home.z } : null
    }
  }

  function needOwner () {
    const entity = ownerEntity()
    if (!entity) throw new UserError(ownerName() ? `I can't see ${ownerName()} right now.` : "I don't know who you are yet. Say something in chat first.")
    return entity
  }

  function findMob (want, maxDist = 40) {
    const center = myPos()
    if (!center) return null
    if (want === 'hostile') return nearestAutoTarget(maxDist)
    if (want === 'animal' || want === 'any') return entitiesNear(center, maxDist, (e) => ANIMALS.has(e.name))[0] || null
    return entitiesNear(center, maxDist, (e) => e.name === want && e.type !== 'player')[0] || null
  }

  const handlers = {
    help () {
      return ok(`I can follow you, come, stop, stay, guard you, attack monsters, hunt animals, collect blocks, craft things, go to coordinates, go home, give you items, eat, sleep, jump, tell you my inventory and status. Commands: ${COMMAND_HELP.join(', ')}.`)
    },

    follow (cmd) {
      const who = cmd.args[0] || ownerName()
      if (!who) throw new UserError("I don't know who to follow yet.")
      if (!findPlayerEntity(who)) throw new UserError(`I can't see ${who} right now.`)
      cancelTask()
      state.mode = 'follow'
      state.followTarget = who
      ensureFollow()
      return ok(who === ownerName() ? "Okay! I'll stay right by your side." : `Okay! I'll follow ${who}.`)
    },

    come () {
      const entity = needOwner()
      startTask('come', 'coming to you', async (task) => {
        await gotoNear(task, entity.position, 2)
        return "I'm here!"
      })
      return ok('Coming to you!')
    },

    stop () {
      const had = state.task || state.mode !== 'idle'
      cancelTask()
      state.mode = 'idle'
      state.followTarget = null
      return ok(had ? 'Okay, I stopped.' : "I wasn't doing anything, but okay!")
    },

    stay () {
      cancelTask()
      state.mode = 'stay'
      return ok("Okay, I'll stay right here.")
    },

    guard () {
      const who = ownerName()
      if (!who) throw new UserError("I don't know who to guard yet.")
      if (!findPlayerEntity(who)) throw new UserError(`I can't see ${who} right now.`)
      cancelTask()
      state.mode = 'guard'
      state.followTarget = who
      ensureFollow()
      return ok("I'll guard you! Nothing gets past me.")
    },

    attack (cmd) {
      const want = resolveMobName(cmd.args.join(' ')) || 'hostile'
      const target = findMob(want)
      const nameText = want === 'hostile' ? 'monster' : nice(want)
      if (!target) throw new UserError(want === 'hostile' ? "I don't see any monsters nearby." : `I can't see any ${nameText} nearby.`)
      if (RUN_FROM.has(target.name)) throw new UserError(`I won't fight a ${nice(target.name)} up close, that's too dangerous!`)
      startFight(target, { announce: true })
      return ok(`Attacking the ${nice(target.name)}!`)
    },

    hunt (cmd) {
      const want = resolveMobName(cmd.args.join(' ')) || 'animal'
      const target = findMob(ANIMALS.has(want) ? want : 'animal', 48)
      if (!target) throw new UserError(ANIMALS.has(want) ? `I can't see any ${want} nearby.` : "I don't see any animals nearby.")
      const label = nice(target.name)
      startTask('hunt', `hunting a ${label}`, async (task) => {
        const before = [...inventoryCounts().values()].reduce((a, b) => a + b, 0)
        await fight(task, target, { label, maxMs: 30000 })
        const after = [...inventoryCounts().values()].reduce((a, b) => a + b, 0)
        return after > before ? `I hunted the ${label} and picked up what it dropped!` : `The ${label} is down.`
      })
      return ok(`I'll go hunt that ${label}!`)
    },

    collect (cmd) {
      const word = cmd.args.join(' ')
      if (!word) throw new UserError('What should I collect?')
      const target = resolveBlockTarget(word, bot.registry)
      if (!target) throw new UserError(`I don't know how to collect ${nice(word)}.`)
      const wanted = Math.min(cmd.count ?? 8, settings.maxCollect)
      startTask('collect', `collecting ${target.label}`, (task) => doCollect(task, target, wanted))
      return ok(`Okay, I'll go collect ${wanted} ${target.label}.`)
    },

    craft (cmd) {
      const word = cmd.args.join(' ')
      if (!word) throw new UserError('What should I craft?')
      const count = Math.min(cmd.count ?? 1, 64)
      if (!craftCandidates(word, bot.registry).length) throw new UserError(`I don't know how to make ${nice(word)}.`)
      startTask('craft', `crafting ${nice(word)}`, (task) => doCraft(task, word, count))
      return ok(`Okay, I'll make ${nice(word)}.`)
    },

    goto (cmd) {
      const nums = cmd.args.map(Number)
      if (cmd.args.length >= 3 && nums.slice(0, 3).every(Number.isFinite)) {
        const [x, y, z] = nums
        startTask('goto', 'walking to the coordinates', async (task) => {
          await gotoNear(task, new Vec3(x, y, z), 1.5, 120000)
          return `I've arrived at ${Math.floor(x)}, ${Math.floor(y)}, ${Math.floor(z)}.`
        })
        return ok(`On my way to ${Math.floor(x)}, ${Math.floor(y)}, ${Math.floor(z)}.`)
      }
      const who = cmd.args[0]
      const entity = findPlayerEntity(who)
      if (!who || !entity) throw new UserError('Tell me three coordinates, or the name of a player I can see.')
      startTask('goto', `walking to ${who}`, async (task) => {
        await gotoNear(task, entity.position, 2)
        return `I'm next to ${who}.`
      })
      return ok(`Going to ${who}.`)
    },

    sethome () {
      const pos = myPos()
      if (!pos) throw new UserError("I'm not in the world yet.")
      shared.home = { x: Math.floor(pos.x), y: Math.floor(pos.y), z: Math.floor(pos.z) }
      shared.save?.()
      return ok('Okay, this is our home now. I will remember it!')
    },

    home () {
      if (!shared.home) throw new UserError("We don't have a home yet. Tell me to set one first.")
      const { x, y, z } = shared.home
      startTask('home', 'walking home', async (task) => {
        await gotoNear(task, new Vec3(x, y, z), 2, 120000)
        return "I'm home!"
      })
      return ok('Heading home!')
    },

    tp () {
      const who = ownerName()
      if (!who) throw new UserError("I don't know who to teleport to.")
      say(`/tp ${bot.username} ${who}`)
      return ok("I tried to teleport to you. If nothing happens, type op Remielle in the server window first.")
    },

    give (cmd) {
      const word = cmd.args.join(' ')
      if (!word) throw new UserError('What should I give you?')
      const entity = needOwner()
      const target = resolveBlockTarget(word, bot.registry)
      const wanted = new Set([normalizeName(word), ...(target?.drops || [])])
      const items = bot.inventory.items().filter((i) => wanted.has(i.name))
      if (!items.length) throw new UserError(`I don't have any ${nice(word)}.`)
      let remaining = cmd.count ?? items.reduce((s, i) => s + i.count, 0)
      startTask('give', 'handing things over', async (task) => {
        await gotoNear(task, entity.position, 2)
        await bot.lookAt(entity.position.offset(0, 1, 0), true)
        let given = 0
        for (const item of items) {
          if (remaining <= 0) break
          const n = Math.min(item.count, remaining)
          await bot.toss(item.type, null, n)
          remaining -= n
          given += n
          await sleep(250)
        }
        return `Here you go, ${given} ${plural(items[0].name, given)}!`
      })
      return ok('Okay, bringing it to you!')
    },

    async jump (cmd) {
      const times = Math.max(1, Math.min(5, cmd.count || 1))
      for (let i = 0; i < times; i++) {
        hop(300)
        await sleep(650)
      }
      return ok(times === 1 ? 'Hop!' : `Hop hop! Jumped ${times} times.`)
    },

    async eat () {
      if (!bot.autoEat) throw new UserError("I can't eat right now.")
      if (bot.food >= 20) return ok("I'm not hungry at all.")
      try {
        await bot.autoEat.eat({})
        return ok('Yum! That was good.')
      } catch {
        throw new UserError("I don't have any food.")
      }
    },

    sleep () {
      if (bot.time?.isDay) throw new UserError("It's daytime, I can't sleep yet.")
      const bed = bot.findBlock({ matching: (b) => bot.isABed(b), maxDistance: 32 })
      if (!bed) throw new UserError("I can't find a bed nearby.")
      startTask('sleep', 'going to bed', async (task) => {
        await gotoNear(task, bed.position, 2, 60000)
        await bot.sleep(bot.blockAt(bed.position))
        return 'Good night... I mean, good morning!'
      })
      return ok('Going to bed.')
    },

    inventory () {
      const { text, counts } = describeInventory()
      return ok(text, { items: Object.fromEntries(counts) })
    },

    status () {
      const d = statusData()
      const parts = [`I have ${Math.round(d.health)} of 20 health and ${Math.round(d.food)} of 20 food.`]
      if (d.task) parts.push(`I'm busy ${d.task.label}.`)
      else if (d.mode !== 'idle') parts.push(d.mode === 'stay' ? "I'm staying put." : `I'm ${d.mode === 'guard' ? 'guarding you' : 'following you'}.`)
      if (d.hostiles.length) parts.push(`There ${d.hostiles.length === 1 ? 'is a' : 'are'} ${d.hostiles.map((h) => nice(h.name)).join(' and ')} nearby.`)
      return ok(parts.join(' '), d)
    },

    where () {
      const pos = myPos()
      if (!pos) throw new UserError("I'm not in the world yet.")
      const owner = ownerEntity()
      const text = `I'm at ${Math.floor(pos.x)}, ${Math.floor(pos.y)}, ${Math.floor(pos.z)}.`
      return ok(owner ? `${text} That's about ${Math.round(owner.position.distanceTo(pos))} blocks from you.` : text)
    },

    say (cmd) {
      if (!cmd.text) throw new UserError('What should I say?')
      say(cmd.text)
      return ok('Done!')
    }
  }

  async function run (text) {
    const cmd = parseCommand(text)
    if (!cmd) return fail("I don't know that one. Say help and I'll tell you what I can do.")
    if (!bot.entity || state.dead) return fail("I'm not standing in the world right now.")
    try {
      return await handlers[cmd.name](cmd)
    } catch (e) {
      if (e instanceof UserError) return fail(e.message)
      log('command crashed:', cmd.raw, e?.stack || e)
      return fail('Something went wrong while I was doing that.')
    }
  }

  // ------------------------------------------------------------------- the heartbeat

  function watchWorld (now) {
    const pos = myPos()
    if (!pos) return

    // forget old sightings
    for (const [id, t] of state.announced) if (now - t > 60000) state.announced.delete(id)

    const near = entitiesNear(pos, 14, isHostile).filter((e) => !LEAVE_ALONE.has(e.name))
    for (const e of near) {
      const d = e.position.distanceTo(pos)
      if (RUN_FROM.has(e.name) && d < 9 && now - state.lastFleeAt > 5000 && !(state.task?.type === 'flee')) {
        state.lastFleeAt = now
        events.push('danger', { message: `Careful! A ${nice(e.name)}! Stay back, I'm getting away from it!`, mood: 'surprised', priority: 3, speak: true, mob: e.name })
        startFlee(e)
        return
      }
      if (!state.announced.has(e.id) && d < 12 && now - state.lastHostileEventAt > 12000) {
        state.announced.set(e.id, now)
        state.lastHostileEventAt = now
        events.push('hostile_near', { message: `Watch out, there's a ${nice(e.name)} nearby!`, mood: 'surprised', priority: 2, speak: true, mob: e.name })
      }
    }

    // dusk
    const t = bot.time?.timeOfDay
    const day = bot.time?.day
    if (typeof t === 'number' && t >= 13000 && t < 14500 && state.nightAnnouncedDay !== day) {
      state.nightAnnouncedDay = day
      events.push('night', { message: "It's getting dark. Stay close to me, monsters come out at night.", mood: 'relaxed', priority: 1, speak: true })
    }

    if (now - state.lastArmorCheck > 15000) {
      state.lastArmorCheck = now
      equipArmor()
    }
  }

  function tick () {
    if (!bot.entity || state.dead) return
    const now = Date.now()
    try { watchWorld(now) } catch (e) { log('watchWorld failed', e.message) }
    try { unstick(now) } catch (e) { log('unstick failed', e.message) }
    if (state.task) return

    if (state.mode === 'follow' || state.mode === 'guard') {
      ensureFollow()
      const hurtRecently = now - state.lastHurtAt < 4000
      const threat = state.mode === 'guard'
        ? (nearestAutoTarget(10, ownerEntity()?.position || myPos()) || (hurtRecently ? nearestAutoTarget(6) : null))
        : (hurtRecently ? nearestAutoTarget(6) : null)
      if (threat) startFight(threat)
    } else if (now - state.lastHurtAt < 3000) {
      const threat = nearestAutoTarget(5)
      if (threat) startFight(threat)
    }
  }

  // ------------------------------------------------------------------- bot events owned by the runner

  let lastHealth = null
  function onHealth () {
    const health = bot.health
    if (typeof health !== 'number') return
    const now = Date.now()
    if (lastHealth !== null && health < lastHealth) {
      state.lastHurtAt = now
      if (health <= 6 && lastHealth > 6) {
        events.push('low_health', { message: `I'm badly hurt! Only ${Math.max(0, Math.round(health))} health left!`, mood: 'surprised', priority: 3, speak: true })
      } else if (now - state.lastHurtEventAt > 10000) {
        state.lastHurtEventAt = now
        events.push('hurt', { message: 'Ouch! Something hit me!', mood: 'angry', priority: 1, speak: true })
      }
    }
    lastHealth = health
  }

  function onDeath () {
    state.dead = true
    const pos = myPos()
    cancelTask()
    state.mode = 'idle'
    events.push('death', {
      message: pos ? `I died at ${Math.floor(pos.x)}, ${Math.floor(pos.y)}, ${Math.floor(pos.z)}. I'm so sorry...` : "I died... I'm so sorry.",
      mood: 'sad', priority: 3, speak: true
    })
  }

  function onSpawn () {
    const wasDead = state.dead
    state.dead = false
    lastHealth = bot.health ?? null
    if (fixHitbox()) log('hitbox matched to the server\'s player width')
    try { setupMovements() } catch (e) { log('setupMovements failed', e.message) }
    if (wasDead) events.push('respawned', { message: "I'm back! Tell me what to do.", mood: 'happy', priority: 2, speak: true })
  }

  function onPlayerCollect (collector) {
    if (collector === bot.entity) setTimeout(() => equipArmor(), 1200)
  }

  bot.on('health', onHealth)
  bot.on('death', onDeath)
  bot.on('spawn', onSpawn)
  bot.on('playerCollect', onPlayerCollect)
  bot.on('wake', () => events.push('woke', { message: 'Good morning!', mood: 'happy', priority: 1, speak: false }))

  const observer = createObserver({ bot, shared, status: statusData, log })
  const actions = createActions({ bot, observer, log, runner: { state, run, status: statusData, ownerEntity } })

  return {
    run,
    tick,
    state,
    status: statusData,
    observe: () => observer(),
    act: (name, args) => actions.act(name, args),
    actionHelp: () => actions.helpText(),
    say,
    onSpawn,
    cancelTask,
    ownerEntity,
    dispose () {
      cancelTask()
      bot.removeListener('health', onHealth)
      bot.removeListener('death', onDeath)
      bot.removeListener('spawn', onSpawn)
      bot.removeListener('playerCollect', onPlayerCollect)
    }
  }
}
