import { X } from 'lucide-react'
import { useT } from '../../i18n/I18n'
import { AddMusicMenu } from './AddMusicMenu'
import { attachedPlaylists, type MusicTags, type PlaylistTag } from './musicTags'

/** The attached-music tags under the text box, each with its name and a remove button, then "Add music". */
export function MusicTagRow({
  offered,
  tags,
  noMusicOffered,
  onTogglePlaylist,
  onToggleNone,
}: {
  offered: readonly PlaylistTag[]
  tags: MusicTags
  noMusicOffered: boolean
  onTogglePlaylist: (tag: PlaylistTag) => void
  onToggleNone: () => void
}) {
  const t = useT()
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ul className="m-0 flex min-w-0 list-none flex-wrap gap-2 p-0" aria-label={t('composer.music.attached')}>
        {tags.kind === 'none' ? (
          <Tag name={t('music.none')} onRemove={onToggleNone} />
        ) : (
          attachedPlaylists(tags).map((tag) => (
            <Tag
              key={tag.id}
              name={tag.name}
              source={t('composer.music.source')}
              onRemove={() => {
                onTogglePlaylist(tag)
              }}
            />
          ))
        )}
      </ul>
      <AddMusicMenu
        offered={offered}
        tags={tags}
        noMusicOffered={noMusicOffered}
        onTogglePlaylist={onTogglePlaylist}
        onToggleNone={onToggleNone}
      />
    </div>
  )
}

function Tag({ name, source, onRemove }: { name: string; source?: string; onRemove: () => void }) {
  const t = useT()
  return (
    <li className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-full border border-line bg-raised py-1 pl-3.5 pr-1 text-sm text-text">
      <span className="min-w-0 truncate font-semibold">{name}</span>
      {source ? <span className="shrink-0 text-muted">{source}</span> : null}
      <button
        type="button"
        aria-label={t('composer.music.remove', { name })}
        onClick={onRemove}
        className="inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full border-0 bg-transparent text-muted hover:bg-line hover:text-text"
      >
        <X aria-hidden="true" size={14} strokeWidth={2.2} />
      </button>
    </li>
  )
}
