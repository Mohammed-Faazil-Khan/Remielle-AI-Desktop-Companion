// A small in-memory event log. The Python side polls /events?since=<id> and speaks the interesting ones.
export class EventQueue {
  constructor (max = 200) {
    this.max = max
    this.items = []
    this.nextId = 1
  }

  /**
   * @param {string} type  short machine name, e.g. 'hurt', 'task_done'
   * @param {object} fields message (first-person, speakable), mood (happy|sad|angry|surprised|relaxed),
   *                        priority (1 minor .. 3 important), speak (should she say it out loud?), plus any data
   */
  push (type, fields = {}) {
    const ev = { id: this.nextId++, t: Date.now(), type, mood: 'relaxed', priority: 1, speak: false, ...fields }
    this.items.push(ev)
    if (this.items.length > this.max) this.items.shift()
    return ev
  }

  since (id = 0) {
    const n = Number(id)
    const after = Number.isFinite(n) ? n : 0
    return this.items.filter((e) => e.id > after)
  }

  get lastId () {
    return this.nextId - 1
  }
}
