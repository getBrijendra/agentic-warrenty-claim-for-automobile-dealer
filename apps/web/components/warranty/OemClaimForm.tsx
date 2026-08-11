'use client'
// Generic OEM claim form renderer driven by each programme's adapter layout hint (no per-OEM React code).
import { Fragment, useState } from 'react'
import { money, progName, useBrand } from '@/lib/api'

const fmt = (v: any) => (v === null || v === undefined || v === '' ? <span className="muted">—</span> : typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v))

function Parts({ parts }: { parts: any[] }) {
  if (!parts?.length) return null
  return (
    <table className="lines" style={{ marginTop: 4 }}>
      <thead><tr><th>Part</th><th>Description</th><th>Qty</th><th>Price</th><th /></tr></thead>
      <tbody>{parts.map((p, i) => (
        <tr key={i}><td className="mono">{p.part_no}</td><td>{p.description}{(p.module_serial || p.serial) && <div className="muted">s/n {p.module_serial ?? p.serial}</div>}</td>
          <td>{p.qty}</td><td>{p.unit_price ?? p.price}</td><td>{p.causal && <span className="chip warn">causal</span>}</td></tr>))}</tbody>
    </table>
  )
}

export default function OemClaimForm({ programme, layout, version }: { programme: string; layout: any; version: any }) {
  const { meta } = useBrand()
  const [view, setView] = useState<'form' | 'canonical' | 'payload'>('form')
  const p = version?.oem_payload
  const c = version?.canonical
  return (
    <div className="panel">
      <h3>
        {progName(meta, programme)} claim {version ? `· v${version.version}` : ''}
        <span className="spacer" />
        {(['form', 'canonical', 'payload'] as const).map((v) => (
          <button key={v} className={`small ${view === v ? 'primary' : ''}`} onClick={() => setView(v)}>
            {v === 'form' ? 'OEM form' : v === 'canonical' ? 'Canonical JSON' : 'OEM payload JSON'}
          </button>
        ))}
      </h3>
      <div className="body">
        {!version && <p className="muted">No claim built yet.</p>}
        {version && view === 'canonical' && <pre>{JSON.stringify(c, null, 2)}</pre>}
        {version && view === 'payload' && (p ? <pre>{JSON.stringify(p, null, 2)}</pre> : <p className="muted">Payload is built at submission time.</p>)}
        {version && view === 'form' && !p && (
          <>
            <p className="muted">OEM payload not built yet — showing the canonical claim.</p>
            <Narrative n={c} />
            <table className="lines">
              <thead><tr><th>Op</th><th>Description</th><th>Claimed</th><th>Std</th><th>Rate</th></tr></thead>
              <tbody>{c.lines.map((l: any) => <tr key={l.line_no}><td className="mono">{l.labour_op}</td><td>{l.description}<Parts parts={l.parts.map((x: any) => ({ ...x, causal: x.part_no === c.causal_part.part_no }))} /></td>
                <td>{l.hours_claimed}</td><td>{l.standard_hours}</td><td>{l.labour_rate}</td></tr>)}</tbody>
            </table>
          </>
        )}
        {version && view === 'form' && p && layout && (
          <>
            <div className="muted" style={{ marginBottom: 8 }}>{layout.title} · <span className="badge sim">Simulated OEM Gateway format (illustrative)</span></div>
            <div className="kv" style={{ marginBottom: 10 }}>
              {layout.header.map(([k, label]: [string, string]) => <Fragment key={k}><span className="k">{label}</span><span className="mono">{fmt(p[k])}</span></Fragment>)}
            </div>
            {layout.lines_key && (
              <table className="lines">
                <thead><tr>{layout.line_cols.map(([k, l]: [string, string]) => <th key={k}>{l}</th>)}</tr></thead>
                <tbody>{p[layout.lines_key].map((ln: any, i: number) => (
                  <tr key={i}>{layout.line_cols.map(([k]: [string, string], j: number) => (
                    <td key={k} className={j === 0 ? 'mono' : ''}>{fmt(ln[k])}{j === 1 && ln.parts && <Parts parts={ln.parts} />}</td>))}</tr>))}</tbody>
              </table>
            )}
            {layout.sections_key && p[layout.sections_key].map((s: any) => (
              <div key={s.section} className="section">
                <div className="row"><b>Repair section {s.section}</b><span className="chip">symptom {s.symptom_code}</span>
                  {s.fault_codes?.map((f: string) => <span key={f} className="chip">{f}</span>)}{s.related_damage && <span className="chip warn">related damage</span>}</div>
                <table className="lines"><thead><tr><th>SRO</th><th>Description</th><th>Time</th><th>Rate</th></tr></thead>
                  <tbody>{s.sro_lines.map((r: any) => <tr key={r.sro}><td className="mono">{r.sro}</td><td>{r.description}</td><td>{r.time_hrs}</td><td>{r.rate}</td></tr>)}</tbody></table>
                <Parts parts={s.parts} />
                {s.technician_note && <p><span className="muted">Technician note:</span> {s.technician_note}</p>}
              </div>
            ))}
            {layout.section_key && (
              <div className="section">
                <div className="row"><b>Single section</b><span className="chip">defect {p.section.defect_code}</span></div>
                <table className="lines"><thead><tr><th>Op</th><th>Description</th><th>Time</th><th>Rate</th></tr></thead>
                  <tbody>{p.section.operations.map((o: any) => <tr key={o.op_code}><td className="mono">{o.op_code}</td><td>{o.description}</td><td>{o.time}</td><td>{o.rate}</td></tr>)}</tbody></table>
                <Parts parts={p.section.parts} />
              </div>
            )}
            {layout.list_fields?.map(([k, l]: [string, string]) => <p key={k}><span className="muted">{l}:</span> <span className="mono">{(p[k] || []).join(', ') || '—'}</span></p>)}
            <Narrative n={p.narrative} />
            <div className="kv">
              {layout.extra?.map(([k, l]: [string, string]) => <Fragment key={k}><span className="k">{l}</span><span>{fmt(p[k])}</span></Fragment>)}
              <span className="k">Attachments</span><span>{p.attachments.map((a: any) => `${a.kind} (${a.id})`).join(', ') || '—'}</span>
              <span className="k">Totals</span><span>labour {money(p.totals.labour, p.totals.currency, 2)} · parts {money(p.totals.parts, p.totals.currency, 2)} · VAT {money(p.totals.vat, p.totals.currency, 2)} · <b>{money(p.totals.total, p.totals.currency, 2)}</b></span>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function Narrative({ n }: { n: any }) {
  return (
    <div className="section">
      <div><span className="muted">Complaint:</span> {n.complaint}</div>
      <div><span className="muted">Cause:</span> {n.cause}</div>
      <div><span className="muted">Correction:</span> {n.correction}</div>
      {n.labour_justification && <div><span className="muted">Labour justification:</span> {n.labour_justification}</div>}
    </div>
  )
}
