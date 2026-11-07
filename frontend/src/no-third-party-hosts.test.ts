import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import indexHtml from '../index.html?raw'

// Every shipped stylesheet and script is bundled from src/; fonts are bundled too.
// This guards the "no runtime requests to third-party hosts" rule at the source level.
// Vitest returns an empty string for ?raw CSS imports, so the stylesheets are read from disk.
function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    return entry.isDirectory() ? cssFiles(path) : path.endsWith('.css') ? [path] : []
  })
}
const stylesheets: Record<string, string> = Object.fromEntries(
  cssFiles(import.meta.dirname).map((path) => [path, readFileSync(path, 'utf8')]),
)
const scripts = import.meta.glob<string>(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

const REMOTE_URL = /(?:https?:)?\/\/(?!localhost\b|127\.0\.0\.1\b)[a-z0-9.-]+\.[a-z]{2,}/gi

// The one exception (ADR 0013 section 9 and its amendment of 2026-11-03, #537): album and playlist
// covers from Spotify's image hosts, named only in CoverArt, which shows them. The exemption is
// each exact quoted string in that one file.
const COVER_ART = './components/songs/CoverArt.tsx'
const SPOTIFY_LOGO = './components/songs/SpotifyAttribution.tsx'
// ADR 0024: the MOWL QR code, served from our own origin (the API refuses any other path).
const MOWL_QR = './components/account/MowlQr.tsx'
const COVER_HOSTS = [
  'https://i.scdn.co',
  'https://mosaic.scdn.co',
  'https://image-cdn-ak.spotifycdn.com',
  'https://image-cdn-fa.spotifycdn.com',
]
const QUOTED = COVER_HOSTS.map((host) => `'${host}'`)
const COVER_HOST = QUOTED[0] ?? ''
// ADR 0025 section 11: Cloudflare Turnstile's script, inserted by the Turnstile component on the
// four auth forms only. The exemption is this exact quoted string in that one file.
const TURNSTILE = './components/auth/Turnstile.tsx'
const TURNSTILE_SCRIPT = "'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'"

/** The remote URLs in a source file, after the SVG namespace and the allowed hosts. */
function remoteUrls(path: string, source: string): string[] {
  // The SVG namespace is an identifier, not a request.
  let checked = source.replaceAll('http://www.w3.org/2000/svg', '')
  if (path === COVER_ART) for (const quoted of QUOTED) checked = checked.replaceAll(quoted, "''")
  if (path === TURNSTILE) checked = checked.replaceAll(TURNSTILE_SCRIPT, "''")
  return checked.match(REMOTE_URL) ?? []
}

describe('no third-party hosts', () => {
  it('finds the sources it is meant to scan', () => {
    expect(Object.keys(stylesheets).length).toBeGreaterThanOrEqual(2)
    for (const css of Object.values(stylesheets)) expect(css.length).toBeGreaterThan(0)
    expect(Object.keys(scripts).length).toBeGreaterThanOrEqual(3)
    expect(Object.keys(scripts)).toContain(COVER_ART)
  })

  it('index.html loads nothing from a remote host', () => {
    expect(indexHtml.match(REMOTE_URL)).toBeNull()
  })

  it.each(Object.entries({ ...stylesheets, ...scripts }))('%s has no remote URL', (path, source) => {
    expect(remoteUrls(path, source)).toEqual([])
  })

  it('CoverArt names each cover host once and no other host', () => {
    const source = scripts[COVER_ART] ?? ''
    for (const quoted of QUOTED) expect(source.split(quoted).length - 1).toBe(1)
    expect(remoteUrls('./elsewhere.tsx', source).sort()).toEqual([...COVER_HOSTS].sort())
  })

  it('still fails for the cover host in any other file, and for any other host in CoverArt', () => {
    expect(remoteUrls('./components/songs/SongRows.tsx', `const a = ${COVER_HOST}`)).toEqual([
      'https://i.scdn.co',
    ])
    expect(remoteUrls('./components/songs/CoverArt2.tsx', `const a = ${COVER_HOST}`)).toHaveLength(1)
    for (const quoted of QUOTED) {
      expect(remoteUrls('./components/newclass/PlaylistTiles.tsx', `const a = ${quoted}`)).toHaveLength(1)
    }
    for (const other of [
      'https://daily-mix.scdn.co',
      'https://image-cdn-xx.spotifycdn.com',
      'https://blend-playlist-covers.spotifycdn.com',
      'https://evil.example',
      '//cdn.example.com',
    ]) {
      expect(remoteUrls(COVER_ART, `const a = '${other}'`)).toEqual([other])
    }
    // The host inside a longer string is not the exempt string: it is caught too.
    expect(remoteUrls(COVER_ART, "const a = 'https://i.scdn.co/image/x'")).toHaveLength(1)
    expect(remoteUrls(COVER_ART, 'const a = "https://i.scdn.co"')).toHaveLength(1)
  })

  it('the Turnstile component names its script once and no other host', () => {
    const source = scripts[TURNSTILE] ?? ''
    expect(source.split(TURNSTILE_SCRIPT).length - 1).toBe(1)
    expect(remoteUrls('./elsewhere.tsx', source)).toEqual(['https://challenges.cloudflare.com'])
    expect(remoteUrls(TURNSTILE, source)).toEqual([])
  })

  it('still fails for the Turnstile script in any other file, and for anything else in that file', () => {
    expect(remoteUrls('./pages/SignInPage.tsx', `const a = ${TURNSTILE_SCRIPT}`)).toHaveLength(1)
    expect(remoteUrls('./components/auth/AuthScreen.tsx', `const a = ${TURNSTILE_SCRIPT}`)).toHaveLength(1)
    for (const other of [
      "'https://challenges.cloudflare.com/turnstile/v0/api.js'",
      "'https://challenges.cloudflare.com'",
      '"https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"',
      "'https://evil.example/api.js?render=explicit'",
      "'//challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'",
    ]) {
      expect(remoteUrls(TURNSTILE, `const a = ${other}`)).toHaveLength(1)
    }
  })

  it('index.html never loads the Turnstile script (only the component does, on the auth forms)', () => {
    expect(indexHtml).not.toContain('challenges.cloudflare.com')
  })

  it('no app source names or builds a Spotify URL (links come only from the API)', () => {
    const naming = Object.entries(scripts)
      .filter(([path]) => !path.startsWith('./test/'))
      .filter(([, source]) =>
        /open\.spotify\.com|spotify:(?:track|playlist|album|artist)|spotify\.link/i.test(source),
      )
      .map(([path]) => path)
    expect(naming).toEqual([])
  })

  it('only CoverArt, the Spotify logo and the MOWL QR code render an image element', () => {
    // src/test/ holds test fixtures (hostile text such as an <img> in a description), not app code.
    const withImg = Object.entries(scripts)
      .filter(([path]) => !path.startsWith('./test/'))
      .filter(([, source]) => /<img\b|createElement\(\s*['"]img['"]|new Image\(/.test(source))
      .map(([path]) => path)
    expect(withImg.sort()).toEqual([COVER_ART, MOWL_QR, SPOTIFY_LOGO].sort())
    // The QR image's one source is the ticket's path, which the API checks is ours.
    expect((scripts[MOWL_QR] ?? '').match(/src=\{[^}]+\}/g)).toEqual(['src={ticket.image}'])
    // The logo's images come from the two bundled brand files and nothing else.
    const logo = scripts[SPOTIFY_LOGO] ?? ''
    expect(logo.match(/src=\{(\w+)\}/g)).toEqual(['src={whiteLogo}', 'src={blackLogo}'])
    expect(logo.match(/from '([^']+)'/g)).toEqual([
      "from '../../assets/brand/Full_Logo_Black_RGB.svg'",
      "from '../../assets/brand/Full_Logo_White_RGB.svg'",
    ])
  })
})
