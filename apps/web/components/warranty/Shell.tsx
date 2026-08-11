'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import type { ReactNode } from 'react'
import { BrandContext, useEventStream, useMeta } from '@/lib/api'

const NAV = [
  ['/demo/warranty-claims', 'Control room'],
  ['/demo/warranty-claims/oem-rules', 'OEM rules'],
  ['/demo/warranty-claims/review', 'Review queue'],
  ['/demo/warranty-claims/gateway', 'Simulated OEM Gateway'],
]

export default function Shell({ children }: { children: ReactNode }) {
  const { meta, error, refresh } = useMeta()
  const path = usePathname()
  useEventStream((e) => { if (e.type === 'mode.changed' || e.type === 'reset') refresh() })
  return (
    <BrandContext.Provider value={{ meta, refresh }}>
      <header className="header">
        <div className="brand">{meta?.brand.displayName ?? '…'} <span>· Warranty Desk</span></div>
        <nav>
          {NAV.map(([href, label]) => (
            <Link key={href} href={href} className={(href === '/demo/warranty-claims' ? path === href : path?.startsWith(href)) ? 'active' : ''}>
              {label}
            </Link>
          ))}
        </nav>
        <div className="pills" title="Honesty labels: what is real and what is simulated">
          {meta && <span className={`pill ${meta.mode}`}>{meta.mode === 'live' ? '● LIVE' : '↺ REPLAY'}</span>}
          {meta?.fake_llm && <span className="pill fake" title="Deterministic scripted-policy chat model — no real AI reasoning">FAKE LLM</span>}
          <span className="pill sim" title="No live connection to any OEM system">Simulated OEM Gateway</span>
          <span className="pill sim" title="Neon/PGlite tables shaped like Autoline">Fake Autoline</span>
        </div>
      </header>
      {error && <div className="page"><div className="banner">Cannot reach the api service ({error}) — start the stack with <code>pnpm dev</code>.</div></div>}
      {children}
    </BrandContext.Provider>
  )
}
