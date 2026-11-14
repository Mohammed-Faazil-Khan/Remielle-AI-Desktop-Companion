// The bot understands one small, exact command language. Natural speech ("get me ten logs") is turned into
// these commands on the Python side (intent_router.py) or by the language model (an "MC: ..." line).
//
//   follow [player]       come            stop             stay             guard
//   attack <mob|nearest>  hunt <animal>   collect <block> [count]
//   craft <item> [count]  goto <x> <y> <z>|goto <player>    home   sethome   tp
//   give <item> [count]   eat             sleep            inventory        status
//   where                 say <text>      help

export const COMMAND_HELP = [
  'follow', 'come', 'stop', 'stay', 'guard', 'attack <mob>', 'hunt <animal>', 'collect <block> [n]',
  'craft <item> [n]', 'goto <x> <y> <z>', 'home', 'sethome', 'tp', 'give <item> [n]', 'eat', 'sleep',
  'inventory', 'status', 'where', 'jump [n]', 'say <text>'
]

const ALIASES = {
  follow: 'follow',
  come: 'come',
  stop: 'stop',
  cancel: 'stop',
  stay: 'stay',
  wait: 'stay',
  guard: 'guard',
  protect: 'guard',
  attack: 'attack',
  kill: 'attack',
  fight: 'attack',
  hunt: 'hunt',
  collect: 'collect',
  mine: 'collect',
  gather: 'collect',
  chop: 'collect',
  craft: 'craft',
  make: 'craft',
  goto: 'goto',
  go: 'goto',
  home: 'home',
  sethome: 'sethome',
  tp: 'tp',
  teleport: 'tp',
  give: 'give',
  eat: 'eat',
  sleep: 'sleep',
  inventory: 'inventory',
  inv: 'inventory',
  status: 'status',
  where: 'where',
  say: 'say',
  jump: 'jump',
  hop: 'jump',
  leap: 'jump',
  help: 'help'
}

const NUMBER_WORDS = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, fifteen: 15, twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60
}

export function toCount (token) {
  if (token === undefined || token === null) return null
  const t = String(token).toLowerCase()
  if (/^\d+$/.test(t)) return Math.max(1, Math.min(10000, parseInt(t, 10)))
  return NUMBER_WORDS[t] ?? null
}

/** "Oak Logs" -> "oak_log". Lowercase, underscores, simple plural removal. */
export function normalizeName (text) {
  let s = String(text || '').toLowerCase().trim().replace(/[^a-z0-9_ ]+/g, ' ').replace(/\s+/g, ' ').trim()
  s = s.replace(/^(?:the|some|a|an|more)\s+/, '')
  s = s.replace(/ /g, '_')
  if (/(?:zombie|cookie|pie|bie|goalie)s$/.test(s)) s = s.slice(0, -1) // zombies, cookies: the -ie is part of the word
  else if (s.endsWith('ies') && s.length > 4) s = s.slice(0, -3) + 'y'
  else if (/(?:ches|shes|sses|xes)$/.test(s)) s = s.slice(0, -2)
  else if (s.endsWith('s') && !/(?:ss|us|is)$/.test(s) && s.length > 3) s = s.slice(0, -1)
  return s
}

/**
 * @returns {{name: string, args: string[], count: number|null, raw: string} | null}
 */
export function parseCommand (text) {
  const raw = String(text || '').trim().replace(/^[!/]+/, '').trim()
  if (!raw) return null
  const tokens = raw.split(/\s+/)
  const name = ALIASES[tokens[0].toLowerCase()]
  if (!name) return null
  let args = tokens.slice(1)

  if (name === 'jump') return { name, args: [], count: args.map(toCount).find((n) => n !== null) ?? null, raw }

  if (name === 'say') return { name, args, count: null, raw, text: args.join(' ').trim() }

  // "go to 10 64 20" / "go home"
  if (tokens[0].toLowerCase() === 'go') {
    if (args[0] && args[0].toLowerCase() === 'to') args = args.slice(1)
    if (args.length === 1 && args[0].toLowerCase() === 'home') return { name: 'home', args: [], count: null, raw }
  }

  // a trailing or leading number is the count: "collect oak_log 10", "give 5 cobblestone"
  let count = null
  if (name === 'collect' || name === 'craft' || name === 'give') {
    const last = toCount(args[args.length - 1])
    const first = toCount(args[0])
    if (args.length > 1 && last !== null) { count = last; args = args.slice(0, -1) } else if (args.length > 1 && first !== null) { count = first; args = args.slice(1) }
  }
  return { name, args, count, raw }
}
