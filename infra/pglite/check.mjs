// Probe: can each PGlite data directory be opened? Exit code 1 if any fails.  node check.mjs <dir> [...]
// serve.mjs runs this in a child process so a failed open does not keep file handles in the server process.
import { PGlite } from '@electric-sql/pglite'
let bad = 0
for (const d of process.argv.slice(2)) {
  try { const db = await PGlite.create(d); await db.query('select 1'); console.log(d, 'OK'); await db.close() }
  catch (e) { console.log(d, 'FAIL', e.message); bad++ }
}
process.exit(bad ? 1 : 0)
