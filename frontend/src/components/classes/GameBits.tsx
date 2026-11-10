import type { DraftGame } from '../../api/draftTypes'
import { useT } from '../../i18n/I18n'
import { gameLabel, gameSlot } from '../../lib/games'
import { SlotPicture } from '../imagery/SlotPicture'

/** A game's name with its small illustration (the legacy game: the name only). */
export function GameBadge({ game }: { game: DraftGame }) {
  const slot = gameSlot(game)
  return (
    <div className="flex items-center gap-3" data-testid="game-badge" data-game={game}>
      {slot === null ? null : <SlotPicture slot={slot} sizes="7rem" className="slot--game" />}
      <strong className="text-[0.9375rem]">{gameLabel(game)}</strong>
    </div>
  )
}

/** The one licence line of a class with a game. classbeat does not check a licence and never says it did. */
export function GameLicence() {
  const t = useT()
  return (
    <p className="m-0 text-[0.875rem] text-muted" data-testid="game-licence">
      {t('class.game.licence')}
    </p>
  )
}
