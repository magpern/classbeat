# classbeat

Describe an indoor-cycling class; Claude picks the songs, times the efforts to the music and
saves the class to your Intelligent Cycling (MOWL) account.

Private, invite-only beta in development. Not made by or affiliated with MOWL, Spotify or
Swedebeat.

- What we're building and in what order: [docs/roadmap.md](docs/roadmap.md)
- Where it stands: [docs/status.md](docs/status.md)
- How agents work on it: [AGENTS.md](AGENTS.md)
- Decisions: [docs/adr/](docs/adr/)

Getting started locally is described in AGENTS.md under "Developer commands" once the
skeleton lands.

## Refreshing the queue functions' md5

Web and worker refuse to start unless each job queue function matches the `md5` in
`backend/src/classbeat/db/queue_digests.py` (ADR 0005 section 3). After changing
`docker/postgres/queue/20-queue.sql` (or the pinned Postgres image in `compose.yaml`), regenerate
it against a throwaway Postgres of that same image where the migrations and the script have run
(the database test setup in AGENTS.md):

```bash
cd backend
uv run python tests/queue_digests.py --write   # CLASSBEAT_TEST_DATABASE_URL = the superuser URL
```

Commit the result. CI runs `--check` on the pinned image and fails with a diff when it is stale.
A change that alters what the functions read also bumps the version (`classbeat-queue vN`) in the
script and `QUEUE_VERSION` in `classbeat/db/queue_check.py`.
