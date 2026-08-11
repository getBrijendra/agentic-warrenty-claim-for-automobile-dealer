'use client'
import Link from 'next/link'
import { Fragment, useEffect, useState } from 'react'
import { SourceBadge } from '@/components/warranty/RuleChecklist'
import { get } from '@/lib/api'

export default function RulesMatrix() {
  const [m, setM] = useState<any>(null)
  useEffect(() => { get('/oem/rules-matrix').then(setM) }, [])
  if (!m) return <div className="page muted">Loading…</div>
  const progs = m.programmes
  let lastGroup = ''
  return (
    <div className="page">
      <h2 style={{ marginTop: 0 }}>Six rulebooks, one desk</h2>
      <div className="banner">
        Rules are data (<code>rule_packs/*.yaml</code>), versioned per programme. <b>Public</b> facts link to their source and must be
        re-verified before any meeting (last-verified dates are not yet set). <b>Illustrative</b> values are realistic placeholders —
        dealer-side terms are confidential and are <b>not</b> the OEM's actual policy. Codes use invented prefixes.
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="matrix">
          <thead>
            <tr><th style={{ width: 210 }}>Rule</th>
              {progs.map((p: any) => (
                <th key={p.programme}><span className="badge prog" style={{ background: p.colour }}>{p.display_name}</span>
                  <div className="muted" style={{ fontWeight: 400, fontSize: 11 }}>{p.status === 'pending' ? 'rules pending' : `pack ${p.pack_version}`}</div></th>))}
            </tr>
          </thead>
          <tbody>
            {m.rows.map((r: any) => {
              const head = r.group !== lastGroup
              lastGroup = r.group
              return (
                <Fragment key={r.key}>
                  {head && <tr><td className="group" colSpan={progs.length + 1}>{r.group}</td></tr>}
                  <tr>
                    <td><span className="tip" title={r.why}>{r.label}</span></td>
                    {progs.map((p: any) => {
                      const cell = r.cells[p.programme]
                      const ex = r.exercised?.[p.programme] ?? []
                      return (
                        <td key={p.programme}>
                          {cell ? <>
                            <div>{cell.value}</div>
                            {cell.label && cell.label !== cell.value && <div className="muted" style={{ fontSize: 11 }}>{cell.label}</div>}
                            {cell.note && <div className="muted" style={{ fontSize: 11 }}>{cell.note}</div>}
                            <div style={{ marginTop: 4 }}><SourceBadge source={cell.source} url={cell.source_url} /></div>
                            {ex.length > 0 && <div style={{ marginTop: 4, fontSize: 11 }} title={`Why it matters: ${r.why}`}>
                              exercised by {ex.slice(0, 3).map((ref: string) => <Link key={ref} href={`/demo/warranty-claims/claims/${ref}`}>{ref.slice(-3)} </Link>)}</div>}
                          </> : <span className="muted">—</span>}
                        </td>)
                    })}
                  </tr>
                </Fragment>)
            })}
          </tbody>
        </table>
      </div>
      <h3>Other public facts</h3>
      <ul>{m.facts.map((f: any, i: number) => <li key={i}><b>{f.programme}</b> · {f.label}: {f.value} <SourceBadge source={f.source} url={f.source_url} /></li>)}</ul>
    </div>
  )
}
