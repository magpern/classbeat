import { ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { useT } from '../../i18n/I18n'
import { gameLabel, gameSlot } from '../../lib/games'
import { GameLicence } from '../classes/GameBits'
import { SlotPicture } from '../imagery/SlotPicture'
import { Chip, ChipButton } from '../ui/chip'
import { GAME_CARDS, GROUPS, GROUP_LABEL, PHRASES, type IdeaGroup } from './ideas'

/**
 * "Ideas for your class": closed by default; inside, a row of Climbs, Sprints, Intervals and Games.
 * Tapping a phrase or a game card hands its sentence to `onAdd`, which puts it in the prompt.
 * Games are shown only when `gamesOn` (the plan includes them) and the class is not running; they
 * are marked as cycling, and the licence line is the same one the class page shows. Nothing here
 * checks a licence.
 */
export function IdeasRow({ gamesOn, running, onAdd }: { gamesOn: boolean; running: boolean; onAdd: (phrase: string) => void }) {
  const t = useT()
  const [group, setGroup] = useState<IdeaGroup | null>(null)
  const showGames = gamesOn && !running
  const groups = GROUPS.filter((g) => g !== 'games' || showGames)
  // A group that has just been hidden (Running was picked) is closed.
  const open = group !== null && groups.includes(group) ? group : null
  return (
    <details className="group rounded-lg border border-line bg-surface px-4">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1 font-medium [&::-webkit-details-marker]:hidden">
        {t('start.ideas.title')}
        <ChevronDown aria-hidden="true" size={16} className="text-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="flex flex-col gap-3 pb-4">
        <div className="flex flex-wrap gap-2" role="group" aria-label={t('start.ideas.title')}>
          {groups.map((g) => (
            <ChipButton
              key={g}
              pressed={open === g}
              onClick={() => {
                setGroup(open === g ? null : g)
              }}
            >
              {t(GROUP_LABEL[g])}
            </ChipButton>
          ))}
        </div>
        {open !== null && open !== 'games' ? (
          <div className="flex flex-wrap gap-2">
            {PHRASES[open].map((key) => (
              <ChipButton
                key={key}
                pressed={false}
                onClick={() => {
                  onAdd(t(key))
                }}
              >
                {t(key)}
              </ChipButton>
            ))}
          </div>
        ) : null}
        {open === 'games' ? <GameCards onAdd={onAdd} /> : null}
      </div>
    </details>
  )
}

function GameCards({ onAdd }: { onAdd: (phrase: string) => void }) {
  const t = useT()
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0">
        <Chip>{t('start.game.cycling')}</Chip>
      </p>
      <ul className="m-0 grid list-none gap-3 p-0 sm:grid-cols-2">
        {GAME_CARDS.map(({ game, article }) => {
          const slot = gameSlot(game)
          const name = gameLabel(game)
          return (
            <li key={game} className="contents">
              <button
                type="button"
                aria-label={t('start.game.add', { game: name })}
                data-game={game}
                onClick={() => {
                  onAdd(t('start.phrase.game', { game: name }))
                }}
                className="flex w-full cursor-pointer flex-col gap-2 rounded-lg border border-line bg-raised p-3 text-left text-text hover:border-brand"
              >
                {slot === null ? null : <SlotPicture slot={slot} sizes="14rem" className="slot--game-card" />}
                <strong>{name}</strong>
                <span className="text-[0.875rem] text-muted">{t(article)}</span>
              </button>
            </li>
          )
        })}
      </ul>
      <GameLicence />
    </div>
  )
}
