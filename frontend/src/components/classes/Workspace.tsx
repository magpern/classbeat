import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { useT } from '../../i18n/I18n'

export type WorkspaceTab = 'chat' | 'class'
type Tab = WorkspaceTab

const TABS: readonly Tab[] = ['chat', 'class']

/**
 * The two-panel class workspace (MA3, plan section D). From 1024 px up the chat is on the left and
 * the class on the right, side by side. Below that the same two regions are tabs, Chat | Class.
 * The layout is CSS only: both regions are always in the page, so a screen reader and the tests
 * see the same content at every width, and the inactive one is only hidden on a phone.
 */
export function Workspace({
  chat,
  work,
  tab,
  onTab: setTab,
}: {
  chat: ReactNode
  work: ReactNode
  /** The tab a phone shows; the page owns it so a jump to a panel can open the right one. */
  tab: Tab
  onTab: (tab: Tab) => void
}) {
  const t = useT()
  const buttons = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({})
  const label: Record<Tab, string> = { chat: t('ws.tab.chat'), class: t('ws.tab.class') }

  const onKey = (event: KeyboardEvent) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (step === 0 && event.key !== 'Home' && event.key !== 'End') return
    event.preventDefault()
    const at = TABS.indexOf(tab)
    const next =
      event.key === 'Home' ? 0 : event.key === 'End' ? TABS.length - 1 : (at + step + TABS.length) % TABS.length
    const target = TABS[next]!
    setTab(target)
    buttons.current[target]?.focus()
  }

  return (
    <div className="flex flex-col gap-4 max-lg:pb-24" data-testid="workspace">
      <div role="tablist" aria-label={t('ws.tabs')} className="flex gap-2 lg:hidden" onKeyDown={onKey}>
        {TABS.map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`ws-tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`ws-${id}`}
            tabIndex={tab === id ? 0 : -1}
            ref={(node) => {
              buttons.current[id] = node
            }}
            className={`button ${tab === id ? '' : 'button--secondary'} flex-1`}
            onClick={() => {
              setTab(id)
            }}
          >
            {label[id]}
          </button>
        ))}
      </div>
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        <section
          id="ws-chat"
          aria-label={t('ws.region.chat')}
          data-testid="ws-chat"
          className={`flex min-w-0 flex-col gap-5 ${tab === 'chat' ? '' : 'max-lg:hidden'}`}
        >
          {chat}
        </section>
        <section
          id="ws-class"
          aria-label={t('ws.region.class')}
          data-testid="ws-class"
          className={`flex min-w-0 flex-col gap-4 ${tab === 'class' ? '' : 'max-lg:hidden'}`}
        >
          {work}
        </section>
      </div>
    </div>
  )
}
