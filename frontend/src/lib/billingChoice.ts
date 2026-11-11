import type { Billing, Offer, OfferAction, Subscription } from '../api/billing'
import type { CatalogueKey } from '../i18n/en'

// What a plan choice means on the Plan screen (M7a-5). Pure: no network, no storage, no clock. The
// server's quote is the authority; this only picks the words and the buttons for what it returned.

/** A buy, upgrade, downgrade or resume is an offer's action; a cancel is a change to Free. */
export type ChoiceKind = OfferAction | 'cancel'

export interface Choice {
  kind: ChoiceKind
  /** The plan chosen; `free` for a cancel. */
  plan: string
}

/** Whether a change to a paid plan or a cancel is waiting for the period end. */
export function scheduled(sub: Subscription | null): boolean {
  return sub !== null && (sub.cancelAtEnd || sub.nextPlan !== null)
}

/** The button an offer gets, or null: nothing to do, or the current plan with no change scheduled. */
export function offerKind(offer: Offer, sub: Subscription | null): OfferAction | null {
  if (offer.action === null) return null
  if (offer.action === 'resume' && !scheduled(sub)) return null
  return offer.action
}

/** Whether the caller may cancel: an active subscription with no cancel scheduled yet. */
export function canCancel(sub: Subscription | null): boolean {
  return sub !== null && sub.status === 'active' && !sub.cancelAtEnd
}

/** Whether a choice is still what the server offers (after a refresh the quote or action may differ). */
export function stillOffered(billing: Billing, choice: Choice): boolean {
  if (choice.kind === 'cancel') return canCancel(billing.subscription)
  const offer = billing.offers.find((o) => o.plan === choice.plan)
  return offer !== undefined && offerKind(offer, billing.subscription) === choice.kind
}

export const ACTION_LABEL: Record<ChoiceKind, CatalogueKey> = {
  buy: 'plan.action.buy',
  upgrade: 'plan.action.upgrade',
  downgrade: 'plan.action.downgrade',
  resume: 'plan.action.resume',
  cancel: 'plan.action.cancel',
}

/** The sentence that says what confirming does; a buy or an upgrade that costs 0 has its own. */
export function confirmKey(kind: ChoiceKind, chargeMinor: number): CatalogueKey {
  if (kind === 'upgrade') return chargeMinor === 0 ? 'plan.confirm.upgradeFree' : 'plan.confirm.upgrade'
  if (kind === 'buy') return 'plan.confirm.buy'
  if (kind === 'downgrade') return 'plan.confirm.downgrade'
  if (kind === 'cancel') return 'plan.confirm.cancel'
  return 'plan.confirm.resume'
}

/** The note after a change went through: the plan changed now, or the change waits for the period end. */
export function doneKey(kind: ChoiceKind): CatalogueKey {
  if (kind === 'buy' || kind === 'upgrade') return 'plan.done.now'
  return kind === 'resume' ? 'plan.done.resume' : 'plan.done.scheduled'
}
