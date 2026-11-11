import { useT } from '../../i18n/I18n'
import { CLASS_LABEL, type Claim } from '../../publicSite/claims'

/** The class of a claim and what it needs, as visible text (PP4, #893). Text, not colour alone. */
export function AvailabilityNote({ claim }: { claim: Pick<Claim, 'availability' | 'note'> }) {
  const t = useT()
  return (
    <p className="m-0 text-[0.9375rem] text-muted" data-availability={claim.availability}>
      <strong className="font-semibold text-text">{t(CLASS_LABEL[claim.availability])}.</strong> {t(claim.note)}
    </p>
  )
}
