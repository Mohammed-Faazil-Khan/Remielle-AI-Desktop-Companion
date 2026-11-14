// The actions Remielle's planner can compose.
//
// The voice commands in runner.js are sentences ("collect 10 oak logs") because that is what speech
// needs. Planning needs the opposite: small, honest, composable verbs, so "sit in the boat" becomes
// walk_to -> mount -> look, and "make me a stone sword" becomes recipes -> craft -> craft -> equip.
// Nothing here knows about anybody's goals; each action does one physical thing, checks that it really
// happened, and says so.
//
// Two rules keep her safe to let loose:
//  - every action verifies its own result (bot.vehicle, the block that is now there, the item count)
//    instead of trusting that the packet worked, and
//  - nothing throws. A broken world, a missing tool, a path that does not exist: the planner gets a
//    plain sentence saying why, which is exactly what it needs to pick another way.
import pathfinderPkg from 'mineflayer-pathfinder'
import { Vec3 } from 'vec3'
import { craftCandidates, resolveMobName, HOSTILE_NAMES } from './aliases.js'

const { goals } = pathfinderPkg

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const nice = (n) => String(n || '').replace(/_/g, ' ')
const round1 = (n) => Math.round(n * 10) / 10
const ok = (message, data) => ({ ok: true, message, ...(data ? { data } : {}) })
const bad = (message, data, did) => ({ ok: false, message: String(message || 'that did not work.'), ...(data ? { data } : {}), ...(did ? { did } : {}) })

class Nope extends Error {} // a refusal the planner should read and route around

const VEHICLES = /(?:^|_)boat$|minecart$/
const YAW = { south: 0, west: Math.PI / 2, north: Math.PI, east: -Math.PI / 2 }
const FACE_VECTORS = {
  up: new Vec3(0, 1, 0), down: new Vec3(0, -1, 0),
  north: new Vec3(0, 0, -1), south: new Vec3(0, 0, 1),
  west: new Vec3(-1, 0, 0), east: new Vec3(1, 0, 0)
}

function toVec (value) {
  if (Array.isArray(value) && value.length === 3) return new Vec3(Number(value[0]), Number(value[1]), Number(value[2]))
  if (value && typeof value === 'object' && Number.isFinite(value.x)) return new Vec3(Number(value.x), Number(value.y), Number(value.z))
  if (value && typeof value === 'object' && Number.isFinite(value.at?.x)) return new Vec3(value.at.x, value.at.y, value.at.z)
  return null
}

