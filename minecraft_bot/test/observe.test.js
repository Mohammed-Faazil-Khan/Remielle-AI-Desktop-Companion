// Perception tests: if the snapshot does not say a thing exists, she cannot plan around it.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Vec3 } from 'vec3'
import { createObserver, cardinal, forwardVector } from '../src/observe.js'
import { makeBody, registry } from './fakeBody.js'

const mob = (id, name, type, x, y, z) => { const e = { id, name, type, position: new Vec3(x, y, z), isValid: true }; return e }

function world ({ items = [], blocks = {}, entities = {}, yaw = 0 } = {}) {
  const bot = makeBody({ items, blocks })
  bot.entity.yaw = yaw
  bot.entity.position = new Vec3(0, 64, 0)
  bot.entities = entities
  return bot
}

function observe (bot) {
  const seen = createObserver({
    bot,
    shared: { home: null },
    log: () => {},
    status: () => ({ owner: 'Faazil', ownerDistance: 4, mode: 'idle', home: null, task: null })
  })
  return seen()
}

test('a boat on the shore is visible, named, and typed as something to ride', () => {
  const snap = observe(world({
    blocks: { '0,63,0': 'grass_block' },
    entities: { 7: mob(7, 'oak_boat', 'other', 3, 64, 1) }
  }))
  const boat = snap.entities.find((e) => e.name === 'oak_boat')
  assert.ok(boat, 'she can see it at all')
  assert.equal(boat.kind, 'vehicle')
  assert.equal(boat.distance, 3.16)
  assert.deepEqual(boat.at, { x: 3, y: 64, z: 1 })
})

test('monsters, animals, players and litter are told apart', () => {
  const snap = observe(world({
    entities: {
      11: mob(11, 'zombie', 'mob', 2, 64, 0),
      12: mob(12, 'cow', 'animal', 4, 64, 0),
      13: mob(13, 'Faazil', 'player', 6, 64, 0),
      14: mob(14, 'item', 'item', 1, 64, 0)
    }
  }))
  const kinds = Object.fromEntries(snap.entities.map((e) => [e.name, e.kind]))
  assert.equal(kinds.zombie, 'monster')
  assert.equal(kinds.cow, 'animal')
  assert.equal(kinds.Faazil, 'player')
  assert.equal(kinds.item, 'dropped_item')
})

test('the ground under her and the block in front of her', () => {
  const snap = observe(world({
    blocks: { '0,63,0': 'grass_block', '0,64,1': 'stone' },
    yaw: 0 // south, +z
  }))
  assert.equal(snap.footing.below, 'grass_block')
  assert.equal(snap.footing.feet, 'air')
  assert.equal(snap.footing.in_front, 'stone')
  assert.equal(snap.me.facing, 'south')
})

test('a hole in front of her shows up as air over nothing', () => {
  const snap = observe(world({ blocks: { '0,63,0': 'grass_block' }, yaw: -Math.PI / 2 }))
  assert.equal(snap.me.facing, 'east')
  assert.equal(snap.footing.in_front, 'air')
  assert.equal(snap.footing.in_front_low, 'air')
})

test('workbenches and ore are reported with where they are', () => {
  const snap = observe(world({
    blocks: { '0,63,0': 'grass_block', '5,63,0': 'crafting_table', '-4,58,2': 'iron_ore', '1,63,-6': 'oak_log' }
  }))
  const named = Object.fromEntries(snap.blocks.map((b) => [b.name, b]))
  assert.equal(named.crafting_table.distance, 5.1)
  assert.deepEqual(named.crafting_table.at, { x: 5, y: 63, z: 0 })
  assert.ok(named.iron_ore, 'she can see ore')
  assert.ok(named.oak_log, 'and wood')
})

test('her bag, her hands and what she could already make', async () => {
  const bot = world({
    items: [{ name: 'oak_log', count: 6 }, { name: 'coal', count: 2 }],
    blocks: { '0,63,0': 'grass_block', '2,63,0': 'crafting_table' }
  })
  await bot.equip({ name: 'oak_log', count: 6 }, 'hand')
  const snap = observe(bot)
  assert.deepEqual(snap.inventory, [{ name: 'oak_log', count: 6 }, { name: 'coal', count: 2 }])
  assert.equal(snap.me.holding, 'oak_log')
  assert.ok(snap.can_craft.now.includes('oak_planks'), 'planks are one craft away')
})

test('riding is part of who she is right now', () => {
  const bot = world({ blocks: { '0,63,0': 'grass_block' } })
  bot.vehicle = { id: 7, name: 'oak_boat' }
  assert.deepEqual(observe(bot).me.riding, { name: 'oak_boat', id: 7 })
})

test('no world, no plan: an empty snapshot still answers politely', () => {
  const bot = world({})
  bot.entity.position = null
  const snap = observe(bot)
  assert.equal(snap.ok, true)
  assert.equal(snap.present, false)
  assert.match(snap.message, /not standing in the world/)
})

test('a broken world does not blind her completely', () => {
  const bot = world({ blocks: { '0,63,0': 'grass_block' } })
  bot.findBlocks = () => { throw new Error('chunk unloaded') }
  bot.inventory.items = () => { throw new Error('window closed') }
  const snap = observe(bot)
  assert.equal(snap.present, true)
  assert.equal(snap.me.health, 20)
  assert.deepEqual(snap.blocks, [])
  assert.deepEqual(snap.inventory, [])
})

test('cardinal and forward agree with each other', () => {
  assert.equal(cardinal(0), 'south')
  assert.equal(cardinal(Math.PI / 2), 'west')
  assert.equal(cardinal(Math.PI), 'north')
  assert.equal(cardinal(-Math.PI / 2), 'east')
  assert.equal(cardinal(null), null)
  assert.equal(Math.round(forwardVector(0).z), 1)
  assert.equal(Math.round(forwardVector(Math.PI / 2).x), -1)
})

test('setup: the registry these tests plan against is the server version', () => {
  assert.ok(registry.itemsByName.oak_planks, 'minecraft-data knows 1.21.11 items')
  assert.ok(registry.blocksByName.crafting_table)
})
