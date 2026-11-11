import { useT } from '../../i18n/I18n'
import type { Claim } from '../../publicSite/claims'
import { AvailabilityNote } from './AvailabilityNote'

/** A list of claims, each with its text and its availability note. */
export function ClaimList({ claims, headingLevel = 3 }: { claims: readonly Claim[]; headingLevel?: 3 | 4 }) {
  const t = useT()
  const Heading = `h${String(headingLevel)}` as 'h3' | 'h4'
  return (
    <ul className="m-0 flex list-none flex-col gap-4 p-0">
      {claims.map((claim) => (
        <li key={claim.id} className="flex flex-col gap-1 rounded-lg border border-line bg-surface p-5">
          <Heading className="m-0 font-display text-lg font-bold">{t(claim.title)}</Heading>
          <p className="m-0">{t(claim.text)}</p>
          <AvailabilityNote claim={claim} />
        </li>
      ))}
    </ul>
  )
}
