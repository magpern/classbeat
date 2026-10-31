import type { ReactNode } from 'react'

// A plain data table in the admin pages' style (the same classes as Users and Codes). Every
// cell is rendered as React text; server values are never HTML.

const CELL = 'border-b border-line py-2 pr-4'

export function DataTable({
  caption,
  head,
  rows,
  empty,
}: {
  caption: string
  head: string[]
  rows: { key: string; cells: ReactNode[] }[]
  empty: string
}) {
  if (rows.length === 0) return <p className="text-muted">{empty}</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-[0.9375rem]">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-muted">
          <tr>
            {head.map((label) => (
              <th key={label} className={`${CELL} font-medium`}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              {row.cells.map((cell, index) => (
                <td key={index} className={CELL}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function SectionTitle({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2 id={id} className="m-0 mb-4 font-display text-xl font-bold">
      {children}
    </h2>
  )
}

export function SubTitle({ children }: { children: ReactNode }) {
  return <h3 className="m-0 mt-6 mb-2 font-display text-lg font-bold">{children}</h3>
}

/** A labelled figure (a total). */
export function Figure({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <dt className="m-0 text-[0.875rem] text-muted">{label}</dt>
      <dd className="m-0 font-display text-xl font-bold">{value}</dd>
    </div>
  )
}

export function when(value: string | null): string {
  if (value === null) return ''
  const at = new Date(value)
  return Number.isNaN(at.getTime())
    ? value
    : at.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
