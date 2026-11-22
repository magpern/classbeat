import type { ClassSongs } from '../../api/classSongs'
import type { MixSuggestion, MixWindow } from '../../api/studioMix'
import { StudioMixChoices } from './StudioMixChoices'
import type { MixState } from './mixChoices'
import { useApprovedSongs } from './useApprovedSongs'

/** The DJ mix's choices with the approved songs' names, read only while DJ mix is on. */
export function StudioMixSection({
  classId,
  songs,
  suggestions,
  state,
  windows,
  disabled,
  onChange,
}: {
  classId: string
  songs: ClassSongs
  suggestions: readonly MixSuggestion[]
  state: MixState
  windows: readonly MixWindow[] | null
  disabled: boolean
  onChange: (next: MixState) => void
}) {
  const names = useApprovedSongs(classId, songs)
  return (
    <StudioMixChoices
      suggestions={suggestions}
      names={names}
      state={state}
      windows={windows}
      disabled={disabled}
      onChange={onChange}
    />
  )
}
