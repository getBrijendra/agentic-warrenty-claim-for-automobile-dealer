'use client'
import { LayoutGroup } from 'motion/react'
import ClaimCard, { type Card } from './ClaimCard'

export default function PipelineBoard({ columns, cards, simNow, onRun }: {
  columns: { key: string; label: string }[]; cards: Card[]; simNow: string | null; onRun: (ref: string) => void
}) {
  return (
    <LayoutGroup>
      <div className="board">
        {columns.map((col) => {
          const list = cards.filter((c) => c.column === col.key).sort((a, b) => a.priority - b.priority || a.wip_no.localeCompare(b.wip_no))
          return (
            <div key={col.key} className="col">
              <div className="head"><span>{col.label}</span><span>{list.length}</span></div>
              <div className="cards">
                {list.map((c) => <ClaimCard key={c.claim_ref} c={c} simNow={simNow} onRun={onRun} />)}
              </div>
            </div>
          )
        })}
      </div>
    </LayoutGroup>
  )
}
