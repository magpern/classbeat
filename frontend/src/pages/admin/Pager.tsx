import { Button } from '../../components/ui/button'

export function Pager({ offset, nextOffset, setOffset }: { offset: number; nextOffset: number | null; setOffset: (n: number) => void }) {
  return (
    <div className="mt-2 flex gap-2">
      {offset > 0 ? (
        <Button
          variant="secondary"
          onClick={() => {
            setOffset(0)
          }}
        >
          Newest
        </Button>
      ) : null}
      {nextOffset !== null ? (
        <Button
          variant="secondary"
          onClick={() => {
            setOffset(nextOffset)
          }}
        >
          Older
        </Button>
      ) : null}
    </div>
  )
}
