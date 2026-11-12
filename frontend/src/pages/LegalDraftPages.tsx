import { LegalDraftDoc } from './legalDraft/LegalDraftDoc'

// The four draft legal pages (PB-4, #975, ADR 0032 section 6). Each is a `draft` path: prerendered,
// noindex, not in the sitemap and linked from nowhere, until the owner and a lawyer publish them.
export const TermsDraftPage = () => <LegalDraftDoc doc="terms" />
export const PrivacyDraftPage = () => <LegalDraftDoc doc="privacy" />
export const RefundsDraftPage = () => <LegalDraftDoc doc="refunds" />
export const SubProcessorsDraftPage = () => <LegalDraftDoc doc="subprocessors" />
