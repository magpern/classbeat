import type { CatalogueKey } from '../i18n/en'

// The Plans page (M7a-5, #950): the three plans as words only. A plan's numbers (classes, minutes,
// games, FTP tests) come from `GET /api/plans` after takeover and are never part of the prerender;
// no price appears anywhere on this page, because prices are read only for a tester or an admin
// (ADR 0031 section 7). Plan ids and quality words follow ADR 0031 section 1.

export interface PricingPlan {
  id: 'free' | 'instructor' | 'pro'
  name: CatalogueKey
  blurb: CatalogueKey
  quality: 'standard' | 'better' | 'best'
}

export const PRICING_PLANS: readonly PricingPlan[] = [
  { id: 'free', name: 'plan.name.free', blurb: 'pricing.free.blurb', quality: 'standard' },
  { id: 'instructor', name: 'plan.name.instructor', blurb: 'pricing.instructor.blurb', quality: 'better' },
  { id: 'pro', name: 'plan.name.pro', blurb: 'pricing.pro.blurb', quality: 'best' },
]

/** What is the same on every plan (ADR 0031 section 1). */
export const SAME_ON_EVERY_PLAN: readonly CatalogueKey[] = [
  'pricing.same.races',
  'pricing.same.classes',
  'pricing.same.connections',
  'pricing.same.songs',
]
