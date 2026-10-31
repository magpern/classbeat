import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

// The fake Spotify of compose.e2e.yaml (ADR 0009 section 13; e2e/fake-spotify/server.py): it
// runs in the e2e stack, answers as api.spotify.com and accounts.spotify.com to web and worker
// inside the compose network (they trust its throwaway CA), and publishes no port. Public test
// values only; nothing here reaches a real Spotify host.

const ROOT = new URL('../..', import.meta.url)
const CLIENT_A = 'e2e0'.repeat(8)
const USER_A = 'e2e-rider-one'
const CALLBACK = `http://127.0.0.1:${process.env.WEB_PORT ?? '8000'}/oauth/spotify/callback`

function compose(args: string[]): string {
  return execFileSync('docker', ['compose', ...args], {
    cwd: ROOT,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

/** Runs a Python snippet in a stack container (the fake is only reachable from there). */
function inContainer(service: 'web' | 'worker', script: string, ...args: string[]): string {
  return compose(['exec', '-T', service, 'python', '-c', script, ...args]).trim()
}

const CONTROL = [
  'import sys, urllib.request as u',
  'u.install_opener(u.build_opener(u.ProxyHandler({})))',
  'q = u.Request("https://api.spotify.com/__e2e/" + sys.argv[2], method=sys.argv[1])',
  'print(u.urlopen(q, timeout=15).read().decode())',
].join('\n')

function control<T>(method: 'GET' | 'POST', path: string): T {
  return JSON.parse(inContainer('web', CONTROL, method, path)) as T
}

// The browser's visit to the authorize page, then the token exchange with PKCE: leaves `token`
// (never printed) and `back` (the callback's query) for the snippet that follows.
const TOKEN = String.raw`
import base64, hashlib, json, secrets, sys
import urllib.error as e, urllib.parse as p, urllib.request as u
client, redirect = sys.argv[1], sys.argv[2]
verifier = secrets.token_urlsafe(32)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
class Stay(u.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
opener = u.build_opener(u.ProxyHandler({}), Stay)
query = p.urlencode({"client_id": client, "response_type": "code", "redirect_uri": redirect,
    "scope": "playlist-read-private playlist-modify-private", "state": "e2e-state",
    "code_challenge_method": "S256", "code_challenge": challenge})
try:
    opener.open("https://accounts.spotify.com/authorize?" + query, timeout=15)
    raise SystemExit("the authorize page did not redirect")
except e.HTTPError as answer:
    location = answer.headers["Location"]
back = p.parse_qs(p.urlsplit(location).query)
form = p.urlencode({"grant_type": "authorization_code", "code": back["code"][0],
    "redirect_uri": redirect, "client_id": client, "code_verifier": verifier}).encode()
token = json.load(opener.open(u.Request("https://accounts.spotify.com/api/token", data=form,
    headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=15))
`

// What classbeat's connect does after the exchange: GET /me. Prints the user id and the granted
// scope, never a token.
const CONNECT = TOKEN + String.raw`
me = json.load(opener.open(u.Request("https://api.spotify.com/v1/me",
    headers={"Authorization": "Bearer " + token["access_token"]}), timeout=15))
print(json.dumps({"user": me["id"], "scope": token["scope"], "state": back["state"][0]}))
`

// The read-back a playlist write verifies with (ADR 0008 section 2, A3), in the worker: classbeat's
// credentials for the new token, the library's paged `playlist_items` (50 entries a page) and
// `classbeat.playlists.common.observed_ids`. Prints the observed track ids, never a token.
const READ_BACK = TOKEN + String.raw`
import time
from mowl import spotify_web
from classbeat.connections.credentials import build_credentials
from classbeat.playlists import common
u.install_opener(u.build_opener(u.ProxyHandler({})))
payload = {"access_token": token["access_token"], "refresh_token": token.get("refresh_token", ""),
    "expires_at": time.time() + float(token["expires_in"])}
creds = build_credentials("spotify", payload, {"client_id": client})
print(json.dumps(common.observed_ids(spotify_web.playlist_items(creds, sys.argv[3]))))
`

const UNAUTHENTICATED_ME = [
  'import urllib.error as e, urllib.request as u',
  'u.install_opener(u.build_opener(u.ProxyHandler({})))',
  'try:',
  '    u.urlopen("https://api.spotify.com/v1/me", timeout=15)',
  'except e.HTTPError as answer:',
  '    print(answer.code)',
].join('\n')

interface FakeState {
  requests: {
    method: string
    host: string
    path: string
    client_id: string | null
    token_app: string | null
    token_user: string | null
    client_secret: boolean
    outcome: string
  }[]
}

test.beforeEach(() => {
  control('POST', 'reset')
})

test.afterAll(() => {
  control('POST', 'reset')
})

test('web connects through the fake Spotify with PKCE inside the compose network', () => {
  const connected = JSON.parse(inContainer('web', CONNECT, CLIENT_A, CALLBACK)) as Record<string, string>
  expect(connected).toEqual({
    user: USER_A,
    scope: 'playlist-read-private playlist-modify-private',
    state: 'e2e-state',
  })
  const state = control<FakeState>('GET', 'state')
  expect(state.requests.map((r) => [r.host, r.path, r.client_id, r.token_app, r.token_user])).toEqual([
    ['accounts.spotify.com', '/authorize', CLIENT_A, null, null],
    ['accounts.spotify.com', '/api/token', CLIENT_A, null, null],
    ['api.spotify.com', '/v1/me', null, CLIENT_A, USER_A],
  ])
  expect(state.requests.every((r) => !r.client_secret && r.outcome === 'answered')).toBe(true)
})

test('the worker reaches the fake Spotify too', () => {
  expect(inContainer('worker', UNAUTHENTICATED_ME)).toBe('401')
  const state = control<FakeState>('GET', 'state')
  expect(state.requests.map((r) => [r.host, r.path, r.outcome])).toEqual([['api.spotify.com', '/v1/me', 'answered']])
})

// #198 item 19: A3's paged read-back is unreachable through a pick (a final list holds at most 40
// songs, one page), so it is covered here at the playlist-read level: 120 tracks over three pages.
test('the worker reads a 120-track playlist back in order over three pages (A3)', () => {
  const { id } = control<{ id: string }>('POST', `playlist?owner=${USER_A}&name=E2E%20read&tracks=120`)
  const observed = JSON.parse(inContainer('worker', READ_BACK, CLIENT_A, CALLBACK, id)) as (string | null)[]
  const expected = Array.from({ length: 120 }, (_, i) => `FakeTrack${String(i).padStart(13, '0')}`)
  expect(observed).toEqual(expected)
  const state = control<FakeState>('GET', 'state')
  const reads = state.requests.filter((r) => r.host === 'api.spotify.com')
  expect(reads.map((r) => [r.path, r.token_app, r.token_user, r.outcome])).toEqual(
    Array.from({ length: 3 }, () => [`/v1/playlists/${id}/items`, CLIENT_A, USER_A, 'answered']),
  )
})

test('the fake Spotify publishes no port: it is reachable only inside the compose network', () => {
  const config = JSON.parse(compose(['config', '--format', 'json'])) as {
    services: Record<string, { ports?: unknown[]; networks?: Record<string, { aliases?: string[] } | null> }>
  }
  const fake = config.services['spotify-fake']
  expect(fake).toBeDefined()
  expect(fake?.ports ?? []).toEqual([])
  expect(Object.keys(fake?.networks ?? {})).toEqual(['default'])
  expect(fake?.networks?.default?.aliases?.sort()).toEqual(['accounts.spotify.com', 'api.spotify.com'])
  // The running container: no port of it is published on the host.
  const out = compose(['ps', '--format', 'json', 'spotify-fake']).trim()
  const rows = (out.startsWith('[') ? JSON.parse(out) : out.split('\n').map((l) => JSON.parse(l))) as {
    Service: string
    State: string
    Publishers: { PublishedPort: number }[] | null
  }[]
  expect(rows.map((r) => [r.Service, r.State])).toEqual([['spotify-fake', 'running']])
  expect((rows[0]?.Publishers ?? []).filter((p) => p.PublishedPort !== 0)).toEqual([])
})
