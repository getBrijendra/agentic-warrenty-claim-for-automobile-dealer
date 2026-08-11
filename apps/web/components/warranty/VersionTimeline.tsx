'use client'
import { money, simTime } from '@/lib/api'

type Ev = { at: string; title: string; sub?: string; tone?: 'good' | 'bad'; diff?: [number, number] }

export default function VersionTimeline({ ws, onDiff }: { ws: any; onDiff: (a: number, b: number) => void }) {
  const evs: Ev[] = []
  const cur = ws.claim.currency
  ws.versions.forEach((v: any) => evs.push({ at: v.created_at, title: `v${v.version} · ${v.built_by}`, sub: v.change_summary,
    diff: v.version > 1 ? [v.version - 1, v.version] : undefined }))
  ws.submissions.forEach((s: any) => evs.push({ at: s.created_at, title: `${s.kind} v${s.version} → ${s.oem_claim_id ?? 'format error'}`,
    sub: `sim ${simTime(s.sim_submitted_at)}` }))
  ws.adjudications.forEach((a: any) => evs.push({ at: a.created_at, title: `OEM ${a.decision}${a.rejection_codes?.length ? ' ' + a.rejection_codes.join(', ') : ''}`,
    sub: `"${a.reviewer_comment}" · sim ${simTime(a.decided_at_sim)}`, tone: a.decision === 'rejected' ? 'bad' : 'good' }))
  ws.credit_notes.forEach((c: any) => evs.push({ at: c.created_at, title: `Credit note ${c.oem_credit_ref}`, sub: `${money(c.amount, cur, 2)} · paid sim ${simTime(c.sim_paid_at)}`, tone: 'good' }))
  ws.escalations.forEach((e: any) => evs.push({ at: e.created_at, title: `Escalated (${e.reason_code}) · ${e.status}`, sub: e.agent_recommendation, tone: 'bad' }))
  evs.sort((a, b) => a.at.localeCompare(b.at))
  return (
    <div className="panel">
      <h3>Version history &amp; OEM responses</h3>
      <div className="body">
        {evs.length === 0 && <p className="muted">Nothing yet.</p>}
        <div className="timeline">
          {evs.map((e, i) => (
            <div key={i} className={`tl ${e.tone ?? ''}`}>
              <b>{e.title}</b>
              {e.sub && <div className="muted" style={{ fontSize: 12 }}>{e.sub}</div>}
              {e.diff && <button className="small" style={{ marginTop: 4 }} onClick={() => onDiff(e.diff![0], e.diff![1])}>diff v{e.diff[0]} → v{e.diff[1]}</button>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
