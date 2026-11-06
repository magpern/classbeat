import type { Catalogue } from '../en'

// A test-only catalogue for the locale "xx": one translated key, one with plural forms, so loading,
// fallback and plurals can be tested while English is the only shipped locale. Never imported by the app.
const xx: Catalogue = {
  'usage.underOneCent': { one: 'ett {amount}', other: 'under {amount} (xx)' },
}

export default xx
