import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { Link } from '../../router/Router'

/**
 * The way into the editor from a Spotify class's page (#1453). Only a Spotify-playlist class with
 * a current version and no open job gets it: the editor refuses every other case, so there is no
 * dead link. A studio class has its own link in the studio panel.
 */
export function SpotifyEditorLink({ summary }: { summary: ClassSummary }) {
  const t = useT()
  if (!summary.spotify || summary.currentRevisionId === null || summary.openJob !== null) return null
  return (
    <p className="m-0">
      <Link className="button" to={`/classes/${summary.id}/edit`}>
        {t('editor.open')}
      </Link>
    </p>
  )
}
