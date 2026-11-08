import { LegalDoc } from './legal/LegalDoc'
import { PRIVACY } from './legal/structure'

/** The privacy notice (issue #718): public, in the visitor's language. */
export function PrivacyPage() {
  return <LegalDoc doc="privacy" sections={PRIVACY} />
}
