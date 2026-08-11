// One PGlite instance exposed over the Postgres wire protocol, with a SAFE multiplexer.
//
// Why not pglite-socket's built-in multiplexer: it queues each protocol message separately, so the extended-query
// messages (Parse/Bind/Describe/Execute … Sync) of two client connections can interleave on the single PGlite
// session — which yields errors or, worse, results routed to the wrong client. Here every connection's messages are
// grouped into units that end at Sync ('S') / Query ('Q') and executed atomically; while a transaction is open only
// the owning connection is served. A disconnect inside a transaction rolls it back.
//
// Usage: node infra/pglite/serve.mjs --port 5432 --data ./var/pglite/core [--max-connections 16]
import { PGlite } from '@electric-sql/pglite'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import net from 'node:net'

const HERE = dirname(fileURLToPath(import.meta.url))
const argv = process.argv.slice(2)
const flag = (k, d) => { const i = argv.indexOf(`--${k}`); return i >= 0 ? argv[i + 1] : d }
const maxConnections = Number(flag('max-connections', 64))
// One or more instances: --instance 5432=./var/pglite/core (repeatable), or legacy --port/--data
const specs = []
argv.forEach((a, i) => { if (a === '--instance') { const [p, ...d] = argv[i + 1].split('='); specs.push([Number(p), d.join('=')]) } })
if (!specs.length) specs.push([Number(flag('port', 5432)), flag('data', 'memory://')])

const CLEAN = '.clean_shutdown' // written after a graceful close; absent → the last stop was a crash / hard kill

async function open(data) {
  if (data.startsWith('memory://')) return PGlite.create(data)
  mkdirSync(data, { recursive: true })
  const marker = join(data, CLEAN)
  const fresh = !existsSync(join(data, 'PG_VERSION'))
  if (!fresh && !existsSync(marker)) {
    // Unclean last shutdown: probe in a child process (so a failed open holds no file handles here). If the data
    // dir is unreadable, move it aside and start fresh; the launcher reseeds empty databases.
    const probe = spawnSync(process.execPath, ['--no-wasm-tier-up', join(HERE, 'check.mjs'), data], { encoding: 'utf8' })
    if (probe.status !== 0) {
      const aside = `${data}.corrupt-${new Date().toISOString().replace(/[:.]/g, '-')}`
      renameSync(data, aside)
      console.error(`[pglite] ${data} could not be opened after an unclean shutdown — moved to ${aside}; starting fresh`)
      mkdirSync(data, { recursive: true })
    } else {
      console.log(`[pglite] ${data} survived an unclean shutdown (probe OK)`)
    }
  }
  if (existsSync(marker)) rmSync(marker)
  return PGlite.create(data)
}

