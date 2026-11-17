import { useState } from 'react'
import type { ClassSummary } from '../../api/classes'
import { useT } from '../../i18n/I18n'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '../ui/dialog'
import { DeleteClass } from './DeleteClass'

/**
 * Delete a class from the list of classes. A class whose design failed (a provider or key error)
 * has no version, so no Duplicate and nothing else to open it for: this is the way to remove it
 * without opening it. It is the same confirmation and the same `DELETE /api/classes/{id}` as the
 * class page's menu; the server removes only the signed-in user's own class.
 */
export function DeleteClassFromList({ summary, onDeleted }: { summary: ClassSummary; onDeleted: () => void }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="secondary" aria-label={t('class.list.delete', { title: summary.title })}>
          {t('class.page.deleteClass')}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="sr-only">{t('class.delete.title')}</DialogTitle>
        <DialogDescription className="sr-only">{summary.title}</DialogDescription>
        <DeleteClass
          summary={summary}
          open
          onClose={() => {
            setOpen(false)
          }}
          onDeleted={() => {
            setOpen(false)
            onDeleted()
          }}
          onReload={onDeleted}
        />
      </DialogContent>
    </Dialog>
  )
}
