import { MiniProfile } from '../classes/MiniProfile'
import { Button } from '../ui/button'
import { Card, CardTitle } from '../ui/card'

/** "Your class so far": a sketch on the stage, a summary and the page's one primary button. */
export function SideCard({
  profile,
  summary,
  busy,
}: {
  profile: [number, number][]
  summary: { label: string; value: string }[]
  busy: boolean
}) {
  return (
    <Card className="flex flex-col gap-4" role="region" aria-labelledby="so-far-title">
      <CardTitle id="so-far-title" className="mb-0">
        Your class so far
      </CardTitle>
      <MiniProfile profile={profile} />
      <p className="m-0 text-[0.8125rem] text-muted">A rough sketch of your numbers. Claude designs the real class.</p>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[0.9375rem]">
        {summary.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-muted">{row.label}</dt>
            <dd className="m-0 text-right text-text">{row.value}</dd>
          </div>
        ))}
      </dl>
      <Button type="submit" disabled={busy} className="w-full">
        Design my class
      </Button>
    </Card>
  )
}
