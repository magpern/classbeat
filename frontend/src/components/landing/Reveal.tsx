import { useRef, type ComponentPropsWithoutRef, type ReactNode } from 'react'
import { useReveal } from '../../hooks/useReveal'

interface Props extends ComponentPropsWithoutRef<'div'> {
  /** The element to render: a list item stays a list item. */
  as?: 'div' | 'section' | 'li'
  /** Wait this long (ms) after scrolling into view; a row of cards uses 0, 120, 240 … */
  delay?: number
  children?: ReactNode
}

/** A block that fades in when it scrolls into view (`useReveal`); the same markup as its plain element. */
export function Reveal({ as = 'div', delay = 0, children, ...rest }: Props) {
  // The tag is only ever div, section or li: all plain HTMLElements, so one ref type serves them.
  const Tag = as as 'div'
  const ref = useRef<HTMLDivElement>(null)
  useReveal(ref, delay)
  return (
    <Tag {...rest} ref={ref}>
      {children}
    </Tag>
  )
}
