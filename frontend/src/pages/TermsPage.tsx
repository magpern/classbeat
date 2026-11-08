import { LegalDoc } from './legal/LegalDoc'
import { TERMS } from './legal/structure'

/** The terms of use (issue #718): public, in the visitor's language. */
export function TermsPage() {
  return <LegalDoc doc="terms" sections={TERMS} />
}
