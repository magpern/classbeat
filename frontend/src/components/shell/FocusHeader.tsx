import { Link } from '../../router/Router'
import { BrandLink } from './BrandLink'

/**
 * The header of a focused flow (a job page, the advanced form): the logo and the way out sit in the
 * same `max-w-5xl` container as the content, with the top bar's height and border (ADR 0013 Afterburn A2).
 */
export function FocusHeader({ label }: { label: string }) {
  return (
    <header className="border-b border-line bg-bg">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-x-4 px-4 py-3 sm:px-6">
        <BrandLink />
        <Link
          to="/"
          className="inline-flex min-h-11 items-center rounded-full bg-raised px-5 font-medium text-text no-underline hover:bg-line"
        >
          {label}
        </Link>
      </div>
    </header>
  )
}
