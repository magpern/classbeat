import type { SpotifyStatus } from '../../../api/spotify'
import { useT } from '../../../i18n/I18n'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../ui/dialog'
import { SpotifyConnection } from '../Spotify'

/**
 * The Spotify dialog on Account, Connections (ADR 0028 section 3; ADR 0009 sections 2, 7, 8, 9).
 * Not connected: the Client ID step and Connect, which leaves for Spotify (PKCE, no Client
 * Secret). Connected or needing a reconnect: the status, the working-playlist consent, Change
 * Client ID and Disconnect. Its body mounts on each open, so it always shows a fresh status.
 */
export function SpotifyDialog({
  open,
  onClose,
  onStatus,
}: {
  open: boolean
  onClose: () => void
  onStatus: (status: SpotifyStatus | null) => void
}) {
  const t = useT()
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent className="w-[min(36rem,calc(100%-2rem))]">
        <DialogTitle>{t('spot.title')}</DialogTitle>
        <DialogDescription>{t('conn.spotify.purpose')}</DialogDescription>
        <SpotifyConnection onStatus={onStatus} />
      </DialogContent>
    </Dialog>
  )
}
