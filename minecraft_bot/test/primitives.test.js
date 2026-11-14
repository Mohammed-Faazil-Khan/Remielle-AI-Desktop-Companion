// Action tests. The rule each one checks: she only says it worked when the world actually changed.
import test from 'node:test'
import assert from 'node:assert/strict'
import { Vec3 } from 'vec3'
import { createActions } from '../src/primitives.js'
import { makeBody, makeRunnerStub } from './fakeBody.js'

function rig ({ items = [], blocks = {}, entities = {}, replies = {} } = {}) {
  const bot = makeBody({ items, blocks })
  bot.entities = entities
  const runner = makeRunnerStub(bot, replies)
  const actions = createActions({ bot, runner, observer: () => ({ present: true }), log: () => {} })
  return { bot, runner, actions }
}

const entity = (id, name, x, y, z, type = 'other') => {
  const e = { id, name, type, position: new Vec3(x, y, z), isValid: true }
  return e
}

test('sit in the boat: walk to it, ask the server, and only then say she is sitting', async () => {
  const boat = entity(7, 'oak_boat', 3, 64, 1)
  const { bot, actions } = rig({ blocks: { '0,63,0': 'grass_block' }, entities: { 7: boat } })
  const result = await actions.act('mount', { target: 'oak boat' })
  assert.equal(result.ok, true, result.message)
  assert.match(result.message, /sitting in the oak boat/i)
  assert.equal(bot.vehicle, boat, 'the server let her on')
  assert.ok(bot.log.mounts.includes('oak_boat'))
  assert.ok(bot.entity.position.distanceTo(boat.position) < 2, 'she walked over first')
})

test('a ride that the server never confirms is reported as a failure', async () => {
  const stubborn = entity(7, 'oak_boat', 1, 64, 0)
  stubborn.noSit = true // it will not take her
  const { bot, actions } = rig({ blocks: { '0,63,0': 'grass_block' }, entities: { 7: stubborn } })
  const result = await actions.act('mount', { target: 'boat' })
  assert.equal(result.ok, false)
  assert.match(result.message, /nothing happened/i)
  assert.equal(bot.vehicle, null)
})

test('a pig needs a saddle, and she says so instead of standing on it', async () => {
  const { actions } = rig({ entities: { 9: entity(9, 'pig', 1, 64, 0, 'animal') } })
  const result = await actions.act('mount', { target: 'pig' })
  assert.equal(result.ok, false)
  assert.match(result.message, /saddle/i)
})

test('getting out: only when she is in something', async () => {
  const idle = rig({})
  assert.match((await idle.actions.act('dismount')).message, /not riding anything/)

  const boat = entity(7, 'oak_boat', 1, 64, 0)
  const riding = rig({ entities: { 7: boat } })
  riding.bot.vehicle = boat
  const result = await riding.actions.act('dismount')
  assert.equal(result.ok, true)
  assert.equal(riding.bot.vehicle, null, 'she really got out')
})

test('driving a boat paddles for real and lets go afterwards', async () => {
  const boat = entity(7, 'oak_boat', 0, 64, 0)
  const { bot, actions } = rig({ entities: { 7: boat } })
  bot.vehicle = boat
  const result = await actions.act('drive', { forward: 1, left: 1, seconds: 0.4 })
  assert.equal(result.ok, true)
  assert.ok(bot.log.steers.length >= 2, 'keeps paddling while she means to move')
  assert.ok(bot.log.steers.every(([left, forward]) => left === 1 && forward === 1))
  assert.equal(bot.getControlState('forward'), false, 'her hands come off the keys')
})

test('she will not drive a horse she is not sitting on', async () => {
  const { actions } = rig({})
  assert.match((await actions.act('drive', {})).message, /not riding anything/i)
})

test('dig: the block is gone afterwards, or she did not dig it', async () => {
  const { bot, actions } = rig({ blocks: { '0,63,0': 'grass_block', '1,63,0': 'stone' } })
  const result = await actions.act('dig', { at: [1, 63, 0] })
  assert.equal(result.ok, true, result.message)
  assert.equal(bot.world.get('1,63,0'), undefined)
  assert.deepEqual(bot.log.digs, ['stone'])
})

