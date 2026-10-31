import { get } from './client'
import { list, object, optionalText, text, type JsonObject } from './decode'
import { isPlaylistLink } from './classes'

// The playlists classbeat made for the user (GET /api/music/class-playlists, ADR 0013 sections 4
// and 9). Server text is data and is rendered as text. The link is the API's own, for the owner,
// and is shown only when it is a Spotify playlist link; the page never builds one.

export interface MosaicSong {
  title: string
  /** An album cover URL on the pinned host, or null; shown through `CoverArt` only. */
  cover: string | null
}

export interface MadePlaylist {
  classId: string
  classTitle: string
  name: string
  link: string | null
  detached: boolean
  note: string | null
  songs: MosaicSong[]
}

function decodeSong(value: unknown): MosaicSong {
  const item = object(value, 'song')
  return { title: text(item, 'title'), cover: optionalText(item, 'cover') }
}

function decodePlaylist(value: unknown): MadePlaylist {
  const item: JsonObject = object(value, 'playlist')
  const link = optionalText(item, 'link')
  return {
    classId: text(item, 'class_id'),
    classTitle: text(item, 'class_title'),
    name: text(item, 'name'),
    link: link !== null && isPlaylistLink(link) ? link : null,
    detached: item.detached === true,
    note: optionalText(item, 'note'),
    songs: list(item, 'songs').map(decodeSong),
  }
}

export async function getClassPlaylists(signal?: AbortSignal): Promise<MadePlaylist[]> {
  const body = object(await get('/api/music/class-playlists', signal), 'playlists')
  return list(body, 'playlists').map(decodePlaylist)
}
