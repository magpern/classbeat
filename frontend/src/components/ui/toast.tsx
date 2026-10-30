import { Toaster as Sonner, toast } from 'sonner'
import { readTheme } from '../../theme/theme'

export { toast }

/** Mount once near the root. Colours come from the tokens. */
export function Toaster() {
  return (
    <Sonner
      theme={readTheme()}
      toastOptions={{
        style: {
          background: 'var(--cb-surface)',
          color: 'var(--cb-text)',
          border: '1px solid var(--cb-line)',
          borderRadius: 'var(--cb-radius-md)',
        },
      }}
    />
  )
}
