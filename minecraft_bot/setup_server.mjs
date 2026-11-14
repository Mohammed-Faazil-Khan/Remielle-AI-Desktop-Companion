// One-time setup of a private Minecraft (Paper) server on this PC.
//
//   node setup_server.mjs                 download Paper and write the settings (does NOT accept the EULA)
//   node setup_server.mjs --accept-eula   same, and write eula.txt because YOU accepted Mojang's EULA
//
// Safe to run again: an existing world and settings are never overwritten (settings are only created once).
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadDotEnv } from './src/env.js'

const here = path.dirname(fileURLToPath(import.meta.url))
loadDotEnv() // fills process.env from the project's .env (real environment variables still win)
const env = process.env

const API = (env.PAPER_API_BASE || 'https://fill.papermc.io/v3').replace(/\/+$/, '')
const SERVER_DIR = path.resolve(here, env.MC_SERVER_DIR || '../minecraft_server')
const PORT = Number(env.MC_PORT) || 25565
const WANT_VERSION = (env.MC_SERVER_VERSION || '1.21.11').trim()
// PaperMC asks every client to identify itself with a contact URL.
const USER_AGENT = 'remielle-local-companion/1.0 (https://github.com/Mohammed-Faazil-Khan)'
const acceptEula = process.argv.includes('--accept-eula')

const say = (m) => console.log(m)
const die = (m) => { console.error(`\n[ERROR] ${m}`); process.exit(1) }

export function parseJavaMajor (text) {
  const m = String(text || '').match(/version "(\d+)(?:\.(\d+))?/)
  if (!m) return null
  const major = Number(m[1])
  return major === 1 ? Number(m[2]) : major // "1.8.0" style
}

export function javaNeededFor (mcVersion) {
  const [maj, min] = String(mcVersion).split('.').map((n) => parseInt(n, 10))
  if (maj >= 26) return 25
  if (maj === 1 && min >= 21) return 21
  if (maj === 1 && min >= 18) return 17
  return 8
}

export function serverProperties ({ port, motd = 'Remielle and Faazil' }) {
  return [
    '# Written by setup_server.mjs - a private server for you and Remielle.',
    `server-port=${port}`,
    'server-ip=127.0.0.1', // only this PC can connect
    'online-mode=false', // required so the bot can join without a Microsoft account
    'enforce-secure-profile=false',
    'white-list=false',
    'difficulty=normal',
    'gamemode=survival',
    'max-players=4',
    'spawn-protection=0',
    'view-distance=8',
    'simulation-distance=6',
    'enable-command-block=false',
    'allow-flight=true',
    `motd=${motd}`,
    ''
  ].join('\n')
}

async function getJson (url) {
  const res = await fetch(url, { headers: { 'User-Agent': USER_AGENT, Accept: 'application/json' } })
  if (!res.ok) throw new Error(`${url} answered ${res.status}`)
  return res.json()
}

function checkJava (mcVersion) {
  const need = javaNeededFor(mcVersion)
  const r = spawnSync('java', ['-version'], { encoding: 'utf8' })
  if (r.error) die(`Java is not installed (or not on PATH). Minecraft ${mcVersion} needs Java ${need}+. Install "Temurin ${need}" from adoptium.net, then run this again.`)
  const major = parseJavaMajor(`${r.stderr}${r.stdout}`)
  if (!major) die(`Could not read the Java version from: ${String(r.stderr).split('\n')[0]}`)
  if (major < need) die(`Java ${major} is too old. Minecraft ${mcVersion} needs Java ${need}+. Install "Temurin ${need}" from adoptium.net.`)
  say(`  Java ${major} found (needs ${need}+).`)
}

/** Picks the version to install: the wanted one, else the newest 1.21.x Paper has. */
async function chooseVersion () {
  const project = await getJson(`${API}/projects/paper`)
  const groups = project.versions || {}
  const all = Object.values(groups).flat()
  if (all.includes(WANT_VERSION)) return WANT_VERSION
  const fallback = all.filter((v) => /^1\.21(\.\d+)?$/.test(v)).sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))[0]
  if (!fallback) die(`Paper has no version ${WANT_VERSION} (and no 1.21.x). Set MC_SERVER_VERSION in .env to one from papermc.io.`)
  say(`  Paper has no ${WANT_VERSION}; using ${fallback} instead.`)
  return fallback
}

