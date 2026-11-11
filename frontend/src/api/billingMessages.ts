import { ApiError } from './client'
import { errorMessage } from './messages'
import type { CatalogueKey } from '../i18n/en'
import { t } from '../i18n/t'

// Plain sentences for the billing refusals (backend/src/classbeat/billing/routes.py). Nothing from the
// server's body is shown: only the `reason` code picks one of our own sentences.

const REASONS: Record<string, CatalogueKey> = {
  quote_changed: 'plan.billing.quoteChanged',
  billing_closed: 'plan.billing.closed',
  price_missing: 'plan.billing.priceMissing',
}

/** The 409 that says the amount moved since the page showed it. */
export function isQuoteChanged(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.reason === 'quote_changed'
}

export function billingError(error: unknown): string {
  if (error instanceof ApiError) {
    const key = error.reason !== null && Object.hasOwn(REASONS, error.reason) ? REASONS[error.reason] : undefined
    if (key !== undefined) return t(key)
    if (error.status === 409 || error.status === 422) return t('plan.billing.refused')
  }
  return errorMessage(error)
}
