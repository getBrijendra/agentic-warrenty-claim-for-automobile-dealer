'use client'
import Link from 'next/link'
import { use, useCallback, useEffect, useRef, useState } from 'react'
import AgentTrace from '@/components/warranty/AgentTrace'
import DiffView from '@/components/warranty/DiffView'
import JobCardView from '@/components/warranty/JobCardView'
import OemClaimForm from '@/components/warranty/OemClaimForm'
import RuleChecklist from '@/components/warranty/RuleChecklist'
import VersionTimeline from '@/components/warranty/VersionTimeline'
import { STATUS_LABEL, get, money, post, progColour, useBrand, useEventStream } from '@/lib/api'

export default function ClaimWorkspace({ params }: { params: Promise<{ claimRef: string }> }) {
  const { claimRef } = use(params)
  const { meta } = useBrand()
  const [ws, setWs] = useState<any>(null)
  const [err, setErr] = useState<string | null>(null)
  const [diff, setDiff] = useState<any>(null)
  const [sel, setSel] = useState<number | null>(null)
  const t = useRef<ReturnType<typeof setTimeout> | null>(null)
  const load = useCallback(() => get(`/claims/${claimRef}`).then((d) => { setWs(d); setErr(null) }).catch((e) => setErr(String(e))), [claimRef])
  useEffect(() => { load() }, [load])
  useEventStream((e) => {
    if (e.data?.claim_ref === claimRef || e.type === 'reset') {
      if (t.current) clearTimeout(t.current)
      t.current = setTimeout(load, 300)
    }
  })
  if (err) return <div className="page"><div className="banner">{err}</div></div>
  if (!ws) return <div className="page muted">Loading…</div>
  const c = ws.claim
  const version = sel ? ws.versions.find((v: any) => v.version === sel) : ws.versions[ws.versions.length - 1]
  return (
    <div className="page">
      <div className="row" style={{ marginBottom: 12 }}>
        <Link href="/demo/warranty-claims">← Control room</Link>
        <h2 style={{ margin: 0 }}>{c.claim_ref}</h2>
        <span className="badge prog" style={{ background: progColour(meta, c.programme) }}>{c.programme}</span>
        <span className={`chip ${c.status === 'paid' ? 'ok' : ['escalated', 'rejected'].includes(c.status) ? 'fail' : ''}`}>{STATUS_LABEL[c.status]}</span>
        {c.claim_type && <span className="chip">{c.claim_type}{c.contract_type && c.contract_type !== c.claim_type ? ` · ${c.contract_type}` : ''}</span>}
        {c.oem_claim_id && <span className="chip">OEM {c.oem_claim_id}</span>}
        <span className="muted">claimed {money(c.amount_claimed, c.currency, 2)}{c.amount_paid ? ` · paid ${money(c.amount_paid, c.currency, 2)}` : ''}</span>
        <span className="spacer" />
        {ws.versions.length > 1 && (
          <select value={version?.version} onChange={(e) => setSel(Number(e.target.value))}
            style={{ background: 'var(--panel2)', color: 'var(--text)', border: '1px solid var(--line)', borderRadius: 6, padding: 4 }}>
            {ws.versions.map((v: any) => <option key={v.version} value={v.version}>v{v.version} ({v.built_by})</option>)}
          </select>
        )}
        {c.status === 'wip_closed' && <button className="primary" onClick={() => post(`/claims/${c.claim_ref}/run`).then(load)}>Run agents on this WIP</button>}
        {c.status === 'escalated' && <Link href="/demo/warranty-claims/review"><button>Open in review queue</button></Link>}
      </div>
      <div className="ws">
        <JobCardView jc={ws.job_card} />
        <div>
          <OemClaimForm programme={c.programme} layout={ws.layout} version={version} />
          <div style={{ height: 12 }} />
          <VersionTimeline ws={ws} onDiff={(a, b) => get(`/claims/${claimRef}/versions/${a}/diff/${b}`).then(setDiff)} />
          {diff && <DiffView diff={diff} onClose={() => setDiff(null)} />}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <RuleChecklist rules={ws.rules} version={ws.rules[0]?.version} />
          <AgentTrace runs={ws.agent_runs} />
        </div>
      </div>
    </div>
  )
}
