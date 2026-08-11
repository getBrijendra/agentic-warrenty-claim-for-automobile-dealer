'use client'
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { get, money, post, progColour, simTime, useBrand, useEventStream } from '@/lib/api'

const KIND: Record<string, string> = { goodwill: 'Goodwill request', pre_auth: 'Pre-authorisation', ta_case: 'TA case request', review: 'Review' }

export default function ReviewQueue() {
  const { meta } = useBrand()
  const [items, setItems] = useState<any[]>([])
  const [edit, setEdit] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  const load = useCallback(() => get('/review').then(setItems).catch(() => {}), [])
  useEffect(() => { load() }, [load])
  useEventStream((e) => { if (e.type.startsWith('escalation') || e.type === 'reset' || e.type === 'claim.status') load() })
  const act = async (id: string, action: string) => {
    try {
      await post(`/review/${id}/actions`, { action, draft_message: edit[id] ?? undefined })
      setMsg(`${action} recorded — the orchestrator resumes the claim`)
      load()
    } catch (e: any) { setMsg(String(e.message)) }
  }
  const open = items.filter((i) => i.status === 'open')
  const done = items.filter((i) => i.status !== 'open')
  return (
    <div className="page">
      <h2 style={{ marginTop: 0 }}>Human review queue <span className="muted" style={{ fontSize: 14 }}>· {open.length} open</span></h2>
      <div className="banner">GOODWILL, claims above AED 25,000 and any R01/R02/R09/R11/R12 outcome always come here. Approving a pre-authorisation
        or TA request sends it to the <b>Simulated OEM Gateway</b>, which grants it in this demo. Every action is logged.</div>
      {msg && <div className="banner" style={{ borderColor: 'var(--line)', color: 'var(--text)' }}>{msg}</div>}
      <div className="panel">
        {open.length === 0 && <div className="body muted">Nothing waiting for a human.</div>}
        {open.map((e) => (
          <div key={e.id} className="esc">
            <div>
              <div className="row">
                <span className="badge prog" style={{ background: progColour(meta, e.programme) }}>{e.programme}</span>
                <Link href={`/demo/warranty-claims/claims/${e.claim_ref}`}><b>{e.claim_ref}</b></Link>
                <span className="muted">WIP {e.wip_no} · {e.model}</span>
              </div>
              <div className="row" style={{ marginTop: 6 }}>
                <span className="chip fail">{e.reason_code}</span><span className="chip">{KIND[e.kind] ?? e.kind}</span>
                <span className="chip">from {e.source_agent}</span>
                {e.confidence != null && <span className="chip">confidence {Math.round(e.confidence * 100)}%</span>}
                {e.amount_claimed && <span className="chip">{money(e.amount_claimed, meta?.brand.currency, 2)}</span>}
              </div>
              <p><b>Agent recommendation:</b> {e.agent_recommendation}</p>
              {e.tried?.length > 0 && <><div className="muted">What was tried</div><ul style={{ marginTop: 2 }}>{e.tried.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></>}
              <div className="muted" style={{ fontSize: 11 }}>raised {simTime(e.created_at)}</div>
            </div>
            <div>
              <div className="muted">Drafted message (to OEM or customer)</div>
              <textarea rows={7} value={edit[e.id] ?? e.draft_message ?? ''} onChange={(ev) => setEdit({ ...edit, [e.id]: ev.target.value })} />
              <div className="row" style={{ marginTop: 8 }}>
                <button className="primary" onClick={() => act(e.id, edit[e.id] !== undefined ? 'edit' : 'approve')}>Approve &amp; send</button>
                <button onClick={() => act(e.id, 'override')} title="Submit as-is; a human override is logged and is not bound by agent guardrails">Override decision</button>
                <button className="danger" onClick={() => act(e.id, 'write_off')}>Write off</button>
              </div>
            </div>
          </div>
        ))}
      </div>
      {done.length > 0 && <>
        <h3>Resolved</h3>
        <div className="panel"><div className="body">
          <table className="lines"><thead><tr><th>Claim</th><th>Reason</th><th>Status</th><th>By</th><th>Actions</th></tr></thead>
            <tbody>{done.map((e) => (
              <tr key={e.id}><td><Link href={`/demo/warranty-claims/claims/${e.claim_ref}`}>{e.claim_ref}</Link></td><td>{e.reason_code} · {KIND[e.kind]}</td>
                <td>{e.status} → claim {e.claim_status}</td><td>{e.resolved_by}</td><td>{e.actions.map((a: any) => a.action).join(', ')}</td></tr>))}</tbody></table>
        </div></div>
      </>}
    </div>
  )
}
