# Sourced by scripts/backup-restore-drill.sh (not run on its own): the hardening cases' shims (#364).
# Needs $work, and defines real_cp, real_docker and real_openssl for the cases that use the shims.
real_cp="$(command -v cp)"; real_docker="$(command -v docker)"; real_openssl="$(command -v openssl)"
mkdir -p "$work/shim-cp" "$work/shim-docker" "$work/shim-openssl" "$work/swap"
cat > "$work/shim-cp/cp" <<'SHIM'
#!/usr/bin/env bash
# After the archive is copied to restore.sh's private copy, replace the ORIGINAL with garbage
# (a swap between verification and use). Whatever reads the original afterwards gets the garbage.
"$REAL_CP" "$@" || exit $?
for last; do :; done
case "$last" in */copy.dump | */copy.gpg) printf 'swapped after the copy\n' > "$DRILL_SWAP" ;; esac
SHIM
cat > "$work/shim-docker/docker" <<'SHIM'
#!/usr/bin/env bash
# pg_dump fails after writing a little; everything else is the real docker.
case " $* " in *" pg_dump "*) echo "PGDMP partial"; echo "pg_dump: simulated failure" >&2; exit 3 ;; esac
exec "$REAL_DOCKER" "$@"
SHIM
cat > "$work/shim-openssl/openssl" <<'SHIM'
#!/usr/bin/env bash
# `openssl dgst` fails after creating its -out file; everything else is the real openssl.
if [ "${1:-}" = dgst ]; then
  prev=""; out=""
  for a in "$@"; do [ "$prev" = -out ] && out="$a"; prev="$a"; done
  [ -z "$out" ] || echo junk > "$out"
  echo "openssl: simulated signing failure" >&2; exit 1
fi
exec "$REAL_OPENSSL" "$@"
SHIM
chmod +x "$work"/shim-*/*
