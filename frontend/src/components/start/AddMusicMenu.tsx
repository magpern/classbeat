import { Check, ChevronDown, Plus } from 'lucide-react'
import { useEffect, useId, useRef, useState, type RefObject } from 'react'
import { useT } from '../../i18n/I18n'
import { cn } from '../../lib/cn'
import { MAX_MUSIC_TAGS, canAttach, isAttached, searchPlaylists, type MusicTags, type PlaylistTag } from './musicTags'

/**
 * "Add music" (ADR 0038 section 10.1): a closed disclosure with a search box over the user's Spotify playlists
 * and, where it is offered, "No music". Several playlists can be chosen (at most MAX_MUSIC_TAGS); the menu stays
 * open while the instructor chooses, and Escape or a click outside closes it. Choosing an attached playlist
 * again removes it. No SwedeBeat mix or playlist is listed (ADR 0038 section 3). The panel is anchored to the
 * nearest positioned ancestor (the tag row) and spans it up to a maximum width, so it never passes the screen's
 * edge at phone width. Escape returns focus to the button.
 */
export function AddMusicMenu({
  offered,
  tags,
  noMusicOffered,
  onTogglePlaylist,
  onToggleNone,
  buttonRef,
}: {
  offered: readonly PlaylistTag[]
  tags: MusicTags
  noMusicOffered: boolean
  onTogglePlaylist: (tag: PlaylistTag) => void
  onToggleNone: () => void
  /** The "Add music" button, so the row can return focus to it after a tag is removed. */
  buttonRef: RefObject<HTMLButtonElement | null>
}) {
  const t = useT()
  const panelId = useId()
  const root = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && root.current?.contains(event.target) !== true) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => {
      document.removeEventListener('pointerdown', outside)
    }
  }, [open])
  const shown = searchPlaylists(offered, query)
  const full = !canAttach(tags)
  return (
    <div
      ref={root}
      onKeyDown={(event) => {
        if (event.key === 'Escape' && open) {
          event.stopPropagation()
          setOpen(false)
          buttonRef.current?.focus()
        }
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => {
          setOpen((o) => !o)
        }}
        className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-full border border-dashed border-brand bg-surface px-4 text-[0.9375rem] font-semibold text-brand"
      >
        <Plus aria-hidden="true" size={16} strokeWidth={2.2} />
        {t('composer.music.add')}
        <ChevronDown aria-hidden="true" size={16} className={cn('transition-transform', open && 'rotate-180')} />
      </button>
      <div
        id={panelId}
        hidden={!open}
        role="group"
        aria-label={t('composer.music.panel')}
        className="absolute inset-x-0 top-full z-20 mt-2 flex max-w-[22rem] flex-col gap-2 rounded-lg border border-line bg-surface p-3 shadow-lg"
      >
        <input
          type="search"
          aria-label={t('composer.music.search')}
          placeholder={t('composer.music.search')}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
          }}
          className="min-h-11 w-full rounded-md border border-line bg-raised px-3 text-base text-text placeholder:text-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        />
        <div className="flex max-h-64 flex-col gap-0.5 overflow-y-auto">
          {noMusicOffered ? <Option checked={tags.kind === 'none'} label={t('music.none')} onSelect={onToggleNone} /> : null}
          <span className="px-1 pb-0.5 pt-1.5 text-xs font-bold uppercase tracking-wide text-muted">{t('composer.music.group')}</span>
          {shown.map((tag) => {
            const checked = isAttached(tags, tag.id)
            return (
              <Option
                key={tag.id}
                checked={checked}
                disabled={full && !checked}
                label={tag.name}
                onSelect={() => {
                  onTogglePlaylist(tag)
                }}
              />
            )
          })}
          {shown.length === 0 ? <p className="m-0 px-2 py-2 text-muted">{t(offered.length === 0 ? 'composer.music.none' : 'start.music.noMatch')}</p> : null}
        </div>
        {full ? <p className="m-0 text-[0.8125rem] text-muted">{t('composer.music.limit', { max: MAX_MUSIC_TAGS })}</p> : null}
      </div>
    </div>
  )
}

function Option({
  checked,
  disabled = false,
  label,
  onSelect,
}: {
  checked: boolean
  disabled?: boolean
  label: string
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={checked}
      disabled={disabled}
      onClick={onSelect}
      className={cn(
        'flex min-h-11 w-full cursor-pointer items-center gap-2.5 rounded-md border-0 px-2 text-left text-[0.9375rem] text-text disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'bg-raised' : 'bg-transparent hover:bg-raised',
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-flex size-5 shrink-0 items-center justify-center rounded-sm border-[1.5px]',
          checked ? 'border-brand bg-brand text-on-brand' : 'border-muted bg-transparent',
        )}
      >
        {checked ? <Check size={12} strokeWidth={3} /> : null}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  )
}