async function chooseBuild (version) {
  const builds = await getJson(`${API}/projects/paper/versions/${version}/builds`)
  const list = (Array.isArray(builds) ? builds : []).slice().sort((a, b) => b.id - a.id)
  const build = list.find((b) => b.channel === 'STABLE') || list[0]
  const dl = build?.downloads?.['server:default']
  if (!dl?.url) die(`No downloadable Paper build found for ${version}.`)
  return { id: build.id, channel: build.channel, url: dl.url, sha256: dl.checksums?.sha256, name: dl.name || `paper-${version}-${build.id}.jar` }
}

async function download (build, target) {
  const res = await fetch(build.url, { headers: { 'User-Agent': USER_AGENT } })
  if (!res.ok) die(`Download failed (${res.status}) from ${build.url}`)
  const bytes = Buffer.from(await res.arrayBuffer())
  if (build.sha256) {
    const got = createHash('sha256').update(bytes).digest('hex')
    if (got !== build.sha256) die('The downloaded file failed its checksum, so it was thrown away. Run this again.')
  }
  const tmp = `${target}.part`
  fs.writeFileSync(tmp, bytes)
  fs.renameSync(tmp, target)
}

async function main () {
  say('Setting up your private Minecraft server...')
  fs.mkdirSync(SERVER_DIR, { recursive: true })
  const jar = path.join(SERVER_DIR, 'paper.jar')
  const info = path.join(SERVER_DIR, 'paper.version.json')

  let installed = null
  try { installed = JSON.parse(fs.readFileSync(info, 'utf8')) } catch { /* first run */ }

  if (installed && fs.existsSync(jar) && installed.version === WANT_VERSION) {
    say(`  Paper ${installed.version} (build ${installed.build}) is already installed.`)
    checkJava(installed.version)
  } else {
    let version
    let build
    try {
      version = await chooseVersion()
      checkJava(version)
      build = await chooseBuild(version)
      say(`  Downloading Paper ${version} build ${build.id} (${build.channel})...`)
      await download(build, jar)
    } catch (e) {
      if (fs.existsSync(jar)) {
        say(`  Could not reach PaperMC (${e.message}); keeping the server you already have.`)
        return finish(installed?.version || WANT_VERSION)
      }
      die(`Could not download Paper: ${e.message}. Check your internet connection and run this again.`)
    }
    fs.writeFileSync(info, JSON.stringify({ version, build: build.id, channel: build.channel }, null, 2))
    installed = { version, build: build.id }
  }
  finish(installed.version)
}

function finish (version) {
  const props = path.join(SERVER_DIR, 'server.properties')
  if (!fs.existsSync(props)) {
    fs.writeFileSync(props, serverProperties({ port: PORT }))
    say('  Wrote server.properties (private: only this PC can join).')
  } else {
    say('  server.properties already exists - left untouched.')
  }
  const eula = path.join(SERVER_DIR, 'eula.txt')
  if (acceptEula) {
    fs.writeFileSync(eula, '# You accepted the Minecraft EULA (https://aka.ms/MinecraftEULA) by running setup with --accept-eula.\neula=true\n')
    say('  EULA accepted (eula.txt written).')
  } else if (!fs.existsSync(eula) || !/eula\s*=\s*true/i.test(fs.readFileSync(eula, 'utf8'))) {
    say('  EULA not accepted yet. Re-run with --accept-eula once you have read https://aka.ms/MinecraftEULA')
  }
  say(`\nDone. Minecraft ${version} server folder: ${SERVER_DIR}`)
  say(`In Minecraft: Multiplayer > Direct Connection > localhost${PORT === 25565 ? '' : ':' + PORT}   (use Java Edition ${version})`)
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((e) => die(e?.message || String(e)))
}
