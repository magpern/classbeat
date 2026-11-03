# MOWL running discovery (ADR 0018, D1): 2026-11-02 read-only probes

Status: **mostly answered from official SATS content; save-side fields still unverified.**
Owner-authorized, read-only, a cloud agent session. Calls made: `login`, `POST v1/programs` (lists),
`GET v1/programs/{id}`, `GET v1/ProgramCategories`, `GET v1/ActivityTypes` and `GET v1/users/me` (its answer
was not recorded and nothing in this document depends on it). No save, update, delete or attach. No credential, token or personal data is
recorded here. Read samples are official SATS programs (public camps).

## How running is found (answers unknown 1)

- Sport is **not** a filter of `POST v1/programs`: `BikeTypeID` 0 to 7 returns the same result, and
  every program (cycling or running) has `BikeTypeID: 1`.
- Running is **`ActivityTypeID: 1`**. `GET v1/ActivityTypes` returns
  `[{0: "Cycling"}, {1: "Running"}]`.
- Campaigns ("camps") are **program categories**, found with `GET v1/ProgramCategories`
  (48 visible to the account; ids in the thousands, so scanning small ids misses them). Each has
  `ActivityTypeID`, `IsOfficial`, `NumberOfWorkouts` (0 even where programs exist, do not trust it),
  `CreatorName`. Running camps: SATS "RUN TO RACE 45" (8248), "RUN TO RACE 60" (8249), and many
  user-made ones (ids 8277 to 8969: "RUN 30", "Run 60", "Indoor running (30)", "Couch2FiveK", ...).
- List a camp with `POST v1/programs` and `ProgramCategoryID` = the camp id (12 programs each for
  8248 and 8249). The list entry has the program's own `ActivityTypeID: 1`, `TSS`, `TotalDuration`,
  `PlaylistID`, but **no segments**: read each with `GET v1/programs/{id}`.
- Search keys tried (`SearchText`, `Search`, `Keyword`) are not filters.

## What a running program looks like (24 programs, 540 intervals)

- Program: `ActivityTypeID: 1`, `BikeTypeID: 1`, `ProgramTypeID: 1`. Its **segments carry
  `ActivityTypeID: 0`** (cycling) in all 104 cases: do not use a segment's value to tell the sport.
- Interval keys are the cycling set (`Duration`, `ScaleCoggan`, `FTPFrom/To`, `MAPFrom/To`,
  `FTHRFrom/To`, `PulseZoneFrom/To`, `Gradient`, `PaceFrom/To`, `RPMFrom/To`, `PositionTypeID`,
  `CycleID`, `IntervalGame`, `FtpTest`, `Intensity`, `Scale1to5`, `ScaleBorg`, ...).
- Values in running programs: `RPMFrom/To` 0, `PositionTypeID` 1 (seated), `CycleID` 1, `Intensity` 1,
  `Scale1to5` 0, `ScaleBorg` 0, `IntervalGame` null, `FtpTest` null, **`PaceFrom/To` null**.
  **`Gradient`** is `0.0` (282) or `1.0` (258): the treadmill incline (the unit is **unverified**), set per
  interval; the same 1.0 sits on the work and the rest intervals of a program.
- **`ScaleCoggan` 1 to 5 applies** (zone set 1), with the same FTP bands as cycling: 1 = 0 to 55,
  2 = 56 to 75, 3 = 76 to 90, 4 = 91 to 105, 5 = 106 to 120 (`FTPFrom/To`, as percent of rFTP).
  Zones 6 and 7 were not seen (the 60 and 45 programs stay below).
- Warm-up/cool-down flags: `IsWarmup`, `IsCooldown`, `IsActiveRecovery` on segment and interval.
- **TSS** is published in the same field (`TSS`, e.g. 103.76 for 3380 s). It matches the app card.
- All 24 programs have a `PlaylistID`; `PlaylistType` null. Whether classbeat can attach a Spotify playlist to a running program is **untested**.

## Against ADR 0018's unknowns

| # | Unknown | Now |
|---|---|---|
| 1 | Running id, filter | Answered: `ActivityTypeID: 1` on programs (and categories); `BikeTypeID` stays 1 |
| 2 | `ActivityTypeID` on programs/segments | Programs 1; segments of official programs 0. What a save must send is **unverified** |
| 3 | Running interval JSON, gradient, pace | Answered for reads: `Gradient` 0.0/1.0, `Pace*` null, RPM 0, seated, no games or FTP test |
| 4 | Scale | `ScaleCoggan` 1 to 5 with FTP bands as cycling; no parallel scale seen |
| 5 | rTSS | Published in `TSS` and matches the app card; the formula is **not derived** here |
| 6 | Spotify on running | `PlaylistID` present on all 24; attach by classbeat **untested** |
| 7 | Example payloads | `GET v1/programs/{id}` for the 24 official programs (raw JSON kept out of the repo); **no user-made running program was read** |

## Still open

Whether `v1/programs` create/update needs the program or each segment to carry `ActivityTypeID: 1`,
how a user-made running program stores `Gradient` and whether `list_workouts` for a user's running
programs needs a different request: only a running program the owner saves in the app (then a second
read-only probe) can answer that. This read-only pass wrote nothing to the account (the owner-authorized save test of 2026-11-03 is described below).

Reproduce (read-only): `mowl.api.call(creds, "GET", "v1/ProgramCategories")`, then
`POST v1/programs` with `ProgramCategoryID` = 8249, then `mowl.api.get_workout(creds, id)`.

## Live save test (2026-11-03, owner-authorized)

One private running program was saved from a one-off script with the requests the cycling save makes
(program, three segments, their intervals, the segment list) and read back. Findings:

- `PUT v1/programs` with `ActivityTypeID: 1` is accepted. The program reads back `ActivityTypeID: 1`,
  `BikeTypeID: 1`, `IsPublic: false`.
- Segments saved with `ActivityTypeID: 0` read back `0`, as in the official programs. A segment value of 1
  was **not** tried.
- Intervals sent as running bodies (cycling keys, RPM 0, seated, `Gradient` as the floats 0.0 and 1.0) read
  back unchanged: `ScaleCoggan` 1 to 3 as sent, `Gradient` 0.0/1.0, `RPMFrom` 0, `PaceFrom` null.
- `TSS` is stored as the client sends it (4.419 sent and read back): the server does not recompute it, so
  the library's running TSS model is what the user sees. It is not calibrated against running data.
- The program is in the unchanged My Workouts list request (`POST v1/programs`, category 17,
  `BikeTypeID: 1`) with its own `ActivityTypeID: 1`.
- Not tested: updating a running program (`PUT v1/programs/{id}`), attaching a new playlist or mix to a running
  program, and any delete (the library has none; the owner deletes the test program in the app).