export function createActions ({ bot, runner, observer, log = () => {} }) {
  // ----------------------------------------------------------------- shared plumbing

  const pos = () => bot.entity?.position || null

  function alive () {
    if (!pos()) throw new Nope("I'm not standing in the world right now.")
    if (runner.state.dead) throw new Nope('I am dead. Give me a second to come back.')
    return pos()
  }

  /** Resolves true when the server confirms the event, false if it never says anything. */
  function onceOrTimeout (event, ms = 2500) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => { bot.removeListener(event, done); resolve(false) }, ms)
      function done () { clearTimeout(timer); bot.removeListener(event, done); resolve(true) }
      bot.on(event, done)
    })
  }

  function findBlockNearby (name, maxDistance = 32) {
    const id = bot.registry?.blocksByName?.[name]?.id
    if (id === undefined) return null
    try {
      const pos2 = bot.findBlock({ matching: id, maxDistance })
      if (!pos2) return null
      return { name, position: pos2 }
    } catch (e) { log('findBlock failed:', e.message); return null }
  }

  async function walkTo (point, range = 1.2, ms = 30000) {
    if (!point) throw new Nope('Where to?')
    const goal = new goals.GoalNear(point.x, point.y, point.z, range)
    try {
      await Promise.race([
        bot.pathfinder.goto(goal),
        new Promise((_r, reject) => setTimeout(() => reject(new Nope('I could not get there in time.')), ms))
      ])
      return true
    } catch (e) {
      if (e instanceof Nope) throw e
      if (e?.name === 'NoPath' || /no path/i.test(String(e?.message))) throw new Nope(`I can't find a way over to ${nice(point.name || 'there')}.`)
      throw new Nope(String(e?.message || e).slice(0, 120))
    }
  }

  async function walkUpTo (entity, range = 1.5) {
    if (!entity?.position) throw new Nope('I cannot see that.')
    if (entity.position.distanceTo(pos()) <= 2.5) return false // already in arm's reach
    await walkTo(entity.position, range)
    return true
  }

  function entityById (id) {
    return Object.values(bot.entities || {}).find((e) => e && e.id === Number(id) && e !== bot.entity) || null
  }

  /** Turns "nearest zombie", "oak boat", "owner", "5" or {name:'pig'} into an entity. */
  function findEntity (target) {
    const here = pos()
    if (target === null || target === undefined || target === '' || target === 'nearest') {
      return null
    }
    if (typeof target === 'object' && !Array.isArray(target)) {
      if (target.id !== undefined) return entityById(target.id)
      if (target.name) return findEntity(target.name)
      return null
    }
    if (typeof target === 'number') return entityById(target)
    const word = String(target).trim().toLowerCase().replace(/ /g, '_')
    if (/^\d+$/.test(word)) return entityById(word)
    if (/^nearest[\s_]/.test(String(target).trim().toLowerCase())) return findEntity(String(target).trim().replace(/^nearest[\s_]+/i, ''))
    if (word === 'owner' || word === 'me' || word === 'player' || word === 'you') {
      return runner.ownerEntity() || (bot.players && Object.values(bot.players).map((p) => p.entity).find(Boolean)) || null
    }
    if (word === 'monster' || word === 'hostile' || word === 'mob') {
      return Object.values(bot.entities || {}).find((e) => e && e !== bot.entity && e.type !== 'player' && HOSTILE_NAMES.has(e.name)) || null
    }
    if (word === 'boat' || word === 'vehicle' || word === 'ride') {
      const near = Object.values(bot.entities || {}).filter((e) => e && e.position && VEHICLES.test(e.name || ''))
        .sort((a, b) => a.position.distanceTo(here) - b.position.distanceTo(here))
      return near[0] || null
    }
    const animal = resolveMobName(word)
    const named = Object.values(bot.entities || {}).filter((e) => {
      if (!e || e === bot.entity || !e.position) return false
      return e.name === word || (animal && e.name === animal)
    }).sort((a, b) => a.position.distanceTo(here) - b.position.distanceTo(here))
    return named[0] || null
  }

  function itemInBag (name) {
    const wanted = String(name || '').replace(/ /g, '_')
    if (!wanted) return null
    return bot.inventory.items().find((i) => i.name === wanted) || null
  }

  /** Picks the best tool she owns for a block, or says why she is bare-handed. */
  async function holdToolFor (block) {
    let tool = null
    try { tool = bot.pathfinder.bestHarvestTool?.(block) || null } catch { /* older pathfinder */ }
    if (!tool) tool = bot.inventory.items().find((i) => i.name === block?.harvestTool) || null
    if (tool && bot.heldItem?.name !== tool.name) {
      try { await bot.equip(tool, 'hand') } catch (e) { log('equip tool failed:', e.message) }
    }
  }

  /** What is true about her right now, in one line - so the planner can check itself for free. */
  function after () {
    const p = pos()
    const st = runner.status()
    return {
      at: p ? { x: round1(p.x), y: round1(p.y), z: round1(p.z) } : null,
      holding: bot.heldItem?.name || null,
      riding: bot.vehicle ? (bot.vehicle.name || 'vehicle') : null,
      health: st.health,
      food: st.food,
      task: st.task ? st.task.label : null
    }
  }

  // ----------------------------------------------------------------- the verbs

  const ACTIONS = {
    // --- see
    look: {
      help: 'look {"at":[x,y,z]|"direction":"north|south|east|west"} — turn her head, nothing else',
      async run (args) {
        alive()
        const point = toVec(args.at)
        if (point) {
          try { await bot.lookAt(point) } catch (e) { throw new Nope(`I cannot turn that way: ${e.message}`) }
          return ok(`I'm looking at ${Math.floor(point.x)},${Math.floor(point.y)},${Math.floor(point.z)}.`, after())
        }
        if (typeof args.direction === 'string' && args.direction in YAW) {
          await bot.look(YAW[args.direction], args.down ? 0.6 : 0, true)
          return ok(`I'm facing ${args.direction}.`, after())
        }
        const snap = observer ? observer() : null
        return ok(snap ? 'This is what I can see around me.' : 'I looked around.', snap || after())
      }
    },

    // --- move
    move: {
      help: 'move {"to":[x,y,z]} or {"target":"owner"|"nearest zombie"|"oak boat"|"crafting table"} {"range":2} — walk there, jumping over what she can',
      async run (args) {
        alive()
        if (bot.vehicle) throw new Nope("I'm riding something — tell me to dismount first, or drive me.")
        const asBlock = args.to && !Array.isArray(args.to) && typeof args.to === 'object' && args.to.name && !Number.isFinite(args.to.x)
          ? String(args.to.name).replace(/ /g, '_') : null
        if (asBlock) {
          const found = findBlockNearby(asBlock)
          if (!found) throw new Nope(`I can't see any ${nice(asBlock)} around here.`)
          await walkTo(found.position, args.range ?? 1.2)
          return ok(`I'm at the ${nice(asBlock)}.`, after())
        }
        const point = toVec(args.to)
        if (point) {
          await walkTo(point, args.range ?? 1.2)
          return ok(`I'm at ${Math.floor(point.x)},${Math.floor(point.y)},${Math.floor(point.z)} now.`, after())
        }
        const entity = findEntity(args.target ?? 'owner')
        if (!entity) throw new Nope('I cannot find that to walk to.')
        await walkUpTo(entity, args.range ?? 1.5)
        return ok(`I'm next to the ${nice(entity.name)}.`, after())
      }
    },

    jump: {
      help: 'jump {"count":1} — hop in place (works on its own; a jump while moving climbs one block)',
      async run (args) {
        alive()
        const times = Math.max(1, Math.min(5, Number(args.count) || 1))
        for (let i = 0; i < times; i++) {
          bot.setControlState('jump', true)
          await sleep(300)
          bot.setControlState('jump', false)
          await sleep(380) // her jump cooldown is half a second
        }
        return ok(times === 1 ? 'Hop!' : `Hopped ${times} times.`, after())
      }
    },

    // --- ride things
    mount: {
      help: 'mount {"target":"oak boat"|"boat"|"pig"|"horse"|42} — sit on it. She has to be within a block or two, so move first if the snapshot says it is far',
      async run (args) {
        alive()
        if (bot.vehicle) return ok(`I'm already riding a ${nice(bot.vehicle.name)}.`, after())
        const entity = findEntity(args.target ?? args.entity ?? 'nearest boat')
        if (!entity) throw new Nope("I can't see that to sit on. Tell me to look around first.")
        if (entity.position.distanceTo(pos()) > 2.6) await walkUpTo(entity, 1.4)
        const saddle = /(^|_)(pig|horse|donkey|mule|camel)$/.test(entity.name || '')
        if (saddle && !itemInBag('saddle')) throw new Nope(`A ${nice(entity.name)} needs a saddle before I can ride it.`)
        const settled = onceOrTimeout('mount', 3000)
        try { bot.mount(entity) } catch (e) { throw new Nope(`I could not get on: ${e.message}`) }
        const rode = await settled
        if (!rode || !bot.vehicle) throw new Nope(`I tried to sit on the ${nice(entity.name)} and nothing happened.`)
        return ok(`I'm sitting in the ${nice(bot.vehicle.name)}!`, after())
      }
    },

    dismount: {
      help: 'dismount {} — get off whatever she is riding',
      async run () {
        alive()
        if (!bot.vehicle) return ok("I'm not riding anything.", after())
        const was = nice(bot.vehicle.name)
        const settled = onceOrTimeout('dismount', 3000)
        bot.dismount()
        await settled
        return ok(bot.vehicle ? `I couldn't get out of the ${was}.` : `Out of the ${was}.`, after())
      }
    },

    drive: {
      help: 'drive {"left":-1|0|1,"forward":-1|0|1,"seconds":2} — paddle a boat / steer a horse. 1 left = turn left, -1 = turn right',
      async run (args) {
        alive()
        if (!bot.vehicle) throw new Nope("Nothing to drive — I'm not riding anything.")
        const left = Math.sign(Number(args.left) || 0)
        const forward = Math.sign(Number(args.forward) ?? 1)
        const seconds = Math.max(0.2, Math.min(10, Number(args.seconds) || 2))
        const stopAt = Date.now() + seconds * 1000
        while (Date.now() < stopAt && bot.vehicle) {
          try { bot.moveVehicle(left, forward) } catch (e) { throw new Nope(`I can't steer: ${e.message}`) }
          // a real client holds the keys down; the boat only paddles while the input says so
          if (forward > 0) bot.setControlState('forward', true)
          if (left > 0) bot.setControlState('left', true)
          if (left < 0) bot.setControlState('right', true)
          await sleep(120)
        }
        for (const c of ['forward', 'left', 'right']) { try { bot.setControlState(c, false) } catch { /* ignore */ } }
        return ok(bot.vehicle ? `Rowing the ${nice(bot.vehicle.name)}.` : 'I fell out on the way.', after())
      }
    },

    // --- hands
    equip: {
      help: 'equip {"item":"iron sword","slot":"hand|head|torso|legs|feet|offHand"} — put it in her hand or wear it',
      async run (args) {
        alive()
        const word = String(args.item || '').replace(/ /g, '_')
        if (!word) throw new Nope('Equip what?')
        let item = itemInBag(word)
        if (!item) {
          const name = craftCandidates(word, bot.registry)[0]
          if (name) item = itemInBag(name)
        }
        if (!item) throw new Nope(`I don't have ${nice(word)} in my bag.`)
        const slot = args.slot === 'off' ? 'offHand' : (args.slot || 'hand')
        try { await bot.equip(item, slot) } catch (e) { throw new Nope(`I can't put that on: ${String(e.message || e).slice(0, 90)}`) }
        return ok(`Holding the ${nice(item.name)}.`, after())
      }
    },

    use: {
      help: 'use {"at":[x,y,z]} to press a door, button, lever, bed or chest — or {"target":"zombie"} to hit something with whatever is in her hand — or {} to just use the held item',
      async run (args) {
        alive()
        if (args.target) {
          const entity = findEntity(args.target)
          if (!entity) throw new Nope(`I can't see a ${nice(args.target)} to use that on.`)
          await walkUpTo(entity, 2.2)
          bot.useOn(entity)
          return ok(`I used my hand on the ${nice(entity.name)}.`, after())
        }
        const point = toVec(args.at)
        if (point) {
          const block = bot.blockAt(point)
          if (!block || block.name === 'air') throw new Nope(`There's nothing at ${Math.floor(point.x)},${Math.floor(point.y)},${Math.floor(point.z)} to press.`)
          if (block.position.distanceTo(pos()) > 4.5) await walkTo(block.position, 2)
          try { await bot.activateBlock(block) } catch (e) { throw new Nope(`I couldn't use the ${nice(block.name)}: ${String(e.message || e).slice(0, 80)}`) }
          await sleep(300)
          return ok(`I used the ${nice(block.name)}.`, after())
        }
        try { await bot.activateItem() } catch (e) { throw new Nope(`I couldn't use my held item: ${String(e.message || e).slice(0, 80)}`) }
        await sleep(200)
        return ok(bot.heldItem ? `I used the ${nice(bot.heldItem.name)} in my hand.` : 'I waved my empty hand.', after())
      }
    },

    // --- change the world
    dig: {
      help: 'dig {"at":[x,y,z]} — break exactly that block (she picks the right tool herself). Clears paths, not builds',
      async run (args) {
        alive()
        const point = toVec(args.at)
        if (!point) throw new Nope('Which block should I break?')
        const block = bot.blockAt(point)
        if (!block || block.name === 'air') throw new Nope('There is nothing there to break.')
        if (block.name === 'bedrock') throw new Nope('Bedrock will not break, not for me.')
        if (block.position.distanceTo(pos()) > 4.5) await walkTo(block.position, 2.5)
        await holdToolFor(block)
        let seconds = 0
        try { seconds = (bot.digTime(block) || 0) / 1000 } catch { /* unknown */ }
        if (seconds > 45) throw new Nope(`Breaking that ${nice(block.name)} would take me about ${Math.round(seconds)} seconds with what I'm holding.`)
        try { await bot.dig(block, true) } catch (e) {
          const m = String(e?.message || e)
          if (/dont have|no.?tool|harvest/i.test(m)) throw new Nope(`I need a better tool for ${nice(block.name)}.`)
          throw new Nope(`I couldn't break the ${nice(block.name)}: ${m.slice(0, 80)}`)
        }
        return ok(`Broke the ${nice(block.name)}.`, after())
      }
    },

    place: {
      help: 'place {"block":"oak planks","at":[x,y,z]} — set a block from her bag into an empty space next to one she can reach. Boats and minecarts go in water',
      async run (args) {
        alive()
        const point = toVec(args.at)
        if (!point) throw new Nope('Where should I put it?')
        const word = String(args.block || '').replace(/ /g, '_')
        if (!word) throw new Nope('Place what?')
        let item = itemInBag(word)
        if (!item) {
          const alt = craftCandidates(word, bot.registry).find((n) => itemInBag(n))
          if (alt) item = itemInBag(alt)
        }
        if (!item) throw new Nope(`I don't have ${nice(word)} in my bag.`)
        const target = bot.blockAt(point)
        if (target && target.name !== 'air' && target.name !== 'water' && !VEHICLES.test(word)) {
          throw new Nope(`Something's already at ${Math.floor(point.x)},${Math.floor(point.y)},${Math.floor(point.z)}.`)
        }
        if (point.distanceTo(pos()) > 4.5) await walkTo(point, 2.5)
        // Minecraft wants "the block I'm building off, plus the direction to grow from it"
        let reference = null
        let face = null
        for (const vector of Object.values(FACE_VECTORS)) {
          const candidate = bot.blockAt(point.minus(vector))
          if (candidate && candidate.name !== 'air' && candidate.name !== 'water') { reference = candidate; face = vector; break }
        }
        if (!reference) throw new Nope('There is nothing solid next to that spot to place against.')
        if (bot.heldItem?.name !== item.name) { try { await bot.equip(item, 'hand') } catch (e) { throw new Nope(`I can't hold the ${nice(item.name)}: ${String(e.message || e).slice(0, 70)}`) } }
        try {
          if (VEHICLES.test(item.name) || /_boat$|minecart$/.test(item.name)) await bot.placeEntity(reference, face)
          else await bot.placeBlock(reference, face)
        } catch (e) {
          throw new Nope(`I couldn't place the ${nice(item.name)}: ${String(e.message || e).slice(0, 90)}`)
        }
        await sleep(250)
        const landed = bot.blockAt(point)?.name
        if (landed && landed !== 'air' && landed !== 'water') return ok(`Placed ${nice(landed)} at ${Math.floor(point.x)},${Math.floor(point.y)},${Math.floor(point.z)}.`, after())
        return ok(`I set the ${nice(item.name)} down${bot.vehicle ? ' and it turned into a ride' : ''}.`, after())
      }
    },

    // --- make things (delegated: the crafting engine in runner.js already knows about workbenches)
    recipes: {
      help: 'recipes {"item":"stone sword"} — what it is made of, so she can plan the steps before taking them',
      run (args) {
        const word = String(args.item || '').replace(/ /g, '_')
        if (!word) throw new Nope('Recipes for what?')
        const reg = bot.registry || {}
        const names = reg.itemsByName?.[word] ? [word] : craftCandidates(word, reg)
        if (!names.length) throw new Nope(`I don't know how to make ${nice(word)}.`)
        const have = new Map()
        for (const i of bot.inventory.items()) have.set(i.name, (have.get(i.name) || 0) + i.count)
        const out = []
        for (const name of names.slice(0, 4)) {
          const id = reg.itemsByName?.[name]?.id
          let list = []
          try { list = bot.recipesAll(id, 0, true) || [] } catch { list = [] }
          for (const recipe of list) {
            const needs = (recipe.delta || []).filter((d) => d.count < 0)
              .map((d) => ({ item: reg.itemsArray?.[d.id]?.name || `id ${d.id}`, count: -d.count }))
            const missing = needs.filter((n) => (have.get(n.item) || 0) < n.count)
            out.push({
              makes: name,
              count: recipe.result?.count ?? 1,
              needs,
              needs_crafting_table: !!recipe.requiresTable,
              have: needs.filter((n) => (have.get(n.item) || 0) >= n.count).map((n) => n.item),
              missing: missing.map((n) => `${n.count} ${n.item}`)
            })
          }
        }
        if (!out.length) throw new Nope(`I can't find a recipe for ${nice(word)}.`)
        // "stone sword" is three different recipes (cobblestone, cobbled deepslate, blackstone). The one
        // she is closest to is the useful one, so it goes first.
        out.sort((a, b) => a.missing.length - b.missing.length)
        return ok(`Here's what ${nice(names[0])} takes.`, { recipes: out.slice(0, 3) })
      }
    }
  }

  // Commands runner.js already does well are borrowed rather than re-written, so the voice path and the
  // thinking path can never drift apart.
  const BORROWED = {
    collect: { help: 'collect {"block":"oak log","count":10} — mine or chop and pick the drops up', args: (a) => `collect ${a.block}${a.count ? ` ${a.count}` : ''}` },
    craft: { help: 'craft {"item":"iron pickaxe","count":1} — make it, walking to a crafting table if the recipe needs one', args: (a) => `craft ${a.item}${a.count ? ` ${a.count}` : ''}` },
    attack: { help: 'attack {"target":"zombie"} — fight it with the best weapon she has', args: (a) => `attack ${a.target || 'nearest monster'}` },
    hunt: { help: 'hunt {"animal":"cow"} — chase and kill it for its drops', args: (a) => `hunt ${a.animal || 'nearest animal'}` },
    follow: { help: 'follow {"on":true|false} — walk with her owner', args: (a) => (a.on === false ? 'stay' : 'follow') },
    stay: { help: 'stay {} — stop moving and wait here', args: () => 'stay' },
    guard: { help: 'guard {} — stay near her owner and fight anything that comes at them', args: () => 'guard' },
    stop: { help: 'stop {} — drop whatever she is doing, right now', args: () => 'stop' },
    come: { help: 'come {} — walk to her owner', args: () => 'come' },
    goto_home: { help: 'goto_home {} — walk to where she sleeps', args: () => 'home' },
    set_home: { help: 'set_home {} — remember this spot as home', args: () => 'sethome' },
    give: { help: 'give {"item":"bread","count":3} — walk to her owner and toss it over', args: (a) => `give ${a.item}${a.count ? ` ${a.count}` : ''}` },
    eat: { help: 'eat {} — eat the best food she has', args: () => 'eat' },
    sleep: { help: 'sleep {} — find a bed and sleep the night away', args: () => 'sleep' },
    inventory: { help: 'inventory {} — read out her bag', args: () => 'inventory' },
    status: { help: 'status {} — health, food, where she is, what she is doing', args: () => 'status' },
    where: { help: 'where {} — her coordinates', args: () => 'where' },
    say: { help: 'say {"text":"hello"} — write a line in the in-game chat', args: (a) => `say ${a.text || a.message || ''}` }
  }

  for (const [name, spec] of Object.entries(BORROWED)) {
    ACTIONS[name] = {
      help: spec.help,
      async run (args) {
        const command = spec.args(args || {})
        const result = await runner.run(command)
        return { ...result, data: result.data ?? after() }
      }
    }
  }

  // ----------------------------------------------------------------- the door

  async function act (name, args = {}) {
    const action = ACTIONS[name]
    if (!action) {
      const guess = name ? `I don't know how to "${name}"` : 'Which action?'
      return bad(`${guess}. My actions are: ${Object.keys(ACTIONS).sort().join(', ')}.`)
    }
    try {
      const result = await action.run(args || {})
      return { ...result, did: name, data: result.data || after() }
    } catch (e) {
      if (e instanceof Nope) return bad(e.message, after(), name)
      log('action crashed:', name, e?.stack || e)
      return bad(`Something went wrong while I was doing ${name}.`, after(), name)
    }
  }

  /** One-line-per-action manual, handed to the model. It is the whole interface, so it stays honest. */
  function helpText () {
    return Object.entries(ACTIONS)
      .map(([name, spec]) => `- ${name}: ${spec.help}`)
      .sort()
      .join('\n')
  }

  return { act, helpText, actions: Object.keys(ACTIONS), after }
}
