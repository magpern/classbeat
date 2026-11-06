import { useEffect } from 'react'
import { useLocale } from '../i18n/I18n'
import { isLocale } from '../i18n/locales'
import { useSession } from './SessionContext'

/** A signed-in account's saved language wins over this browser's (ADR 0023 section 2). */
export function AccountLocale() {
  const { session } = useSession()
  const { setLocale } = useLocale()
  const saved = session?.user?.locale ?? null
  useEffect(() => {
    if (saved !== null && isLocale(saved)) setLocale(saved)
  }, [saved, setLocale])
  return null
}
