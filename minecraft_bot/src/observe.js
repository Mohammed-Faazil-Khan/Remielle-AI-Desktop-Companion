// What Remielle can perceive, in one object.
//
// She can only plan around what she is told about. "Sit in the boat" is unanswerable while the boat
// exists only as bytes in a chunk, and trivial once the snapshot says `oak boat, 3.1 blocks, to your
// west, sitting on the ground`. This module turns the raw mineflayer world into that snapshot.
//
// It is deliberately small and flat: a language model reads this on every thinking step, so every
// field costs tokens and every long list costs attention. Nothing here tries to be clever - no path
// finding, no guessing what she "probably" wants. It reports what is true right now, and lets the
// planner decide what to do about it.
import { HOSTILE_NAMES, ANIMALS } from './aliases.js'

// Things she can ride that are not boats. A pig needs a saddle first; the planner learns that from
// the item list, not from a rule in here.
const RIDEABLE_MOBS = new Set(['horse', 'donkey', 'mule', 'pig', 'camel', 'strider', 'ravager', 'skeleton_horse', 'zombie_horse'])

// Blocks worth walking to. Matched by name or suffix so one misspelled version constant cannot make
// her blind - anything the registry does not know is simply skipped.
const STATIONS = /(?:^|_)(?:crafting_table|furnace|smoker|blast_furnace|chest|barrel|ender_chest|anvil|enchanting_table|brewing_stand|loom|cartography_table|stonecutter|grindstone|smithing_table|cauldron|bed|_bed|door|door_(?:open|closed))$/
const ORE = /(?:^|deepslate_|nether_|raw_)(?:coal|iron|copper|gold|redstone|lapis|diamond|emerald|quartz|ancient_debris)(?:_|$)|ancient_debris/
const LOG = /(?:^|stripped_)\w*(?:log|wood|stem|block)$/
const AIR = new Set(['air', 'cave_air', 'void_air', 'structure_void'])

const r2 = (n) => Math.round(n * 100) / 100
const floor3 = (p) => ({ x: Math.floor(p.x), y: Math.floor(p.y), z: Math.floor(p.z) })

/** Which way she is looking, as a word a person would say. */
export function cardinal (yawRadians) {
  if (typeof yawRadians !== 'number' || !Number.isFinite(yawRadians)) return null
  // Minecraft: 0 = south (+z), 90 = west, 180 = north, -90 = east
  const deg = ((yawRadians * 180 / Math.PI) % 360 + 360) % 360
  const d = deg > 180 ? deg - 360 : deg
  if (d >= -45 && d < 45) return 'south'
  if (d >= 45 && d < 135) return 'west'
  if (d >= 135 || d <= -135) return 'north'
  return 'east'
}

/** A unit vector pointing where she looks, on the horizontal plane. */
export function forwardVector (yawRadians) {
  if (typeof yawRadians !== 'number' || !Number.isFinite(yawRadians)) return null
  return { x: -Math.sin(yawRadians), z: Math.cos(yawRadians) }
}

const kindOf = (e, registry) => {
  if (e.type === 'player') return 'player'
  if (e.name === 'item') return 'dropped_item'
  const name = String(e.name || '')
  if (/_boat$|minecart$/.test(name)) return 'vehicle'
  if (RIDEABLE_MOBS.has(name)) return 'rideable_mob'
  if (HOSTILE_NAMES.has(name) || e.type === 'hostile') return 'monster'
  if (ANIMALS.has(name) || e.type === 'animal') return 'animal'
  return e.type || 'other'
}

const isInteresting = (name) => STATIONS.test(name) || ORE.test(name) || LOG.test(name) || name === 'water' || name === 'lava'

/**
 * Everything she can sense right now. Never throws: a half-known world still beats no world, so each
 * section is built inside its own guard and a broken chunk just leaves that part out.
 */
