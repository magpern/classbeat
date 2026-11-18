import { createPickJob, type PickInput } from './songs'
import { classContext, currentConnectionIds, followUpBrief } from '../lib/songs'
import { actionKey, dropActionKey } from '../lib/submittedInput'

/**
 * "Create the playlist again" (ADR 0008 section 6, ADR 0038 section 7): a resume of the approved
 * list. The server reads the list, its hash, its base and `save` from the approval receipt and
 * ignores whatever else the browser sends, so only the list's id goes; it takes no credit. The
 * key is kept until the call succeeds, so a repeated click returns the same job.
 */
export async function resumePlaylist(args: {
  classId: string
  listRevisionId: string
  listJobId: string
  targetSec: number
}): Promise<string> {
  const ids = await currentConnectionIds()
  const context = await classContext(args.classId)
  const input: PickInput = {
    class_id: args.classId,
    brief: followUpBrief(context.brief, args.targetSec, ''),
    mode: 'resume',
    resume: { song_list_revision_id: args.listRevisionId },
    mowl_connection_id: ids.mowl,
    spotify_connection_id: ids.spotify,
  }
  const scope = `resume.${args.listJobId}`
  const job = await createPickJob(input, actionKey(scope))
  dropActionKey(scope)
  return job.id
}
