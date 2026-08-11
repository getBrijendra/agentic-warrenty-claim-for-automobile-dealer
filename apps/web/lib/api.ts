'use client'
// Thin client for the Python `api` service (+ SSE hook, brand context, formatters). No business logic here.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

export const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000/api/v1'

export async function get<T = any>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

export async function post<T = any>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: body ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data?.detail ?? `${r.status}`)
  return data
}

export type Programme = { programme: string; display_name: string; colour: string; status: string; prefix: string }
export type Meta = {
  brand: { displayName: string; currency: string; locale: string; timeZone: string; vatRate: number }
  mode: 'live' | 'replay'; fake_llm: boolean; llm_provider: string; dms_adapter: string; simulated_oem: boolean
  models: { main: string; fast: string }; programmes: Programme[]; max_parallel: number
}

export const BrandContext = createContext<{ meta: Meta | null; refresh: () => void }>({ meta: null, refresh: () => {} })
export const useBrand = () => useContext(BrandContext)

export function useMeta() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(() => {
    get<Meta>('/meta').then((m) => { setMeta(m); setError(null) }).catch((e) => setError(String(e?.message ?? e)))
  }, [])
  useEffect(() => { refresh() }, [refresh])
  return { meta, error, refresh }
}

export function money(v: number | null | undefined, currency = 'AED', digits = 0) {
  if (v === null || v === undefined) return '—'
  return new Intl.NumberFormat('en-AE', { style: 'currency', currency, maximumFractionDigits: digits }).format(v)
}

export function simTime(iso?: string | null, withDate = true) {
  if (!iso) return '—'
  const d = new Date(iso)
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Dubai', ...(withDate ? { day: '2-digit', month: 'short' } : {}), hour: '2-digit', minute: '2-digit',
  }).format(d)
}

export function pct(v?: number | null) {
  return v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`
}

export function progColour(meta: Meta | null, p: string) {
  return meta?.programmes.find((x) => x.programme === p)?.colour ?? '#666'
}

export function progName(meta: Meta | null, p: string) {
  return meta?.programmes.find((x) => x.programme === p)?.display_name ?? p
}

export type StreamEvent = { id?: string; type: string; data: any }

/** Server-Sent Events from the api (fed by the transactional outbox). Reconnects with Last-Event-ID. */
export function useEventStream(onEvent: (e: StreamEvent) => void) {
  const cb = useRef(onEvent)
  cb.current = onEvent
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    const es = new EventSource(`${API}/stream`)
    const topics = ['claim.status', 'agent.step', 'kpi.update', 'simclock.tick', 'reset', 'escalation.created',
      'escalation.resolved', 'oem.adjudicated', 'credit_note.posted', 'mode.changed', 'run.started', 'run.finished']
    const handlers = topics.map((t) => {
      const h = (ev: MessageEvent) => { try { cb.current({ id: ev.lastEventId, type: t, data: JSON.parse(ev.data) }) } catch {} }
      es.addEventListener(t, h as EventListener)
      return [t, h] as const
    })
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    return () => { handlers.forEach(([t, h]) => es.removeEventListener(t, h as EventListener)); es.close() }
  }, [])
  return connected
}

export const STATUS_LABEL: Record<string, string> = {
  wip_closed: 'WIP closed', triage: 'Triage', building: 'Building', checking: 'Checking', blocked: 'Blocked',
  ready: 'Ready', submitted: 'Submitted', info_requested: 'Info requested', accepted: 'Accepted', rejected: 'Rejected',
  resolving: 'Resolving', resubmitted: 'Resubmitted', paid: 'Paid', escalated: 'Escalated', written_off: 'Written off',
  withdrawn: 'Withdrawn',
}
