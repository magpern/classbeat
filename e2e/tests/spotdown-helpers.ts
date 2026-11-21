import { inContainer } from "./spotify-helpers";
import { sql } from "./song-helpers";

// Shared by spotdown-pick.spec.ts: the fake spotdown's test control.
// Public test values only: the fake answers as http://spotdown-api:8000 inside the compose network
// (compose.e2e.yaml, e2e/fake-spotdown), never a real spotdown, YouTube or Spotify.

const CONTROL = [
  "import sys, urllib.request as u",
  "u.install_opener(u.build_opener(u.ProxyHandler({})))",
  'q = u.Request("http://spotdown-api:8000/__e2e/" + sys.argv[2], method=sys.argv[1])',
  "print(u.urlopen(q, timeout=15).read().decode())",
].join("\n");

function control<T>(method: "GET" | "POST", path: string): T {
  return JSON.parse(inContainer("web", CONTROL, method, path)) as T;
}

/** The fake spotdown forgets what it was asked and what it analysed. */
export function resetSpotdown(): void {
  control("POST", "reset");
}

/** Every request the fake spotdown got since the last reset: method and path only. */
export function spotdownRequests(): { method: string; path: string }[] {
  return control<{ requests: { method: string; path: string }[] }>(
    "GET",
    "state",
  ).requests;
}

/** How many requests of that method and path the fake spotdown got. */
export function spotdownCount(method: string, path: string): number {
  return spotdownRequests().filter(
    (r) => r.method === method && r.path === path,
  ).length;
}

/** The id of a signed-up user, by email (constants only: never user text). */
export function userId(email: string): string {
  return sql(
    `SELECT id FROM users WHERE email = '${email.replaceAll("'", "''")}'`,
  );
}
