import { execFileSync } from "node:child_process";
import { appendFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { inContainer } from "./spotify-helpers";
import { sql } from "./song-helpers";

// Shared by spotdown-pick.spec.ts: the fake spotdown's test control and the worker's allowlist.
// Public test values only: the fake answers as http://spotdown-api:8000 inside the compose network
// (compose.e2e.yaml, e2e/fake-spotdown), never a real spotdown, YouTube or Spotify.

const ROOT = new URL("../..", import.meta.url);

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

const COMPOSE = [
  "compose",
  "-f",
  "compose.yaml",
  "-f",
  "compose.e2e.yaml",
  "--profile",
  "e2e",
];

/**
 * Where the worker's logs go before a recreate removes them. CI sets E2E_SAVED_LOGS to a file under
 * the runner temp directory and its "no secret value in the logs" step scans it with the rest.
 */
export const SAVED_LOGS =
  process.env.E2E_SAVED_LOGS ??
  join(tmpdir(), "classbeat-e2e-saved-worker.log");

/** Appends the running worker's logs to SAVED_LOGS (a recreate would remove them). */
export function saveWorkerLogs(): void {
  const logs = execFileSync(
    "docker",
    [...COMPOSE, "logs", "--no-color", "worker"],
    {
      cwd: ROOT,
      encoding: "utf8",
      maxBuffer: 256 * 1024 * 1024,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  appendFileSync(SAVED_LOGS, logs);
}

/** Writes one line to the worker's stdout, so the saved logs can be shown to hold it. */
export function plantWorkerLogMarker(marker: string): void {
  execFileSync(
    "docker",
    [
      ...COMPOSE,
      "exec",
      "-T",
      "worker",
      "sh",
      "-c",
      `echo ${marker} > /proc/1/fd/1`,
    ],
    {
      cwd: ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
}

/**
 * Recreates the worker with `CLASSBEAT_SPOTDOWN_SPOTIFY_OWNERS` set to these user ids (none: the
 * stack's own, empty list). The list is read once, when the worker starts, and the ids of the
 * e2e users are only known after they sign up. The spec files run one after the other, so no
 * other run is going; a model script link (/fake-run) is a tmpfs the recreate resets to the
 * default, which every spec sets again before it needs another. A recreate removes the old
 * container's logs, so they are saved first (saveWorkerLogs) for the CI secret scan.
 */
export function allowSpotdownOwners(...ids: string[]): void {
  saveWorkerLogs();
  execFileSync(
    "docker",
    [
      ...COMPOSE,
      "up",
      "-d",
      "--no-deps",
      "--force-recreate",
      "--wait",
      "worker",
    ],
    {
      cwd: ROOT,
      encoding: "utf8",
      env: { ...process.env, E2E_SPOTDOWN_OWNERS: ids.join(",") },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
}
