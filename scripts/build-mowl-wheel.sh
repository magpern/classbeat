#!/usr/bin/env bash
# Builds the mowl-workouts wheel classbeat pins (ADR 0001, threat model "accepted risks"):
# the source of a release tag, built WITHOUT the bundled Swedebeat app key. The release wheel
# on GitHub carries mowl/_swedebeat_app.json (written by that repo's release workflow from a
# secret); the source never does, so a wheel built from the tagged source has no key file.
#
#   scripts/build-mowl-wheel.sh            # v0.6.0 into backend/vendor/
#
# Needs `gh` signed in with read access to magpern/mowl-workouts (private) and `uv`. It never
# prints a token. Then refresh the lock: `cd backend && uv lock`.
#
# Checks before writing: the tag still points at the pinned commit; the built wheel has no
# _swedebeat_app.json; every other file outside the dist-info has the same SHA-256 as in the
# release wheel's RECORD (so it is the released code, minus the key); and METADATA (in the
# dist-info, so not in that comparison) declares the same Requires-Dist lines, because those
# decide what `uv lock` installs.
#
# What was pinned (a rebuild must print the same release digest; the vendored wheel's hash is
# what uv.lock verifies at install time):
#   tag v0.6.0 commit        0319fcb8c779806653a55251edd196ebfa18369c
#   release asset sha256     5ea21460062170116cb1947802e2b4046477502c30478dd87ef9940bf11160f1
#   vendored wheel sha256    d3957a800b346613f77184cecd1e95fee41de8b935a7ceb65b64f2a70bb9b12c
# The two wheels differ (the release one has the key file), so their digests differ too.
set -euo pipefail

REPO=magpern/mowl-workouts
TAG=v0.6.0
COMMIT=0319fcb8c779806653a55251edd196ebfa18369c
WHEEL=mowl_workouts-0.6.0-py3-none-any.whl
KEY_FILE=mowl/_swedebeat_app.json

PYTHON="$(command -v python3 || command -v python || true)"
die() { echo "build-mowl-wheel.sh: $*" >&2; exit 1; }

[ -n "$PYTHON" ] || die "python3 is required"
root="$(git rev-parse --show-toplevel)"
out="$root/backend/vendor"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

actual="$(gh api "repos/$REPO/commits/$TAG" --jq .sha)"
[ "$actual" = "$COMMIT" ] || die "$TAG points at $actual, expected $COMMIT"

mkdir -p "$work/src" "$work/release" "$work/dist"
gh api "repos/$REPO/tarball/$COMMIT" > "$work/source.tar.gz"
tar -xzf "$work/source.tar.gz" -C "$work/src" --strip-components=1
[ ! -e "$work/src/src/$KEY_FILE" ] || die "the tagged source contains $KEY_FILE"

# Reproducible timestamps inside the wheel: the commit's time.
epoch="$(gh api "repos/$REPO/commits/$COMMIT" --jq .commit.committer.date)"
SOURCE_DATE_EPOCH="$(date -d "$epoch" +%s)"
export SOURCE_DATE_EPOCH
uv build --wheel --out-dir "$work/dist" "$work/src" >/dev/null
built="$work/dist/$WHEEL"
[ -f "$built" ] || die "uv build did not produce $WHEEL"

"$PYTHON" - "$built" "$KEY_FILE" <<'PY' || die "the built wheel contains the key file"
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
sys.exit(1 if any(n.endswith("_swedebeat_app.json") for n in names) else 0)
PY

# Same code as the release: compare every RECORD hash except the key file and the dist-info,
# then the dependency declarations in METADATA.
gh release download "$TAG" -R "$REPO" -p "$WHEEL" -D "$work/release"
echo "release asset:"
sha256sum "$work/release/$WHEEL"
"$PYTHON" - "$built" "$work/release/$WHEEL" "$KEY_FILE" <<'PY' || die "the built wheel differs from the release"
import sys, zipfile

def record(path):
    wheel = zipfile.ZipFile(path)
    name = next(n for n in wheel.namelist() if n.endswith(".dist-info/RECORD"))
    rows = (line.split(",") for line in wheel.read(name).decode().splitlines() if line)
    return {r[0]: r[1] for r in rows if not r[0].endswith(".dist-info/RECORD")}

built, release, key = record(sys.argv[1]), record(sys.argv[2]), sys.argv[3]
code = lambda rec: {k: v for k, v in rec.items() if ".dist-info/" not in k and k != key}
if code(built) != code(release):
    sys.exit(1)

def requires(path):
    wheel = zipfile.ZipFile(path)
    name = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
    return sorted(
        line.strip() for line in wheel.read(name).decode().splitlines()
        if line.startswith("Requires-Dist:")
    )

if requires(sys.argv[1]) != requires(sys.argv[2]):
    sys.exit(1)
PY

mkdir -p "$out"
cp "$built" "$out/$WHEEL"
echo "wrote backend/vendor/$WHEEL"
sha256sum "$out/$WHEEL"
