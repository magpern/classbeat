import { useT } from '../../../i18n/I18n'
import { Button } from '../../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../ui/dialog'

/**
 * "Disconnect X?" with what happens, short, and the two answers. The detail of what is deleted
 * stays one sentence here; the long forms live on the help page.
 */
export function ConfirmDisconnect({
  open,
  title,
  text,
  yes,
  busy,
  onYes,
  onClose,
}: {
  open: boolean
  title: string
  text: string
  yes: string
  busy: boolean
  onYes: () => void
  onClose: () => void
}) {
  const t = useT()
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{text}</DialogDescription>
        <div className="flex flex-wrap gap-3">
          <Button variant="danger" disabled={busy} onClick={onYes}>
            {yes}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            {t('conn.keep')}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
