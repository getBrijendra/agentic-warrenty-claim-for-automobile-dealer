'use client'
import { motion } from 'motion/react'
import { useRouter } from 'next/navigation'
import { STATUS_LABEL, money, progColour, useBrand } from '@/lib/api'

export type Card = {
  claim_ref: string; wip_no: string; programme: string; model: string; status: string; column: string
  claim_type: string | null; amount: number | null; amount_paid: number | null; priority: number
  window_due_at: string | null; flags: string[]; resubmissions: number; first_pass_result: string | null
  touched_by_human: boolean
}

export default function ClaimCard({ c, simNow, onRun }: { c: Card; simNow: string | null; onRun: (ref: string) => void }) {
  const { meta } = useBrand()
  const router = useRouter()
  const colour = progColour(meta, c.programme)
  const left = c.window_due_at && simNow ? (new Date(c.window_due_at).getTime() - new Date(simNow).getTime()) / 864e5 : null
  const open = !['paid', 'withdrawn', 'written_off', 'submitted', 'resubmitted', 'accepted'].includes(c.status)
  const prefix = meta?.programmes.find((p) => p.programme === c.programme)?.prefix ?? c.programme
  return (
    <motion.div layout layoutId={c.claim_ref} transition={{ type: 'spring', stiffness: 380, damping: 32 }}
      className="card" style={{ borderLeftColor: colour }} onClick={() => router.push(`/demo/warranty-claims/claims/${c.claim_ref}`)}>
      <div className="t">
        <span>WIP {c.wip_no}</span>
        <span className="badge prog" style={{ background: colour }}>{prefix}</span>
      </div>
      <div className="m" title={c.model}>{c.model}</div>
      <div className="row" style={{ gap: 4, justifyContent: 'space-between' }}>
        <span>{c.amount ? money(c.amount_paid ?? c.amount, meta?.brand.currency) : '—'}</span>
        {open && left !== null && <span className={left <= 3 ? 'urgent' : 'muted'}>{left < 0 ? 'window missed' : `${left.toFixed(1)}d left`}</span>}
      </div>
      <div className="chips" style={{ marginTop: 4 }}>
        <span className={`chip ${c.status === 'paid' ? 'ok' : c.status === 'escalated' || c.status === 'rejected' ? 'fail' : ''}`}>{STATUS_LABEL[c.status] ?? c.status}</span>
        {c.claim_type && c.claim_type !== 'BASE' && <span className="chip">{c.claim_type}</span>}
        {c.first_pass_result === 'rejected' && <span className="chip warn">rejected→fixed {c.resubmissions ? `v${c.resubmissions + 1}` : ''}</span>}
        {c.priority <= 10 && open && <span className="chip fail">priority</span>}
        {(c.flags || []).filter((f) => /^R\d\d$/.test(f)).slice(0, 3).map((f) => <span key={f} className="chip">{f}</span>)}
        {c.touched_by_human && <span className="chip">human</span>}
      </div>
      {c.status === 'wip_closed' && (
        <button className="small" style={{ marginTop: 6 }} onClick={(e) => { e.stopPropagation(); onRun(c.claim_ref) }}>Run one</button>
      )}
    </motion.div>
  )
}
