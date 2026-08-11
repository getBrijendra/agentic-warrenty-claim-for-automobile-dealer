'use client'

export function SourceBadge({ source, url }: { source: string; url?: string | null }) {
  if (source === 'public')
    return <a className="badge src-public" href={url ?? '#'} target="_blank" rel="noreferrer" title="Public manufacturer / dealer page — re-verify before the meeting">Public ↗</a>
  return <span className="badge src-illustrative" title="Dealer-side terms are confidential — this value is illustrative">Illustrative — confirm with OEM</span>
}

export default function RuleChecklist({ rules, version }: { rules: any[]; version?: number }) {
  const n = { pass: 0, warn: 0, fail: 0 } as Record<string, number>
  rules.forEach((r) => (n[r.result] += 1))
  return (
    <div className="panel">
      <h3>Rule checklist {version ? `· v${version}` : ''} <span className="spacer" />
        <span className="chip ok">{n.pass} pass</span><span className="chip warn">{n.warn} warn</span><span className="chip fail">{n.fail} fail</span></h3>
      <div className="body">
        {rules.length === 0 && <p className="muted">Rules not run yet.</p>}
        {rules.map((r) => (
          <div key={r.id} className={`rule ${r.result}`}>
            <span className="ico">{r.result === 'pass' ? '✓' : r.result === 'warn' ? '!' : '✕'}</span>
            <div>
              <div className="row" style={{ gap: 6 }}><b>{r.label}</b>{r.code && r.result !== 'pass' && <span className="chip">{r.code}</span>}<SourceBadge source={r.source} url={r.source_url} /></div>
              <div className="muted">{r.detail}</div>
              {r.result !== 'pass' && r.suggested_fix && <div style={{ fontSize: 12 }}>→ {r.suggested_fix}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
