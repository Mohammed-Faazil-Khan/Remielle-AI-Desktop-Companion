// A tiny HTTP API on 127.0.0.1 only. Remielle's Python side uses it to send commands, read status and poll events.
import http from 'node:http'

const MAX_BODY = 16 * 1024

function sendJson (res, status, body) {
  const data = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(data),
    'Cache-Control': 'no-store'
  })
  res.end(data)
}

function readJson (req) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    req.on('data', (c) => {
      size += c.length
      if (size > MAX_BODY) { reject(new Error('body too large')); req.destroy(); return }
      chunks.push(c)
    })
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8')
      if (!text.trim()) return resolve({})
      try { resolve(JSON.parse(text)) } catch { reject(new Error('invalid JSON')) }
    })
    req.on('error', reject)
  })
}

export function createApi ({ settings, events, getSession, log }) {
  const allowedHosts = new Set([`127.0.0.1:${settings.apiPort}`, `localhost:${settings.apiPort}`])

  const server = http.createServer(async (req, res) => {
    try {
      // This API controls a game character; stop web pages (DNS rebinding / cross-site requests) from reaching it.
      if (!allowedHosts.has(String(req.headers.host || '').toLowerCase()) || req.headers.origin) {
        return sendJson(res, 403, { ok: false, message: 'forbidden' })
      }
      const url = new URL(req.url, 'http://localhost')
      const session = getSession()
      const runner = session?.runner || null

      if (req.method === 'GET' && url.pathname === '/health') {
        return sendJson(res, 200, {
          ok: true,
          connected: !!runner,
          username: session?.bot?.username || settings.username,
          minecraft: session?.bot?.version || null,
          owner: runner ? (runner.state && (settings.owner || null)) : null
        })
      }

      if (req.method === 'GET' && url.pathname === '/status') {
        if (!runner) return sendJson(res, 200, { ok: true, connected: false })
        return sendJson(res, 200, { ok: true, ...runner.status() })
      }

      if (req.method === 'GET' && url.pathname === '/events') {
        const since = Number(url.searchParams.get('since') || 0)
        return sendJson(res, 200, { ok: true, last: events.lastId, events: events.since(since) })
      }

      if (req.method === 'GET' && url.pathname === '/observe') {
        if (!runner) return sendJson(res, 200, { ok: true, present: false, message: "I'm not in the Minecraft world right now." })
        return sendJson(res, 200, runner.observe())
      }

      if (req.method === 'GET' && url.pathname === '/actions') {
        return sendJson(res, 200, { ok: true, help: runner ? runner.actionHelp() : '' })
      }

      if (req.method === 'POST' && url.pathname === '/act') {
        const body = await readJson(req)
        const action = String(body.action || '').trim().toLowerCase()
        if (!action) return sendJson(res, 400, { ok: false, message: 'empty action' })
        if (!runner) return sendJson(res, 200, { ok: false, message: "I'm not in the Minecraft world right now." })
        const args = (body.args && typeof body.args === 'object' && !Array.isArray(body.args)) ? body.args : {}
        return sendJson(res, 200, await runner.act(action, args))
      }

      if (req.method === 'POST' && url.pathname === '/command') {
        const body = await readJson(req)
        const command = String(body.command || '').trim()
        if (!command) return sendJson(res, 400, { ok: false, message: 'empty command' })
        if (!runner) return sendJson(res, 200, { ok: false, message: "I'm not in the Minecraft world right now." })
        return sendJson(res, 200, await runner.run(command))
      }

      if (req.method === 'POST' && url.pathname === '/say') {
        const body = await readJson(req)
        const text = String(body.text || '').trim()
        if (!text) return sendJson(res, 400, { ok: false, message: 'empty text' })
        if (!runner) return sendJson(res, 200, { ok: false, message: "I'm not in the Minecraft world right now." })
        runner.say(text)
        return sendJson(res, 200, { ok: true, message: 'sent' })
      }

      if (req.method === 'POST' && url.pathname === '/note') {
        const body = await readJson(req)
        const text = String(body.text || '').trim()
        if (!text) return sendJson(res, 400, { ok: false, message: 'empty text' })
        // Something she wants said out loud without writing it in the game chat (a long thinking job, for example).
        events.push('note', {
          message: text.slice(0, 300),
          mood: String(body.mood || 'relaxed'),
          priority: Math.max(0, Math.min(4, Number(body.priority) || 1)),
          speak: true
        })
        return sendJson(res, 200, { ok: true, message: 'noted' })
      }

      return sendJson(res, 404, { ok: false, message: 'not found' })
    } catch (e) {
      log('api error:', e?.message || e)
      try { sendJson(res, 400, { ok: false, message: String(e?.message || e) }) } catch { /* connection already gone */ }
    }
  })

  return {
    server,
    listen () {
      return new Promise((resolve, reject) => {
        server.once('error', reject)
        server.listen(settings.apiPort, settings.apiHost, () => {
          server.removeListener('error', reject)
          resolve()
        })
      })
    },
    close () {
      return new Promise((resolve) => server.close(() => resolve()))
    }
  }
}
