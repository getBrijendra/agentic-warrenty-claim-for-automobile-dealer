'use client'
import { money, pct, useBrand } from '@/lib/api'

export type Kpis = { agent: any; baseline: any; sim_now: string }

function Cmp({ better }: { better: boolean | null }) {
  if (better === null) return null
  return <span className={better ? 'better' : 'worse'}>{better ? '▲ better' : '▼ worse'}</span>
}

export default function KpiBar({ k }: { k: Kpis | null }) {
  const { meta } = useBrand()
  const cur = meta?.brand.currency ?? 'AED'
  if (!k) return <div className="kpis">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="kpi"><div className="label">…</div></div>)}</div>
  const a = k.agent, b = k.baseline
  const lower = (x: number | null, y: number | null) => (x === null || y === null ? null : x <= y)
  return (
    <div className="kpis">
      <div className="kpi" title="Claims rejected on first submission ÷ claims adjudicated">
        <div className="label">First-pass rejection</div>
        <div className="value">{pct(a.first_pass_rejection_rate)}</div>
        <div className="base">baseline {pct(b.first_pass_rejection_rate)} · {a.first_pass_rejected}/{a.submitted} <Cmp better={lower(a.first_pass_rejection_rate, b.first_pass_rejection_rate)} /></div>
      </div>
      <div className="kpi" title="Sim-days from WIP close to OEM payment">
        <div className="label">Cycle time (median · P90)</div>
        <div className="value">{a.cycle_median_days ?? '—'}d <span className="muted" style={{ fontSize: 14 }}>· {a.cycle_p90_days ?? '—'}d</span></div>
        <div className="base">baseline {b.cycle_median_days}d · {b.cycle_p90_days}d <Cmp better={lower(a.cycle_median_days, b.cycle_median_days)} /></div>
      </div>
      <div className="kpi" title="Final rejections + missed windows + never filed">
        <div className="label">Unrecovered</div>
        <div className="value">{money(a.unrecovered, cur)}</div>
        <div className="base">{pct(a.unrecovered_rate)} of value · baseline {pct(b.unrecovered_rate)} <Cmp better={lower(a.unrecovered_rate, b.unrecovered_rate)} /></div>
      </div>
      <div className="kpi">
        <div className="label">Recovered</div>
        <div className="value">{money(a.recovered, cur)}</div>
        <div className="base">{a.paid} paid · {money(a.at_risk_escalated, cur)} awaiting humans</div>
      </div>
      <div className="kpi">
        <div className="label">Auto-resolved</div>
        <div className="value">{a.auto_resolved}/{a.claims}</div>
        <div className="base">{a.escalated} escalated · {a.withdrawn} withdrawn · {a.reject_fix_accept} reject→fix→accept</div>
      </div>
      <div className="kpi" title={b.period}>
        <div className="label">Manual baseline</div>
        <div className="value" style={{ fontSize: 16, marginTop: 6 }}>{b.claims} claims · 12 mo</div>
        <div className="base">{b.window_missed} missed windows · {b.never_filed} never filed</div>
      </div>
    </div>
  )
}
