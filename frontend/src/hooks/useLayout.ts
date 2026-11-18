import { useEffect, useState } from 'react'

/** ADR 0013 section 4 and Afterburn A2: a top bar from 640 px ('wide'), a tab bar below ('phone'). */
export type Layout = 'phone' | 'wide'

const WIDE = '(min-width: 640px)'

function read(): Layout {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'phone'
  return window.matchMedia(WIDE).matches ? 'wide' : 'phone'
}

export function useLayout(): Layout {
  const [layout, setLayout] = useState<Layout>(read)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const list = window.matchMedia(WIDE)
    const update = () => {
      setLayout(read())
    }
    update()
    list.addEventListener('change', update)
    return () => {
      list.removeEventListener('change', update)
    }
  }, [])
  return layout
}
