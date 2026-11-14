// Turns friendly words ("wood", "iron", "diamonds") into real block / item / mob names for the game version in use.
import { normalizeName } from './parser.js'

const WOOD_TYPES = ['oak', 'birch', 'spruce', 'jungle', 'acacia', 'dark_oak', 'cherry', 'mangrove', 'pale_oak']

// key -> { label, blocks: [block names], drops: [item names that end up in the inventory] }
const ORE = (name, drop, label = name) => ({ label, blocks: [`${name}_ore`, `deepslate_${name}_ore`], drops: [drop] })

const SIMPLE = {
  stone: { label: 'cobblestone', blocks: ['stone'], drops: ['cobblestone'] },
  cobblestone: { label: 'cobblestone', blocks: ['stone'], drops: ['cobblestone'] },
  cobble: { label: 'cobblestone', blocks: ['stone'], drops: ['cobblestone'] },
  deepslate: { label: 'cobbled deepslate', blocks: ['deepslate'], drops: ['cobbled_deepslate'] },
  coal: ORE('coal', 'coal'),
  iron: ORE('iron', 'raw_iron', 'raw iron'),
  gold: ORE('gold', 'raw_gold', 'raw gold'),
  copper: ORE('copper', 'raw_copper', 'raw copper'),
  diamond: ORE('diamond', 'diamond'),
  emerald: ORE('emerald', 'emerald'),
  lapis: ORE('lapis', 'lapis_lazuli', 'lapis lazuli'),
  lapis_lazuli: ORE('lapis', 'lapis_lazuli', 'lapis lazuli'),
  redstone: ORE('redstone', 'redstone'),
  sand: { label: 'sand', blocks: ['sand'], drops: ['sand'] },
  gravel: { label: 'gravel', blocks: ['gravel'], drops: ['gravel', 'flint'] },
  dirt: { label: 'dirt', blocks: ['dirt', 'grass_block'], drops: ['dirt'] },
  clay: { label: 'clay balls', blocks: ['clay'], drops: ['clay_ball'] },
  sugar_cane: { label: 'sugar cane', blocks: ['sugar_cane'], drops: ['sugar_cane'] },
  cactus: { label: 'cactus', blocks: ['cactus'], drops: ['cactus'] },
  pumpkin: { label: 'pumpkins', blocks: ['pumpkin'], drops: ['pumpkin'] },
  melon: { label: 'melon slices', blocks: ['melon'], drops: ['melon_slice'] },
  obsidian: { label: 'obsidian', blocks: ['obsidian'], drops: ['obsidian'] }
}

const LOG_WORDS = new Set(['wood', 'log', 'tree', 'timber', 'wood_log', 'logs'])

// try the word as written and with common plural endings put back ("oak_plank" -> "oak_planks")
function variants (key) {
  return [key, `${key}s`, `${key}es`]
}

function existing (registry, names) {
  return names.filter((n) => registry.blocksByName[n])
}

/**
 * @returns {{label: string, blocks: string[], drops: string[]} | null}
 */
export function resolveBlockTarget (word, registry) {
  const key = normalizeName(word)
  if (!key) return null

  if (LOG_WORDS.has(key)) {
    const logs = registry.blocksArray.map((b) => b.name).filter((n) => n.endsWith('_log') && !n.startsWith('stripped_'))
    if (logs.length) return { label: 'logs', blocks: logs, drops: logs }
  }

  for (const w of WOOD_TYPES) {
    if (key === w || key === `${w}_log` || key === `${w}_wood` || key === `${w}_tree`) {
      const name = `${w}_log`
      if (registry.blocksByName[name]) return { label: `${w.replace('_', ' ')} logs`, blocks: [name], drops: [name] }
    }
  }

  const simple = SIMPLE[key] || SIMPLE[key.replace(/_ore$/, '')]
  if (simple) {
    const blocks = existing(registry, simple.blocks)
    if (blocks.length) return { label: simple.label, blocks, drops: simple.drops }
  }

  // an exact Minecraft block name, e.g. "red_sand", "bamboo"
  for (const v of variants(key)) {
    if (registry.blocksByName[v]) return { label: v.replace(/_/g, ' '), blocks: [v], drops: [v] }
  }
  for (const suffix of ['_log', '_ore']) {
    if (registry.blocksByName[key + suffix]) return { label: (key + suffix).replace(/_/g, ' '), blocks: [key + suffix], drops: [key + suffix] }
  }
  return null
}

// ----------------------------------------------------------------------------- mobs

export const HOSTILE_NAMES = new Set([
  'zombie', 'zombie_villager', 'husk', 'drowned', 'skeleton', 'stray', 'bogged', 'spider', 'cave_spider', 'witch',
  'slime', 'magma_cube', 'phantom', 'pillager', 'vindicator', 'evoker', 'ravager', 'silverfish', 'blaze', 'ghast',
  'hoglin', 'zoglin', 'wither_skeleton', 'guardian', 'elder_guardian', 'creeper', 'endermite', 'vex', 'breeze',
  'enderman', 'warden', 'piglin_brute'
])

// never started automatically (they need special handling or are too dangerous for a melee bot)
export const RUN_FROM = new Set(['creeper', 'warden', 'ghast', 'ravager', 'evoker', 'elder_guardian', 'wither'])
export const LEAVE_ALONE = new Set(['enderman', 'zombified_piglin', 'piglin', 'iron_golem', 'wolf', 'bee', 'llama', 'polar_bear', 'panda'])

export const ANIMALS = new Set(['cow', 'pig', 'sheep', 'chicken', 'rabbit', 'mooshroom', 'goat'])

export function resolveMobName (word) {
  const key = normalizeName(word)
  if (!key) return null
  if (['nearest', 'closest', 'hostile', 'monster', 'enemy', 'mob', 'it', 'that', 'them', 'those', 'anything', 'everything'].includes(key)) return 'hostile'
  if (key === 'skeletons') return 'skeleton'
  return key
}

// ----------------------------------------------------------------------------- crafting

const MATERIAL_ORDER = ['diamond', 'iron', 'stone', 'wooden']
const TOOLS = new Set(['pickaxe', 'axe', 'shovel', 'sword', 'hoe'])

/** "pickaxe" -> ["diamond_pickaxe", "iron_pickaxe", "stone_pickaxe", "wooden_pickaxe"]; exact names stay as they are. */
export function craftCandidates (word, registry) {
  const key = normalizeName(word)
  if (!key) return []
  if (TOOLS.has(key)) return MATERIAL_ORDER.map((m) => `${m}_${key}`).filter((n) => registry.itemsByName[n])
  if (key.startsWith('wood_') && TOOLS.has(key.slice(5))) return [`wooden_${key.slice(5)}`]
  if (key === 'table' || key === 'crafting' || key === 'workbench') return ['crafting_table']
  if (key === 'plank' || key === 'wood_plank') return registry.itemsArray.map((i) => i.name).filter((n) => n.endsWith('_planks'))
  if (key === 'stick') return ['stick']
  if (key === 'torch') return ['torch']
  for (const v of variants(key)) {
    if (registry.itemsByName[v]) return [v]
  }
  return []
}