test('dig: bedrock is not a thing she can do, and she says why', async () => {
  const { actions } = rig({ blocks: { '0,63,0': 'bedrock' } })
  const result = await actions.act('dig', { at: [0, 63, 0] })
  assert.equal(result.ok, false)
  assert.match(result.message, /bedrock/i)
})

test('dig: nothing there is not a block to break', async () => {
  const { actions } = rig({ blocks: { '0,63,0': 'grass_block' } })
  assert.match((await actions.act('dig', { at: [0, 64, 0] })).message, /nothing there/i)
  assert.match((await actions.act('dig', {})).message, /which block/i)
})

test('place: a wall goes up where she was told, one block at a time', async () => {
  const { bot, actions } = rig({
    items: [{ name: 'oak_planks', count: 10 }],
    blocks: { '0,63,0': 'grass_block', '0,63,1': 'grass_block' }
  })
  const result = await actions.act('place', { block: 'oak planks', at: [0, 64, 1] })
  assert.equal(result.ok, true, result.message)
  assert.equal(bot.world.get('0,64,1'), 'oak_planks')
  assert.equal(bot.heldItem.name, 'oak_planks', 'she held the block to place it')
})

test('place: she owns the block or she does not build', async () => {
  const { actions } = rig({ blocks: { '0,63,1': 'grass_block' } })
  const result = await actions.act('place', { block: 'cobblestone', at: [0, 64, 1] })
  assert.equal(result.ok, false)
  assert.match(result.message, /don't have cobblestone/i)
})

test('place: floating in mid air is refused, not silently dropped', async () => {
  const { bot, actions } = rig({ items: [{ name: 'oak_planks', count: 5 }] })
  const result = await actions.act('place', { block: 'oak planks', at: [0, 70, 0] })
  assert.equal(result.ok, false)
  assert.match(result.message, /nothing solid/i)
  assert.equal(bot.world.get('0,70,0'), undefined)
})

test('use: a door is pressed, an empty space is not', async () => {
  const { bot, actions } = rig({ blocks: { '0,63,0': 'grass_block', '0,64,1': 'oak_door' } })
  const result = await actions.act('use', { at: [0, 64, 1] })
  assert.equal(result.ok, true, result.message)
  assert.deepEqual(bot.log.activated, ['oak_door'])
  assert.match((await actions.act('use', { at: [5, 70, 5] })).message, /nothing at 5,70,5 to press/)
})

test('equip: the sword comes to her hand, and a missing item is a plain no', async () => {
  const { bot, actions } = rig({ items: [{ name: 'iron_sword', count: 1 }, { name: 'cobblestone', count: 12 }] })
  const result = await actions.act('equip', { item: 'iron sword' })
  assert.equal(result.ok, true, result.message)
  assert.equal(bot.heldItem.name, 'iron_sword')
  assert.match((await actions.act('equip', { item: 'diamond pickaxe' })).message, /don't have diamond pickaxe/i)
})

test('recipes: what it takes, and what she is short of', async () => {
  const { actions } = rig({ items: [{ name: 'cobblestone', count: 8 }] })
  const result = await actions.act('recipes', { item: 'stone sword' })
  assert.equal(result.ok, true, result.message)
  const recipe = result.data.recipes[0]
  assert.equal(recipe.makes, 'stone_sword')
  assert.ok(recipe.needs.some((n) => n.item === 'cobblestone' && n.count === 2))
  assert.ok(recipe.needs.some((n) => n.item === 'stick'), 'a sword needs a stick too')
  assert.equal(recipe.needs_crafting_table, true, 'she will have to stand at a workbench')
  assert.deepEqual(recipe.missing, ['1 stick'])
})

test('recipes: something with no recipe is said out loud', async () => {
  const { actions } = rig({})
  assert.match((await actions.act('recipes', { item: 'bedrock' })).message, /find a recipe for bedrock/i)
})

test('borrowed commands go through the same engine the voice uses', async () => {
  const { runner, actions } = rig({ items: [{ name: 'iron_ingot', count: 5 }] })
  await actions.act('craft', { item: 'iron pickaxe', count: 1 })
  await actions.act('collect', { block: 'oak log', count: 8 })
  await actions.act('attack', { target: 'zombie' })
  await actions.act('goto_home')
  await actions.act('give', { item: 'bread', count: 2 })
  assert.deepEqual(runner.calls, ['craft iron pickaxe 1', 'collect oak log 8', 'attack zombie', 'home', 'give bread 2'])
})

test('move: to coordinates, to her owner, or to a named thing', async () => {
  const owner = entity(11, 'Faazil', 9, 64, 9, 'player')
  const { bot, actions } = rig({ entities: { 11: owner }, blocks: { '4,63,0': 'crafting_table' } })
  await actions.act('move', { to: [3, 64, 4] })
  assert.ok(bot.entity.position.distanceTo(new Vec3(3, 64, 4)) <= 1.3, 'she arrives within reach, not on the exact pixel')
  const toOwner = await actions.act('move', { target: 'owner' })
  assert.equal(toOwner.ok, true, toOwner.message)
  assert.ok(bot.entity.position.distanceTo(owner.position) < 2)
  const toTable = await actions.act('move', { to: { name: 'crafting table' } })
  assert.equal(toTable.ok, true, toTable.message)
  assert.match((await actions.act('move', { target: 'ender dragon' })).message, /cannot find that/i)
})

test('jump: a hop is a hop, and it is bounded', async () => {
  const { bot, actions } = rig({})
  const started = Date.now()
  const result = await actions.act('jump', { count: 2 })
  assert.equal(result.ok, true)
  assert.equal(bot.getControlState('jump'), false, 'she lets go')
  assert.ok(Date.now() - started > 600)
})

test('an action she has never heard of tells her what she can do', async () => {
  const { actions } = rig({})
  const result = await actions.act('fly_to_moon')
  assert.equal(result.ok, false)
  assert.match(result.message, /don't know how to "fly_to_moon"/)
  assert.match(result.message, /mount/)
})

test('every reply is a sentence, a verdict and where she ended up', async () => {
  const { actions } = rig({})
  for (const [name, args] of [['mount', {}], ['dig', {}], ['place', {}], ['equip', {}], ['recipes', {}], ['use', {}], ['drive', {}], ['move', {}]]) {
    const result = await actions.act(name, args)
    assert.equal(typeof result.ok, 'boolean', name)
    assert.equal(typeof result.message, 'string', name)
    assert.ok(result.message.length > 3, `${name} says nothing`)
    assert.ok(result.data && typeof result.data === 'object', `${name} has no state to check`)
    assert.equal(result.did, name)
  }
})

test('a body that throws does not take her down', async () => {
  const { bot, actions } = rig({ blocks: { '0,63,0': 'grass_block' } })
  bot.dig = () => { throw new Error('the chunk vanished') }
  const result = await actions.act('dig', { at: [0, 63, 0] })
  assert.equal(result.ok, false)
  assert.match(result.message, /couldn't break the grass block/)
})

test('dead is not a time for planning', async () => {
  const bot = makeBody({})
  const runner = { state: { dead: true }, run: async () => ({ ok: true, message: 'x' }), status: () => ({}), ownerEntity: () => null }
  const dying = createActions({ bot, runner, log: () => {} })
  assert.match((await dying.act('jump', {})).message, /I am dead/i)
  bot.entity.position = null
  assert.match((await dying.act('dig', { at: [0, 63, 0] })).message, /not standing in the world/i)
})

test('the manual she is given covers every action', async () => {
  const { actions } = rig({})
  const help = actions.helpText()
  for (const name of actions.actions) assert.match(help, new RegExp(`^- ${name}: `, 'm'), `${name} has no help line`)
  assert.match(help, /mount \{"target"/)
  assert.ok(actions.actions.includes('observe') === false, 'observation is a separate door, not a verb')
})

test('look can hand back the whole snapshot when she is asked what she sees', async () => {
  let asked = 0
  const bot = makeBody({})
  const runner = makeRunnerStub(bot)
  const actions = createActions({ bot, runner, log: () => {}, observer: () => { asked++; return { ok: true, present: true, entities: [] } } })
  const result = await actions.act('look', {})
  assert.equal(asked, 1)
  assert.equal(result.ok, true)
  assert.ok(result.data.present)
})
