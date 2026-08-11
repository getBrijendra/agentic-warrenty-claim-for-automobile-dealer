'use client'
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'
import { get, money, simTime, useBrand, useEventStream } from '@/lib/api'

export default function GatewayInbox() {
  const { meta } = useBrand()
  const [prog, setProg] = useState<string | null>(null)
  const [data, setData] = useState<any>(null)
  const [open, setOpen] = useState<string | null>(null)
  const load = useCallback(() => get(`/gateway/inbox${prog ? `?programme=${prog}` : ''}`).then(setData).catch(() => {}), [prog])
  useEffect(() => { load() }, [load])
  useEventStream((e) => { if (['oem.adjudicated', 'credit_note.posted', 'claim.status', 'reset'].includes(e.type)) load() })
  const progs = meta?.programmes.filter((p) => p.status === 'active') ?? []
  return (
    <div className="page">
      <h2 style={{ marginTop: 0 }}>Simulated OEM Gateway</h2>
      <div className="banner">This is a <b>simulation</b> of per-OEM warranty portals — not connected to Ford, JLR, Ferrari, Maserati, VinFast or Ford Trucks
        systems and not endorsed by them. Decisions are deterministic: the same rule packs plus gateway-only checks, evaluated on the payload received.
        Reviewer comments are generated from the reason codes only. Sim time {simTime(data?.now)}.</div>
      <div className="controls">
        <button className={`filter ${!prog ? 'on' : ''}`} onClick={() => setProg(null)}>All OEMs</button>
        {progs.map((p) => <button key={p.programme} className={`filter ${prog === p.programme ? 'on' : ''}`} onClick={() => setProg(p.programme)}
          style={prog === p.programme ? { background: p.colour, borderColor: p.colour } : { borderColor: p.colour }}>{p.display_name}</button>)}
      </div>
      <div className="grid2" style={{ gridTemplateColumns: '2fr 1fr' }}>
        <div className="panel">
          <h3>Payloads received ({data?.submissions?.length ?? 0})</h3>
          <div className="body">
            <table className="lines">
              <thead><tr><th>OEM claim</th><th>Dealer ref</th><th>v</th><th>Received</th><th>Decision</th><th>Reviewer</th></tr></thead>
              <tbody>{data?.submissions?.map((s: any) => {
                const k = `${s.oem_claim_id}-${s.version}`
                return [
                  <tr key={k} onClick={() => setOpen(open === k ? null : k)} style={{ cursor: 'pointer' }}>
                    <td className="mono">{s.oem_claim_id}</td>
                    <td><Link href={`/demo/warranty-claims/claims/${s.claim_ref}`} onClick={(e) => e.stopPropagation()}>{s.claim_ref}</Link></td>
                    <td>{s.version}</td><td>{simTime(s.received_at_sim)}</td>
                    <td>{s.status === 'under_review' ? <span className="chip">under review · due {simTime(s.expected_by_sim)}</span>
                      : <span className={`chip ${s.decision === 'rejected' ? 'fail' : 'ok'}`}>{s.decision} {s.rejection_codes?.join(',')}</span>}</td>
                    <td style={{ fontSize: 12 }}>{s.reviewer_comment ?? ''}</td>
                  </tr>,
                  open === k && <tr key={k + 'd'}><td colSpan={6}>
                    <div className="grid2">
                      <div><div className="muted">Payload as received</div><pre>{JSON.stringify(s.payload, null, 2)}</pre></div>
                      <div><div className="muted">Adjudication trace (incl. gateway-only checks)</div>
                        {s.rule_trace ? s.rule_trace.map((r: any, i: number) => (
                          <div key={i} className={`rule ${r.result}`}><span className="ico">{r.result === 'pass' ? '✓' : r.result === 'warn' ? '!' : '✕'}</span>
                            <div><b>{r.label}</b> {r.code && r.result === 'fail' && <span className="chip">{r.code}</span>}<div className="muted">{r.detail}</div></div></div>))
                          : <p className="muted">Hidden until the simulated decision date.</p>}
                      </div>
                    </div></td></tr>]
              })}</tbody>
            </table>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="panel">
            <h3>Credit notes (payment runs)</h3>
            <div className="body">
              {data?.credit_notes?.length === 0 && <p className="muted">No payment run yet.</p>}
              {data?.credit_notes?.map((c: any) => (
                <div key={c.oem_credit_ref} className="row" style={{ borderBottom: '1px solid var(--line)', padding: '4px 0' }}>
                  <span className="mono">{c.oem_credit_ref}</span><span className="muted">{c.claim_ref}</span><span className="spacer" />
                  <b>{money(c.amount, meta?.brand.currency, 2)}</b><span className="muted">{simTime(c.sim_paid_at)}</span>
                </div>))}
            </div>
          </div>
          <div className="panel">
            <h3>Authorisation requests</h3>
            <div className="body">
              {data?.preauths?.length === 0 && <p className="muted">None.</p>}
              {data?.preauths?.map((p: any) => (
                <div key={p.ticket} className="row" style={{ padding: '3px 0' }}>
                  <span className="mono">{p.ticket}</span><span className="chip">{p.kind}</span><span className={`chip ${p.status === 'approved' ? 'ok' : ''}`}>{p.status}</span>
                  <span className="muted">{p.claim_ref}</span>{p.ref && <span className="mono">{p.ref}</span>}
                </div>))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
