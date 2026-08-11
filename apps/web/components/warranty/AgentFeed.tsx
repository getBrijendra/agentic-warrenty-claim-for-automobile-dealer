'use client'
import Link from 'next/link'
import { progColour, simTime, useBrand } from '@/lib/api'

export type FeedItem = { key: string; at: string | null; agent: string; wip: string; claim_ref: string; programme: string
  text: string; kind: 'note' | 'workshop' | 'decision' | 'status'; tone?: 'good' | 'bad'; question?: string }

const AGENT = { triage: 'Triage', builder: 'Builder', compliance: 'Compliance', submitter: 'Submitter', resolver: 'Resolver',
  orchestrator: 'Orchestrator', 'oem-gateway': 'OEM Gateway' } as Record<string, string>

export default function AgentFeed({ items, connected }: { items: FeedItem[]; connected: boolean }) {
  const { meta } = useBrand()
  return (
    <div className="panel">
      <h3>Live agent feed <span className="spacer" /><span className={`chip ${connected ? 'ok' : 'fail'}`}>{connected ? 'stream on' : 'reconnecting'}</span></h3>
      <div className="feed">
        {items.length === 0 && <div className="item muted">Press <b>Run agents</b> — status notes from the agents appear here. Raw model reasoning is never streamed.</div>}
        {items.map((it) => (
          <div key={it.key} className={`item ${it.kind === 'workshop' ? 'workshop' : ''} ${it.tone ?? ''}`}>
            <div className="meta">
              <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 4, marginRight: 5, background: progColour(meta, it.programme) }} />
              {simTime(it.at)} · <b style={{ color: 'var(--text)' }}>{AGENT[it.agent] ?? it.agent}</b> ·{' '}
              <Link href={`/demo/warranty-claims/claims/${it.claim_ref}`}>WIP {it.wip}</Link>
              {it.kind === 'workshop' && ' · workshop chat'}
            </div>
            {it.kind === 'workshop' && it.question && <div className="muted" style={{ fontSize: 11.5 }}>Q: {it.question}</div>}
            <div>{it.text}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
