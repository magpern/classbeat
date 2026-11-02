# MOWL running discovery (ADR 0018, D1): 2026-11-02 read-only probe

Status: **inconclusive, D1 still open.** Owner-authorized, read-only, one cloud agent session.
No credential, token, program name or program id from the account is recorded here.

## What was run

`mowl.api.login` for the owner's own MOWL account, then only `POST v1/programs` (the My Workouts
listing call, `mowl.api.list_workouts`'s request). No `GET v1/programs/{id}`, no write, no attach.

## Findings

1. **`BikeTypeID` sent as 0 to 7 returns the same 14 programs** in My Workouts
   (`ProgramCategoryID` 17). The request field either is not the filter or is ignored for this
   account. Every returned program has `BikeTypeID: 1`, `ActivityTypeID: 0`, `ProgramTypeID: 1`,
   and empty `BikeTypeName` and `ProgramTypeName`.
2. **Categories 0 to 39** (`ProgramCategoryID`, with `BikeTypeID: 1`): 28 are non-empty (camps,
   climbs, on-demand, FTP tests, My Workouts, ...). Every program in them is `BikeTypeID: 1`,
   `ActivityTypeID: 0`, `ProgramTypeID: 1`. No category with running content is visible to this
   account.
3. The listing entry carries these keys (names only): `ActivityTypeID`, `AllowCopy`, `BikeTypeID`,
   `BikeTypeName`, `CacheIdentifier`, `CategoryName`, `CreatorAlias`, `CreatorID`, `CreatorName`,
   `DateCreated`, `DateUpdated`, `Description`, `DesignerTypeName`, `Hidden`, `IsPublic`,
   `IsUserFavoriteProgram`, `Name`, `Playlist`, `PlaylistID`, `PlaylistLink`, `PlaylistType`,
   `ProgramCategoryID`, `ProgramID`, `ProgramTypeID`, `ProgramTypeName`, `SegmentCount`,
   `Segments`, `ShareImageURI`, `ShareUri`, `ShortDescription`, `SpotifyUrl`, `TSS`,
   `TargetGroupID`, `TargetGroupName`, `TotalDuration`.

## What it means for ADR 0018's unknowns

| # | Unknown | After this probe |
|---|---|---|
| 1 | Running `BikeTypeID`, and the filter | Not found. Cycling is `1`. The list request's `BikeTypeID` did not filter; a different field, endpoint or app-side sport switch probably selects running |
| 2 to 6 | `ActivityTypeID`, interval fields, scale, rTSS, Spotify attach | Open. This account has no running program to read |
| 7 | Example running payloads | Open |

## Next step (owner)

Either the ADR's default (capture the MOWL desktop app's traffic while switching to RUN and opening
an official running workout) or one exported running program file. A running program saved to
the account in the app would let a second read-only probe (`GET v1/programs/{id}`) answer 2 to 7.
Agents write nothing to the account to produce one.

Reproduce: sign in with `mowl.api.login`, then `mowl.api.call(creds, "POST", "v1/programs",
form={"ProgramCategoryID": 17, "PageNumber": 0, "itemsPerPage": 50, "BikeTypeID": n,
"ProgramOrdering": "creationDateDesc"})`.
