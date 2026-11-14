import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { createApi } from '../src/api.js'
import { EventQueue } from '../src/events.js'

const settings = { apiHost: '127.0.0.1', apiPort: 3191, username: 'Remielle', owner: 'Faazil' }

function raw (path, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const rq = http.request({ host: '127.0.0.1', port: settings.apiPort, path, method, headers }, (res) => {
      let data = ''
      res.on('data', (c) => { data += c })
      res.on('end', () => resolve({ status: res.statusCode, json: data ? JSON.parse(data) : null }))
    })
    rq.on('error', reject)
    if (body) rq.write(body)
    rq.end()
  })
}

test('the control API', async (t) => {
  const events = new EventQueue()
  const said = []
  let session = null
  const api = createApi({ settings, events, getSession: () => session, log: () => {} })
  await api.listen()
  t.after(() => api.close())

  await t.test('works while she is not in the world', async () => {
    assert.deepEqual((await raw('/health')).json.connected, false)
    assert.deepEqual((await raw('/status')).json, { ok: true, connected: false })
    const r = await raw('/command', { method: 'POST', body: JSON.stringify({ command: 'follow' }) })
    assert.equal(r.status, 200)
    assert.equal(r.json.ok, false)
    assert.match(r.json.message, /not in the Minecraft world/)
    assert.deepEqual((await raw('/observe')).json, { ok: true, present: false, message: "I'm not in the Minecraft world right now." })
    assert.equal((await raw('/act', { method: 'POST', body: JSON.stringify({ action: 'mount' }) })).json.ok, false)
    assert.equal((await raw('/actions')).json.help, '')
  })

  session = {
    bot: { username: 'Remielle', version: '1.21.4' },
    runner: {
      state: {},
      status: () => ({ connected: true, health: 20 }),
      observe: () => ({ ok: true, present: true, me: { name: 'Remielle', at: { x: 0, y: 64, z: 0 } }, entities: [] }),
      actionHelp: () => '- mount: sit on it',
      act: async (name, args) => ({ ok: true, did: name, message: `did ${name}`, data: { at: args.at || null } }),
      run: async (text) => ({ ok: true, message: `ran ${text}` }),
      say: (text) => said.push(text)
    }
  }

  await t.test('runs commands, reports status, relays chat', async () => {
    assert.equal((await raw('/health')).json.connected, true)
    assert.equal((await raw('/status')).json.health, 20)
    assert.equal((await raw('/command', { method: 'POST', body: JSON.stringify({ command: 'come' }) })).json.message, 'ran come')
    await raw('/say', { method: 'POST', body: JSON.stringify({ text: 'hi there' }) })
    assert.deepEqual(said, ['hi there'])
  })

  await t.test('the thinking API: see, act, and know what she can act as', async () => {
    const seen = (await raw('/observe')).json
    assert.equal(seen.present, true)
    assert.equal(seen.me.name, 'Remielle')
    assert.match((await raw('/actions')).json.help, /mount/)
    const done = (await raw('/act', { method: 'POST', body: JSON.stringify({ action: 'dig', args: { at: [1, 63, 0] } }) })).json
    assert.equal(done.did, 'dig')
    assert.deepEqual(done.data.at, [1, 63, 0])
    assert.equal((await raw('/act', { method: 'POST', body: '{}' })).status, 400, 'an action with no name is a bad request')
    assert.equal((await raw('/act', { method: 'POST', body: JSON.stringify({ action: 'fly', args: 'nonsense' }) })).json.did, 'fly')
  })

  await t.test('events can be polled incrementally', async () => {
    events.push('a', { message: 'first' })
    events.push('b')
    let r = (await raw('/events?since=0')).json
    assert.deepEqual(r.events.map((e) => e.type), ['a', 'b'])
    r = (await raw(`/events?since=${r.last}`)).json
    assert.deepEqual(r.events, [])
  })

  await t.test('she can say something out loud without typing it in the game chat', async () => {
    const marker = (await raw('/events?since=0')).json.last
    assert.equal((await raw('/note', { method: 'POST', body: JSON.stringify({ text: ' give me a moment ', mood: 'happy', priority: 6 }) })).json.ok, true)
    assert.equal((await raw('/note', { method: 'POST', body: JSON.stringify({ text: 'x'.repeat(400) }) })).status, 200)
    assert.equal((await raw('/note', { method: 'POST', body: JSON.stringify({ text: '  ' }) })).status, 400)
    const got = (await raw(`/events?since=${marker}`)).json.events
    assert.deepEqual(got.map((e) => e.type), ['note', 'note'])
    assert.equal(got[0].message, 'give me a moment')
    assert.equal(got[0].mood, 'happy')
    assert.equal(got[0].priority, 4, 'a silly priority is clamped, not obeyed')
    assert.equal(got[0].speak, true)
    assert.equal(got[1].message.length, 300, 'a long note is trimmed rather than dropped')
    assert.equal(got[1].priority, 1, 'no priority means ordinary')
  })

  await t.test('bad input is rejected, not fatal', async () => {
    assert.equal((await raw('/command', { method: 'POST', body: '{nope' })).status, 400)
    assert.equal((await raw('/command', { method: 'POST', body: '{}' })).status, 400)
    assert.equal((await raw('/say', { method: 'POST', body: JSON.stringify({ text: '  ' }) })).status, 400)
    assert.equal((await raw('/command', { method: 'POST', body: JSON.stringify({ command: 'x'.repeat(40000) }) }).catch(() => ({ status: 0 }))).status === 200, false)
    assert.equal((await raw('/missing')).status, 404)
    assert.equal((await raw('/health')).status, 200, 'still alive afterwards')
  })

  await t.test('web pages and rebinding attacks cannot reach it', async () => {
    assert.equal((await raw('/status', { headers: { Origin: 'https://evil.example' } })).status, 403)
    assert.equal((await raw('/status', { headers: { Host: 'evil.example' } })).status, 403)
    assert.equal((await raw('/status', { headers: { Host: `localhost:${settings.apiPort}` } })).status, 200)
  })
})
