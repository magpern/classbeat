import { useEffect, useState } from 'react'

/** ADR 0013 section 4: a sidebar from 1024 px, an icon rail from 640 px, a tab bar below. */
export type Layout = 'phone' | 'rail' | 'sidebar'

const SIDEBAR = '(min-width: 1024px)'
const RAIL = '(min-width: 640px)'

function read(): Layout {
  if (typeof window.matchMedia !== 'function') return 'phone'
  if (window.matchMedia(SIDEBAR).matches) return 'sidebar'
  return window.matchMedia(RAIL).matches ? 'rail' : 'phone'
}

export function useLayout(): Layout {
  const [layout, setLayout] = useState<Layout>(read)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const lists = [window.matchMedia(SIDEBAR), window.matchMedia(RAIL)]
    const update = () => {
      setLayout(read())
    }
    update()
    for (const list of lists) list.addEventListener('change', update)
    return () => {
      for (const list of lists) list.removeEventListener('change', update)
    }
  }, [])
  return layout
}