export function createObserver ({ bot, shared, status = () => ({}), log = () => {}, reach = 16 }) {
  const registry = () => bot.registry || {}

  function nearbyEntities (pos) {
    const out = []
    for (const e of Object.values(bot.entities || {})) {
      if (!e || e === bot.entity || e.isValid === false || !e.position) continue
      const d = e.position.distanceTo(pos)
      if (d > 32) continue
      out.push({
        name: e.name || e.displayName || 'unknown',
        kind: kindOf(e, registry()),
        id: e.id,
        distance: r2(d),
        at: floor3(e.position),
        health: typeof e.health === 'number' ? Math.round(e.health) : undefined,
        on_fire: e.onFire === true || undefined
      })
    }
    out.sort((a, b) => a.distance - b.distance)
    return out.slice(0, 18)
  }

  /** The ground under her and the space she is standing in - the fastest way to spot a hole or a ceiling. */
  function footing (pos) {
    const at = (dx, dy, dz) => {
      try {
        const b = bot.blockAt(pos.offset(dx, dy, dz))
        return b && b.name && b.name !== 'unknown' ? b.name : 'air'
      } catch { return 'unknown' }
    }
    const f = forwardVector(bot.entity?.yaw) || { x: 0, z: 0 }
    const ahead = { x: Math.round(f.x), z: Math.round(f.z) }
    return {
      below: at(0, -1, 0),
      feet: at(0, 0, 0),
      head: at(0, 1, 0),
      in_front: at(ahead.x, 0, ahead.z),
      in_front_low: at(ahead.x, -1, ahead.z),
      in_front_high: at(ahead.x, 1, ahead.z)
    }
  }

  /** Notable blocks, grouped by what they are, nearest first. */
  function noteworthy (pos) {
    const grouped = new Map()
    const add = (name, p) => {
      const d = r2(p.distanceTo(pos))
      const seen = grouped.get(name)
      if (!seen || d < seen.distance) grouped.set(name, { name, distance: d, at: floor3(p), count: (seen ? seen.count : 0) + 1 })
      else seen.count += 1
    }
    const scan = (matching, count = 64) => {
      try {
        const found = bot.findBlocks({ matching, maxDistance: reach, count }) || []
        const names = registry().blocksArray || []
        for (const p of found) {
          const b = bot.blockAt(p)
          const name = b?.name || (typeof matching === 'number' ? names[matching]?.name : null) || 'block'
          add(name, p)
        }
      } catch (e) { log('observe scan failed:', e.message) }
    }
    scan((b) => b && isInteresting(b.name))
    try {
      const water = registry().blocksByName?.water?.id
      if (typeof water === 'number') scan(water, 8)
    } catch { /* water is a bonus */ }
    return [...grouped.values()].sort((a, b) => a.distance - b.distance).slice(0, 18)
  }

  /** What the items she has already add up to. */
  function craftable (hasTable) {
    const reg = registry()
    const outputs = reg.recipes ? Object.keys(reg.recipes).map(Number) : []
    const now = []
    const atATable = []
    if (typeof bot.recipesFor !== 'function' || !outputs.length) return { now, atATable }
    const inventory = bot.inventory
    for (const id of outputs) {
      const name = reg.itemsArray?.[id]?.name || reg.itemsById?.[id]?.name
      if (!name) continue
      try {
        if (bot.recipesFor(id, 0, 1, hasTable).length) now.push(name)
        else if (!hasTable && bot.recipesFor(id, 0, 1, true).length) atATable.push(name)
        // free the model early: it plans better with 20 options than with 800
        if (now.length > 40) break
      } catch { /* this version's recipe book is unreadable; carry on without it */ }
    }
    return { now: now.sort().slice(0, 30), atATable: atATable.sort().slice(0, 20) }
  }

  function observe () {
    const entity = bot.entity
    const pos = entity?.position
    const base = status()
    if (!pos) return { ok: true, present: false, message: "I am not standing in the world right now." }

    const yaw = entity.yaw
    const items = []
    try {
      const counts = new Map()
      for (const it of bot.inventory.items()) counts.set(it.name, (counts.get(it.name) || 0) + it.count)
      for (const [name, count] of [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 30)) items.push({ name, count })
    } catch (e) { log('observe inventory failed:', e.message) }

    const blocks = noteworthy(pos)
    const hasTable = blocks.some((b) => b.name === 'crafting_table')
    const gear = {}
    try {
      for (const slot of ['head', 'torso', 'legs', 'feet']) {
        const item = bot.inventory?.slots?.[bot.getEquipmentDestSlot(slot)]
        if (item?.name) gear[slot] = item.name
      }
    } catch { /* equipment is a bonus */ }

    return {
      ok: true,
      present: true,
      me: {
        name: bot.username,
        health: bot.health,
        food: bot.food,
        dead: base.dead === true || (typeof bot.health === 'number' && bot.health <= 0),
        at: floor3(pos),
        facing: cardinal(yaw),
        holding: bot.heldItem?.name || null,
        wearing: gear,
        riding: bot.vehicle ? { name: bot.vehicle.name, id: bot.vehicle.id } : null,
        on_ground: entity.onGround !== false,
        in_water: !!entity.isInWater,
        sneaking: !!entity.sneaking,
        sprinting: !!entity.sprinting,
        busy_with: base.task ? base.task.label : null,
        mode: base.mode || 'idle'
      },
      world: {
        dimension: base.dimension ?? bot.game?.dimension,
        day: bot.time?.day,
        time_of_day: bot.time?.timeOfDay,
        is_day: bot.time?.isDay ?? base.isDay,
        raining: !!bot.isRaining,
        home: base.home || shared?.home || null
      },
      owner: base.owner ? {
        name: base.owner,
        distance: base.ownerDistance ?? null,
        visible: !!base.ownerVisible
      } : null,
      footing: footing(pos),
      entities: nearbyEntities(pos),
      blocks,
      inventory: items,
      can_craft: craftable(hasTable)
    }
  }

  return observe
}
