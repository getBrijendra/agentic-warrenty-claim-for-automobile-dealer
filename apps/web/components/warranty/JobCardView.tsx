'use client'
import { money, simTime, useBrand } from '@/lib/api'

const PAY: Record<string, string> = { C: 'Customer', W: 'Warranty', I: 'Internal', CP: 'Campaign', MP: 'Maint. prog.' }

export default function JobCardView({ jc }: { jc: any }) {
  const { meta } = useBrand()
  if (!jc || jc.error) return <div className="panel"><h3>Job card</h3><div className="body muted">{jc?.error ?? 'loading…'}</div></div>
  const v = jc.vehicle ?? {}
  return (
    <div className="panel jobcard">
      <h3>WIP {jc.wip_no} (Job Card) <span className="spacer" /><span className="badge">Fake Autoline</span></h3>
      <div className="body">
        <div className="hdr"><span>{jc.branch} · {jc.status.toUpperCase()}</span><span>closed {simTime(jc.closed_at)}</span></div>
        <div className="kv">
          <span className="k">Customer</span><span>{jc.customer?.name} <span className="muted">({jc.customer?.emirate})</span></span>
          <span className="k">Vehicle</span><span>{v.make} {v.model} {v.model_year}</span>
          <span className="k">VIN</span><span title="fictional, valid-format">{v.vin}</span>
          <span className="k">Plate</span><span>{v.plate}</span>
          <span className="k">Odometer in/out</span><span>{jc.odometer_in?.toLocaleString()} / {jc.odometer_out?.toLocaleString()} km</span>
          <span className="k">Warranty start</span><span>{v.warranty_start_date} · {v.market_spec}</span>
          {v.aftermarket_mods?.length > 0 && <><span className="k">Modifications</span><span style={{ color: 'var(--warn)' }}>{v.aftermarket_mods.join(', ')}</span></>}
          <span className="k">Advisor</span><span>{jc.advisor?.name}</span>
          <span className="k">Technician</span><span>{jc.technicians?.map((t: any) => t?.name).join(', ')}</span>
          <span className="k">DTCs</span><span>{jc.dtc_codes?.join(', ') || '—'}</span>
          <span className="k">Diag session</span><span>{jc.diagnostic_session_id ?? <span className="muted">not recorded</span>}</span>
          {jc.pre_auth_ref && <><span className="k">Pre-auth</span><span>{jc.pre_auth_ref}</span></>}
          {jc.tech_support_ref && <><span className="k">Tech support</span><span>{jc.tech_support_ref}</span></>}
        </div>
        <p className="muted" style={{ margin: '10px 0 4px' }}>CUSTOMER COMPLAINT (verbatim)</p>
        <div className="story">"{jc.customer_complaint}"</div>
        <p className="muted" style={{ margin: '10px 0 4px' }}>TECHNICIAN STORY</p>
        <div className="story">{jc.technician_story}</div>
        <p className="muted" style={{ margin: '10px 0 4px' }}>LINES</p>
        <table>
          <thead><tr><th>#</th><th>Code</th><th>Description</th><th>Hrs/Qty</th><th>Pay</th></tr></thead>
          <tbody>
            {jc.lines?.map((l: any) => (
              <tr key={l.id}>
                <td>{l.line_no}</td>
                <td>{l.op_code ?? l.part_no}{l.is_causal && <span className="chip warn" style={{ marginLeft: 4 }}>causal</span>}</td>
                <td>{l.description}{l.part_serial && <div className="muted">s/n {l.part_serial}</div>}
                  {l.line_type === 'labour' && <div className="muted">std {l.op_standard_hours} h · {l.op_system}</div>}</td>
                <td>{l.line_type === 'labour' ? `${l.labour_hours_actual} h` : `${l.qty} × ${money(l.unit_price, meta?.brand.currency)}`}</td>
                <td>{PAY[l.pay_type] ?? l.pay_type}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ margin: '10px 0 4px' }}>ATTACHMENTS ({jc.attachments?.length ?? 0})</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
          {jc.attachments?.map((a: any) => (
            <a key={a.id} href={a.url} target="_blank" rel="noreferrer" title={`${a.title} — generated placeholder`}>
              <img src={a.url} alt={a.title} style={{ width: '100%', borderRadius: 6, border: '1px solid var(--line)' }} />
              <div style={{ fontSize: 11 }}>{a.kind} · {a.uploaded_by === 'workshop-sim' ? 'uploaded by workshop' : a.id}</div>
            </a>
          ))}
        </div>
      </div>
    </div>
  )
}
