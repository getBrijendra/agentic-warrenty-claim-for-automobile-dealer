'use client'

const show = (v: any) => (v === undefined ? '∅' : v === null ? 'null' : typeof v === 'object' ? JSON.stringify(v) : String(v))

export default function DiffView({ diff, onClose }: { diff: any; onClose: () => void }) {
  return (
    <div className="panel diff" style={{ marginTop: 12 }}>
      <h3>Diff v{diff.a} → v{diff.b} · {diff.built_by} <span className="spacer" /><button className="small" onClick={onClose}>✕</button></h3>
      <div className="body">
        <p className="muted">{diff.summary}</p>
        {diff.changes.length === 0 && <p className="muted">No field changes.</p>}
        {diff.changes.map((c: any) => (
          <div key={c.path} className="chg">
            <span className="mono">{c.path}</span>
            <span className="before">{show(c.before)}</span>
            <span className="after">{show(c.after)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
