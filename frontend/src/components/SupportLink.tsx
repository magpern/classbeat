import { useEffect, useState } from 'react'
import { getSupportAddress } from '../api/plan'
import { useT } from '../i18n/I18n'

/** The configured support address (never hard-coded): undefined while loading, null when there is none. */
export function useSupportAddress(): string | null | undefined {
  const [address, setAddress] = useState<string | null | undefined>(undefined)
  useEffect(() => {
    const abort = new AbortController()
    getSupportAddress(abort.signal).then(setAddress, () => {
      // No address is shown as a plain message, never as a broken link.
      if (!abort.signal.aborted) setAddress(null)
    })
    return () => {
      abort.abort()
    }
  }, [])
  return address
}

/** A mailto link to the address, or a plain message when there is none. */
export function SupportLink({ address }: { address: string | null | undefined }) {
  const t = useT()
  if (address === undefined) return null
  if (address === null) return <>{t('help.contact.none')}</>
  return <a href={`mailto:${address}`}>{address}</a>
}
