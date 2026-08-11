'use client'
import { useState } from 'react'

export default function AgentTrace({ runs }: { runs: any[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const total = runs.reduce((a, r) => ({ tin: a.tin + r.input_tokens, tout: a.tout + r.output_tokens, usd: a.usd + Number(r.cost_usd) }),
    { tin: 0, tout: 0, usd: 0 })
  return (
    <div className="panel trace">
      <h3>Agent trace <span className="spacer" /><span className="muted" style={{ textTransform: 'none' }}>
        {(total.tin + total.tout).toLocaleString()} tok · ≈${total.usd.toFixed(3)}</span></h3>
      <div className="body">
        {runs.length === 0 && <p className="muted">No agent runs yet.</p>}
        {runs.map((r) => {
          const ms = r.ended_at ? new Date(r.ended_at).getTime() - new Date(r.started_at).getTime() : null
          return (
            <div key={r.id} className="run">
              <div className="row" style={{ gap: 6 }}>
                <b style={{ textTransform: 'capitalize' }}>{r.agent}</b>
                <span className={`chip ${r.status === 'succeeded' ? 'ok' : 'fail'}`}>{r.status}</span>
                <span className="muted mono">{r.model}</span><span className="spacer" />
                <span className="muted" style={{ fontSize: 11 }}>{r.turns} turns · {ms ? `${(ms / 1000).toFixed(1)}s` : '…'} · {(r.input_tokens + r.output_tokens).toLocaleString()} tok</span>
              </div>
              {r.error && <div style={{ color: 'var(--fail)', fontSize: 12 }}>{r.error}</div>}
              {r.steps.filter((s: any) => s.kind !== 'tool_result' || s.tool_name !== 'ask_workshop').map((s: any) => {
                const p = s.payload || {}
                if (s.kind === 'status_note') return <div key={s.id} className="step note">{p.text}</div>
                if (s.kind === 'workshop_chat') return (
                  <div key={s.id} className="step">
                    <div className="bubble q"><span className="muted">Warranty desk → workshop:</span> {p.question}</div>
                    <div className="bubble"><span className="muted">{p.technician?.name} ({p.technician?.role}):</span> {p.answer}
                      {p.attachment && <div><a href={p.attachment.url} target="_blank" rel="noreferrer">📎 {p.attachment.title}</a></div>}</div>
                  </div>)
                if (s.kind === 'decision') return <div key={s.id} className="step"><b>Decision:</b> {p.summary}</div>
                const k = String(s.id)
                return (
                  <div key={s.id} className="step">
                    <span className="mono" style={{ cursor: 'pointer' }} onClick={() => setOpen((o) => ({ ...o, [k]: !o[k] }))}>
                      {s.kind === 'tool_call' ? '→' : '←'} {s.tool_name}{s.duration_ms != null ? ` · ${s.duration_ms} ms` : ''} {open[k] ? '▾' : '▸'}
                    </span>
                    {open[k] && <pre>{JSON.stringify(p, null, 2)}</pre>}
                  </div>)
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}
