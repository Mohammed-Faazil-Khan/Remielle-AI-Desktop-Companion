import test from 'node:test'
import assert from 'node:assert/strict'
import minecraftData from 'minecraft-data'
import { parseCommand, normalizeName, toCount } from '../src/parser.js'
import { resolveBlockTarget, resolveMobName, craftCandidates } from '../src/aliases.js'
import { EventQueue } from '../src/events.js'
import { parseDotEnv, readSettings } from '../src/env.js'

const registry = minecraftData('1.21.4')

test('parseCommand understands the command language', () => {
  assert.deepEqual(parseCommand('follow').name, 'follow')
  assert.equal(parseCommand('!follow').name, 'follow')
  assert.equal(parseCommand('/come').name, 'come')
  assert.equal(parseCommand('cancel').name, 'stop')
  assert.equal(parseCommand('mine iron 5').name, 'collect')
  assert.equal(parseCommand('banana split'), null)
  assert.equal(parseCommand(''), null)
  assert.equal(parseCommand(null), null)
})

test('counts can come first or last, as digits or words', () => {
  assert.deepEqual(parseCommand('collect oak_log 10').count, 10)
  assert.deepEqual(parseCommand('collect oak_log 10').args, ['oak_log'])
  assert.equal(parseCommand('give 5 cobblestone').count, 5)
  assert.deepEqual(parseCommand('give 5 cobblestone').args, ['cobblestone'])
  assert.equal(parseCommand('collect wood ten').count, 10)
  assert.equal(parseCommand('collect wood').count, null)
  assert.equal(toCount('twenty'), 20)
  assert.equal(toCount('banana'), null)
  assert.equal(toCount('999999'), 10000)
})

test('go to coordinates / go home', () => {
  assert.deepEqual(parseCommand('go to 10 64 -20').args, ['10', '64', '-20'])
  assert.equal(parseCommand('goto 1 2 3').name, 'goto')
  assert.equal(parseCommand('go home').name, 'home')
})

test('say keeps the whole sentence', () => {
  assert.equal(parseCommand('say hello everyone, I am here').text, 'hello everyone, I am here')
})

test('normalizeName handles plurals without breaking words that end in s', () => {
  assert.equal(normalizeName('Oak Logs'), 'oak_log')
  assert.equal(normalizeName('cactus'), 'cactus')
  assert.equal(normalizeName('lapis'), 'lapis')
  assert.equal(normalizeName('torches'), 'torch')
  assert.equal(normalizeName('glass'), 'glass')
  assert.equal(normalizeName('diamonds'), 'diamond')
})

test('block aliases resolve against the real game data', () => {
  const wood = resolveBlockTarget('wood', registry)
  assert.ok(wood.blocks.includes('oak_log') && wood.blocks.includes('birch_log'))
  assert.ok(!wood.blocks.some((n) => n.startsWith('stripped_')))
  assert.deepEqual(resolveBlockTarget('oak', registry).blocks, ['oak_log'])
  assert.deepEqual(resolveBlockTarget('stone', registry).drops, ['cobblestone'])
  const iron = resolveBlockTarget('iron', registry)
  assert.deepEqual(iron.blocks, ['iron_ore', 'deepslate_iron_ore'])
  assert.deepEqual(iron.drops, ['raw_iron'])
  assert.deepEqual(resolveBlockTarget('Diamonds', registry).drops, ['diamond'])
  assert.deepEqual(resolveBlockTarget('lapis lazuli', registry).drops, ['lapis_lazuli'])
  assert.ok(resolveBlockTarget('cactus', registry))
  assert.ok(resolveBlockTarget('red sand', registry))
  assert.equal(resolveBlockTarget('unobtainium', registry), null)
  assert.equal(resolveBlockTarget('', registry), null)
})

test('every alias target exists in the game data (so nothing silently matches nothing)', () => {
  for (const word of ['stone', 'coal', 'iron', 'gold', 'copper', 'diamond', 'emerald', 'lapis', 'redstone', 'sand', 'gravel', 'dirt', 'clay', 'sugar cane', 'cactus', 'pumpkin', 'melon', 'obsidian']) {
    const t = resolveBlockTarget(word, registry)
    assert.ok(t, `no target for ${word}`)
    for (const b of t.blocks) assert.ok(registry.blocksByName[b], `${word}: unknown block ${b}`)
    for (const d of t.drops) assert.ok(registry.itemsByName[d], `${word}: unknown drop ${d}`)
  }
})

test('mob names', () => {
  assert.equal(resolveMobName('the nearest'), 'hostile')
  assert.equal(resolveMobName(''), null)
  assert.equal(resolveMobName('cave spider'), 'cave_spider')
  assert.equal(resolveMobName('zombies'), 'zombie')
})

test('crafting candidates', () => {
  assert.deepEqual(craftCandidates('pickaxe', registry), ['diamond_pickaxe', 'iron_pickaxe', 'stone_pickaxe', 'wooden_pickaxe'])
  assert.deepEqual(craftCandidates('crafting table', registry), ['crafting_table'])
  assert.deepEqual(craftCandidates('torches', registry), ['torch'])
  assert.deepEqual(craftCandidates('oak planks', registry), ['oak_planks'])
  assert.ok(craftCandidates('planks', registry).includes('birch_planks'))
  assert.deepEqual(craftCandidates('banana', registry), [])
})

test('EventQueue numbers events and returns only newer ones', () => {
  const q = new EventQueue(3)
  const a = q.push('one')
  q.push('two', { speak: true, mood: 'happy' })
  q.push('three')
  q.push('four')
  assert.equal(a.id, 1)
  assert.equal(q.items.length, 3)
  assert.deepEqual(q.since(2).map((e) => e.type), ['three', 'four'])
  assert.deepEqual(q.since('nonsense').map((e) => e.type), ['two', 'three', 'four'])
  assert.equal(q.lastId, 4)
  assert.equal(q.items[0].speak, true)
  assert.equal(q.items[2].speak, false)
})

test('.env parsing and settings defaults', () => {
  const env = parseDotEnv('# c\nMC_OWNER="Faazil"\nMC_PORT=25570\nEMPTY=\nBROKEN\n')
  assert.deepEqual(env, { MC_OWNER: 'Faazil', MC_PORT: '25570' })
  const s = readSettings({ MC_PORT: '25570', MC_OWNER: ' Faazil ', MC_API_PORT: 'abc', MC_VERSION: '1.21.4' })
  assert.equal(s.port, 25570)
  assert.equal(s.owner, 'Faazil')
  assert.equal(s.apiPort, 3100) // junk falls back
  assert.equal(s.version, '1.21.4')
  assert.equal(s.host, '127.0.0.1')
  assert.equal(readSettings({}).version, false)
  assert.equal(readSettings({}).apiHost, '127.0.0.1')
})
