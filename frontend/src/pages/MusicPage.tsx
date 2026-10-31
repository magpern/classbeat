import { RequireAuth } from '../components/RequireAuth'
import { Card, CardContent, CardTitle } from '../components/ui/card'

/** Placeholder so the Music item of the navigation has a page; the real one is UI8. */
export function MusicPage() {
  return (
    <RequireAuth>
      <div className="flex flex-col gap-6">
        <h1 className="m-0 font-display text-3xl font-extrabold">Music</h1>
        <Card role="region" aria-labelledby="music-title">
          <CardTitle id="music-title">Your playlists are coming here</CardTitle>
          <CardContent>The playlists from MOWL and the ones classbeat made will show up on this page.</CardContent>
        </Card>
      </div>
    </RequireAuth>
  )
}
