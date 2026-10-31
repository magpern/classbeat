import { useId, type ReactNode } from 'react'

/**
 * The card of the focus pages (Sign in, Sign up and the mail-link pages): centred, narrow, with
 * the Archivo title. Same props as the card of `forms.tsx` (without `wide`), so the pages only change the import.
 */
export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  const id = useId()
  return (
    <section
      aria-labelledby={id}
      className="mx-auto w-full max-w-md rounded-lg border border-line bg-surface p-6 text-text sm:p-8"
    >
      <h1 id={id} className="m-0 mb-5 font-display text-2xl font-extrabold leading-tight">
        {title}
      </h1>
      {children}
    </section>
  )
}