async function serve(port, data) {
  const db = await open(data)
  await db.query('select 1')
  const queue = [] // { conn, bytes, resolve }
  let owner = null // connection that owns the open transaction
  let busy = false
  let nextId = 1

  async function pump() {
    if (busy) return
    busy = true
    try {
      while (queue.length) {
        let idx = 0
        if (owner && db.isInTransaction()) {
          idx = queue.findIndex((u) => u.conn === owner)
          if (idx === -1) break // wait for the owner's next unit
        }
        const unit = queue.splice(idx, 1)[0]
        if (unit.conn.closed) { unit.resolve(); continue }
        try {
          await db.execProtocolRawStream(unit.bytes, {
            onRawData: (chunk) => { if (!unit.conn.closed) unit.conn.socket.write(Buffer.from(chunk)) },
          })
        } catch (e) {
          console.error(`[pglite:${port}] protocol error conn#${unit.conn.id}:`, e?.message ?? e)
        }
        owner = db.isInTransaction() ? unit.conn : null
        unit.resolve()
      }
    } finally {
      busy = false
    }
  }

  function submit(conn, bytes) {
    return new Promise((resolve) => { queue.push({ conn, bytes, resolve }); pump() })
  }

  const server = net.createServer((socket) => {
    const conn = { id: nextId++, socket, closed: false, buf: Buffer.alloc(0), pending: [], started: false }
    if (server.connections > maxConnections) { socket.destroy(); return }
    socket.setNoDelay(true)
    let chain = Promise.resolve()

    socket.on('data', (chunk) => {
      conn.buf = Buffer.concat([conn.buf, chunk])
      const units = []
      for (;;) {
        if (!conn.started) {
          if (conn.buf.length < 8) break
          const len = conn.buf.readInt32BE(0)
          const code = conn.buf.readInt32BE(4)
          if (conn.buf.length < len) break
          const msg = conn.buf.subarray(0, len)
          conn.buf = conn.buf.subarray(len)
          if (code === 80877103 || code === 80877104) { socket.write('N'); continue } // SSL / GSS request → no
          if (code === 80877102) { socket.destroy(); return } // cancel request: unsupported
          conn.started = true
          units.push(Buffer.from(msg))
          continue
        }
        if (conn.buf.length < 5) break
        const type = String.fromCharCode(conn.buf[0])
        const len = 1 + conn.buf.readInt32BE(1)
        if (conn.buf.length < len) break
        const msg = conn.buf.subarray(0, len)
        conn.buf = conn.buf.subarray(len)
        if (type === 'X') { socket.end(); return }
        conn.pending.push(Buffer.from(msg))
        if (type === 'S' || type === 'Q' || type === 'H') {
          units.push(Buffer.concat(conn.pending))
          conn.pending = []
        }
      }
      for (const u of units) chain = chain.then(() => submit(conn, new Uint8Array(u)))
    })

    const close = async () => {
      if (conn.closed) return
      conn.closed = true
      if (owner === conn) {
        // roll back through the queue so it never overlaps another unit
        const rb = { id: 0, closed: false, socket: { write: () => {} } }
        const sql = Buffer.from('ROLLBACK\0')
        const q = Buffer.alloc(5); q.write('Q', 0); q.writeInt32BE(4 + sql.length, 1)
        owner = rb
        queue.unshift({ conn: rb, bytes: new Uint8Array(Buffer.concat([q, sql])), resolve: () => {} })
      }
      pump()
    }
    socket.on('close', close)
    socket.on('error', close)
  })

  server.maxConnections = maxConnections
  server.listen(port, '127.0.0.1', () => {
    console.log(`[pglite] listening on 127.0.0.1:${port} data=${data} maxConnections=${maxConnections} (unit-safe multiplexer)`)
  })
  // run internal SQL through the same queue so it never overlaps a client's unit
  const runSql = (sql) => {
    const s = Buffer.from(sql + '\0')
    const q = Buffer.alloc(5); q.write('Q', 0); q.writeInt32BE(4 + s.length, 1)
    const internal = { id: 0, closed: false, socket: { write: () => {} } }
    return new Promise((resolve) => { queue.push({ conn: internal, bytes: new Uint8Array(Buffer.concat([q, s])), resolve }); pump() })
  }
  return { server, db, runSql, data, idle: () => !busy && queue.length === 0 && !db.isInTransaction() }
}

const running = []
for (const [port, data] of specs) running.push(await serve(port, data)) // sequential init keeps peak memory low

// Durability: a clean db.close() flushes to disk; a hard kill (taskkill /F) may not. So we (1) checkpoint
// periodically when idle and (2) offer a graceful stop that works on Windows too: create the --stop-file.
const stopFile = flag('stop-file', null)
if (stopFile && existsSync(stopFile)) rmSync(stopFile)
let stopping = false
const shutdown = async (why) => {
  if (stopping) return
  stopping = true
  console.log(`[pglite] shutting down (${why}) — flushing ${running.length} database(s)`)
  for (const r of running) {
    r.server.close()
    try { await Promise.race([r.runSql('CHECKPOINT'), new Promise((ok) => setTimeout(ok, 5000))]) } catch {}
    try {
      await r.db.close()
      if (!r.data.startsWith('memory://')) writeFileSync(join(r.data, CLEAN), new Date().toISOString())
    } catch (e) { console.error('[pglite] close failed', e?.message ?? e) }
  }
  if (stopFile && existsSync(stopFile)) rmSync(stopFile)
  console.log('[pglite] closed cleanly')
  process.exit(0)
}
for (const sig of ['SIGINT', 'SIGTERM', 'SIGBREAK', 'SIGHUP']) process.on(sig, () => shutdown(sig))
setInterval(async () => {
  if (stopFile && existsSync(stopFile)) return shutdown('stop file')
}, 300)
setInterval(async () => {
  for (const r of running) if (!stopping && r.idle()) await r.runSql('CHECKPOINT')
}, 15000)
