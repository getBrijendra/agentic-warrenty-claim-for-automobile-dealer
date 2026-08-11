'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import AgentFeed, { type FeedItem } from '@/components/warranty/AgentFeed'
import KpiBar, { type Kpis } from '@/components/warranty/KpiBar'
import PipelineBoard from '@/components/warranty/PipelineBoard'
import type { Card } from '@/components/warranty/ClaimCard'
import { get, post, simTime, useBrand, useEventStream } from '@/lib/api'

type Clock = { base_sim: string; base_real: string; speed: number; paused: boolean; now: string }

function useSimNow(clock: Clock | null) {
  const [now, setNow] = useState<string | null>(null)
  useEffect(() => {
    if (!clock) return
    const tick = () => {
      if (clock.paused) return setNow(clock.base_sim)
      const elapsed = (Date.now() - new Date(clock.base_real).getTime()) / 1000
      setNow(new Date(new Date(clock.base_sim).getTime() + elapsed * clock.speed * 1000).toISOString())
    }
    tick()
    const t = setInterval(tick, 500)
    return () => clearInterval(t)
  }, [clock])
  return now
}

export default function ControlRoom() {
  const { meta, refresh } = useBrand()
  const [board, setBoard] = useState<{ columns: any[]; cards: Card[] }>({ columns: [], cards: [] })
  const [kpis, setKpis] = useState<Kpis | null>(null)
  const [clock, setClock] = useState<Clock | null>(null)
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [filter, setFilter] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [drawer, setDrawer] = useState<null | { accuracy?: any; cost?: any }>(null)
  const simNow = useSimNow(clock)
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    get('/claims').then(setBoard).catch(() => {})
    get<Kpis>('/kpis').then(setKpis).catch(() => {})
    get<Clock>('/simclock').then(setClock).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])
  const reloadSoon = () => {
    if (pending.current) clearTimeout(pending.current)
    pending.current = setTimeout(() => get('/claims').then(setBoard).catch(() => {}), 250)
  }
  const flash = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3500) }

  const connected = useEventStream((e) => {
    const d = e.data
    if (e.type === 'claim.status') {
      reloadSoon()
      const tone = ['paid', 'accepted'].includes(d.to) ? 'good' : ['escalated', 'rejected', 'written_off'].includes(d.to) ? 'bad' : undefined
      if (['escalated', 'rejected', 'paid', 'withdrawn', 'accepted', 'resubmitted'].includes(d.to)) {
        push({ key: `s${e.id}`, at: d.sim_at, agent: d.to === 'paid' || d.to === 'accepted' || d.to === 'rejected' ? 'oem-gateway' : 'orchestrator',
          wip: d.wip_no, claim_ref: d.claim_ref, programme: d.programme, kind: 'status', tone,
          text: `${d.to.replace('_', ' ').toUpperCase()} — ${d.reason}` })
      }
    } else if (e.type === 'agent.step') {
      if (d.kind === 'status_note' || d.kind === 'workshop_chat')
        push({ key: `a${e.id}`, at: d.sim_at, agent: d.agent, wip: d.wip_no, claim_ref: d.claim_ref, programme: d.programme,
          kind: d.kind === 'workshop_chat' ? 'workshop' : 'note', text: d.text, question: d.question })
    } else if (e.type === 'kpi.update') setKpis(e.data)
    else if (e.type === 'simclock.tick') setClock(e.data)
    else if (e.type === 'reset') { setFeed([]); load() }
  })
  const push = (it: FeedItem) => setFeed((f) => [it, ...f].slice(0, 250))

  const act = async (fn: () => Promise<any>, msg?: string) => {
    setBusy(true)
    try { await fn(); if (msg) flash(msg) } catch (err: any) { flash(String(err.message ?? err)) } finally { setBusy(false); load() }
  }
  const progs = meta?.programmes.filter((p) => p.status === 'active') ?? []
  const cards = filter ? board.cards.filter((c) => c.programme === filter) : board.cards
  const waiting = board.cards.filter((c) => c.status === 'wip_closed' && (!filter || c.programme === filter)).length

  return (
    <div className="page">
      <KpiBar k={kpis} />
      <div className="controls">
        <button className={`filter ${!filter ? 'on' : ''}`} onClick={() => setFilter(null)}>All</button>
        {progs.map((p) => (
          <button key={p.programme} className={`filter ${filter === p.programme ? 'on' : ''}`} onClick={() => setFilter(p.programme)}
            style={filter === p.programme ? { background: p.colour, borderColor: p.colour } : { borderColor: p.colour }}>
            {p.display_name}
          </button>
        ))}
        <span className="spacer" />
        <span className="muted">Mode</span>
        <button disabled={busy} onClick={() => act(() => post('/mode', { mode: meta?.mode === 'live' ? 'replay' : 'live' }).then(refresh))}
          title="LIVE = model calls through the llm-gateway (FAKE provider today); REPLAY = recorded run played back">
          {!meta ? '…' : meta.mode === 'live' ? '● LIVE' : '↺ REPLAY'}
        </button>
        <span className="clock" title="Simulated clock">Sim {simTime(simNow)} {clock?.paused ? '⏸' : '▶'}</span>
        <button disabled={busy} onClick={() => act(() => post('/simclock', { action: clock?.paused ? 'resume' : 'pause' }))}>{clock?.paused ? '▶ Resume' : '⏸ Pause'}</button>
        <button disabled={busy} onClick={() => act(() => post('/simclock', { action: 'speed', multiplier: 1 }))}>1x</button>
        <button disabled={busy} onClick={() => act(() => post('/simclock', { action: 'speed', multiplier: 10 }))}>10x</button>
        <button className="primary" disabled={busy || waiting === 0}
          onClick={() => act(() => post('/runs', { scope: 'batch', programme: filter }), `Running agents on ${waiting} WIPs`)}>
          Run agents{waiting ? ` (${waiting})` : ''}
        </button>
        <button className="danger" disabled={busy} onClick={() => act(() => post('/admin/reset'), 'Reset: claims cleared, WIPs restored')}>Reset</button>
        <button disabled={busy} onClick={async () => setDrawer({ accuracy: await get('/admin/accuracy').catch(() => null), cost: await get('/admin/cost').catch(() => null) })}>Admin</button>
      </div>
      <div className="room">
        <PipelineBoard columns={board.columns} cards={cards} simNow={simNow}
          onRun={(ref) => act(() => post(`/claims/${ref}/run`), `Running ${ref}`)} />
        <AgentFeed items={feed} connected={connected} />
      </div>
      <p className="muted" style={{ marginTop: 12, fontSize: 12 }}>
        Agents working on Autoline-shaped data against OEM rules. OEM side = <b>Simulated OEM Gateway</b> (no live OEM connection).
        Model provider: {meta?.llm_provider}{meta?.fake_llm ? ' (deterministic fake — no real AI reasoning)' : ''} · roles {meta?.models.main} / {meta?.models.fast}.
      </p>
      {drawer && <AdminDrawer data={drawer} onClose={() => setDrawer(null)} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}

function AdminDrawer({ data, onClose }: { data: { accuracy?: any; cost?: any }; onClose: () => void }) {
  const t = data.accuracy?.totals
  return (
    <div className="panel" style={{ position: 'fixed', top: 60, right: 16, width: 560, maxHeight: '80vh', overflow: 'auto', zIndex: 40 }}>
      <h3>Admin · agent accuracy &amp; cost <span className="spacer" /><button className="small" onClick={onClose}>✕</button></h3>
      <div className="body">
        <p className="muted">Scored against the hidden seed manifest (read only by workshop-sim; never by agent tools).</p>
        {t && <div className="row">
          <span className="chip ok">caught by agent {t.caught_by_agent}</span><span className="chip warn">caught by gateway {t.caught_by_gateway}</span>
          <span className="chip fail">missed {t.missed}</span><span className="chip">showcase ok {t.showcase_ok}</span>
        </div>}
        <table className="lines" style={{ marginTop: 8 }}>
          <thead><tr><th>WIP</th><th>Issue</th><th>Code</th><th>Verdict</th><th>Designed for</th></tr></thead>
          <tbody>{(data.accuracy?.rows ?? []).map((r: any, i: number) => (
            <tr key={i}><td>{r.wip_no}</td><td>{r.issue}</td><td>{r.code ?? '—'}</td><td>{r.verdict.replaceAll('_', ' ')}</td><td className="muted">{r.expected}</td></tr>))}</tbody>
        </table>
        <h4>Batch cost (estimated as if real models)</h4>
        <table className="lines">
          <thead><tr><th>Agent</th><th>Model</th><th>Calls</th><th>Tokens in/out</th><th>USD</th></tr></thead>
          <tbody>{(data.cost?.rows ?? []).map((r: any) => (
            <tr key={r.agent + r.provider}><td>{r.agent}</td><td className="mono">{r.model} ({r.provider})</td><td>{r.calls}</td>
              <td>{r.input_tokens?.toLocaleString()} / {r.output_tokens?.toLocaleString()}</td><td>${Number(r.cost_usd).toFixed(3)}</td></tr>))}</tbody>
        </table>
        <p className="muted">Total ≈ ${Number(data.cost?.total_usd ?? 0).toFixed(3)} · caps: {data.cost?.caps?.per_claim?.toLocaleString()} tokens/claim,{' '}
          {data.cost?.caps?.per_batch?.toLocaleString()} tokens/batch. {data.cost?.note}</p>
      </div>
    </div>
  )
}
