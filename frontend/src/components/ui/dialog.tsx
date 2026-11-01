import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { createContext, forwardRef, useContext, useEffect, useRef, useState, type ComponentProps, type Ref } from 'react'
import { cn } from '../../lib/cn'
import { keepTabInside, lockPageBehind } from '../../lib/modalLock'

const DialogTriggerRefContext = createContext<React.RefObject<HTMLElement | null> | null>(null)

function assignRef<T>(ref: Ref<T> | undefined, value: T | null): void {
  if (typeof ref === 'function') ref(value)
  else if (ref) ref.current = value
}

/**
 * A modal dialog that works under `style-src 'self'` (ADR 0013 section 9). Radix's modal mode
 * locks the page scroll with an injected `<style>`, which the policy refuses (a console error
 * per open, and no lock). So Radix runs non-modal here, and the modal behaviour is ours and
 * class-based: `lockPageBehind` (scroll lock and `inert`), our own overlay (a click on it
 * closes), a Tab loop, and a taller-than-the-window dialog scrolls inside itself.
 */
export function Dialog(props: ComponentProps<typeof DialogPrimitive.Root>) {
  const triggerRef = useRef<HTMLElement | null>(null)
  return (
    <DialogTriggerRefContext.Provider value={triggerRef}>
      <DialogPrimitive.Root {...props} modal={false} />
    </DialogTriggerRefContext.Provider>
  )
}

export const DialogTrigger = forwardRef<
  HTMLButtonElement,
  ComponentProps<typeof DialogPrimitive.Trigger>
>(function DialogTrigger(props, forwardedRef) {
  const triggerRef = useContext(DialogTriggerRefContext)
  return (
    <DialogPrimitive.Trigger
      {...props}
      ref={(node) => {
        if (triggerRef) triggerRef.current = node
        assignRef(forwardedRef, node)
      }}
    />
  )
})
export const DialogClose = DialogPrimitive.Close

export function DialogContent({
  className,
  children,
  onKeyDown,
  onCloseAutoFocus,
  onFocusOutside,
  onPointerDownOutside,
  ...props
}: ComponentProps<typeof DialogPrimitive.Content>) {
  const triggerRef = useContext(DialogTriggerRefContext)
  // The portal renders after the first commit, so the element arrives through state.
  const [node, setNode] = useState<HTMLDivElement | null>(null)
  useEffect(() => (node ? lockPageBehind(node) : undefined), [node])
  return (
    <DialogPrimitive.Portal>
      <div aria-hidden="true" className="fixed inset-0 z-40 bg-scrim" />
      <DialogPrimitive.Content
        ref={setNode}
        aria-modal="true"
        className={cn(
          'fixed top-1/2 left-1/2 z-50 max-h-[calc(100%-2rem)] w-[min(28rem,calc(100%-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-line bg-surface p-5 text-text',
          className,
        )}
        onKeyDown={(event) => {
          onKeyDown?.(event)
          if (node && !event.defaultPrevented) keepTabInside(event, node)
        }}
        // Non-modal Radix skips trigger focus after an outside dismiss; restore it ourselves.
        onCloseAutoFocus={(event) => {
          onCloseAutoFocus?.(event)
          if (event.defaultPrevented) return
          const trigger = triggerRef?.current
          if (trigger) {
            trigger.focus()
            event.preventDefault()
          }
        }}
        // Focus is kept inside by the Tab loop; a pointer-down on another dialog (a prompt on
        // top of this one) is not a click away.
        onFocusOutside={(event) => {
          onFocusOutside?.(event)
          event.preventDefault()
        }}
        onPointerDownOutside={(event) => {
          onPointerDownOutside?.(event)
          if (event.target instanceof Element && event.target.closest('[role="dialog"]')) event.preventDefault()
        }}
        {...props}
      >
        {children}
        <DialogPrimitive.Close
          aria-label="Close"
          className="absolute top-3 right-3 inline-flex size-11 cursor-pointer items-center justify-center rounded-full border-0 bg-transparent text-muted hover:bg-raised"
        >
          <X aria-hidden="true" size={20} strokeWidth={2} />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}

export function DialogTitle({ className, ...props }: ComponentProps<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn('m-0 mb-2 font-display text-xl font-bold', className)} {...props} />
}

export function DialogDescription({ className, ...props }: ComponentProps<typeof DialogPrimitive.Description>) {
  return <DialogPrimitive.Description className={cn('m-0 mb-4 text-[0.9375rem] text-muted', className)} {...props} />
}
