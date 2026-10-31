#!/usr/bin/env bash
# Back up the classbeat database of a compose stack (M6, docs/runbooks/backup-restore.md).
#
#   scripts/backup.sh [-o DIR] [--keep N] --gpg-recipient FPR --gpg-sign FPR       (the default)
#   scripts/backup.sh [-o DIR] [--keep N] --no-encrypt --sign-key KEY.pem           (accepted risk)
#
# Encrypted and signed is the only mode without --no-encrypt: the script refuses to write a backup
# that is neither. pg_dump (custom format) is piped into `gpg --encrypt --sign` and no plaintext
# dump touches the disk. FPR are full 40-digit key fingerprints (also BACKUP_GPG_RECIPIENT and
# BACKUP_GPG_SIGNER): the archive is encrypted to exactly that public key and signed by exactly
# that secret key, which must be in the invoking user's keyring and usable without a prompt
# (cron). restore.sh accepts the archive only when that same signer verifies (SR-06, #355).
#
# --no-encrypt writes a plaintext dump (mode 600) and prints a warning. It still needs --sign-key
# (also BACKUP_SIGN_KEY): a PEM private key (EC or RSA) kept OUTSIDE the backup directory; the
# detached signature is <name>.dump.sig. A bare checksum beside the archive is not accepted by
# restore.sh, because whoever can replace the archive can replace it too.
#
# Runs pg_dump in the `db` container as the superuser over the local socket (no password on a
# command line); the superuser is needed because every owned table has forced row level security.
# In the backup: the database `classbeat`. NOT in it: secrets/, the KEK files, the roles'
# passwords. Treat the file as sensitive either way.
#
# --keep N keeps the newest N backups (files named classbeat-<UTC timestamp>.dump[.gpg] in DIR,
# with their .sig) and deletes older ones, never the new one.
# The stack is picked like any compose command: COMPOSE_PROJECT_NAME / .env / COMPOSE_FILE.
set -euo pipefail

out=backups
keep=""
recipient="${BACKUP_GPG_RECIPIENT:-}"
signer="${BACKUP_GPG_SIGNER:-}"
sign_key="${BACKUP_SIGN_KEY:-}"
no_encrypt=0
die() { echo "backup.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    -o) [ $# -ge 2 ] || die "-o needs a directory"; out="$2"; shift 2 ;;
    --keep) [ $# -ge 2 ] && [ -n "$2" ] || die "--keep needs a number"; keep="$2"; shift 2 ;;
    --gpg-recipient) [ $# -ge 2 ] || die "--gpg-recipient needs a fingerprint"; recipient="$2"; shift 2 ;;
    --gpg-sign) [ $# -ge 2 ] || die "--gpg-sign needs a fingerprint"; signer="$2"; shift 2 ;;
    --no-encrypt) no_encrypt=1; shift ;;
    --sign-key) [ $# -ge 2 ] || die "--sign-key needs a file"; sign_key="$2"; shift 2 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [ -n "$keep" ]; then
  [[ "$keep" =~ ^[0-9]+$ ]] || die "--keep must be a positive whole number"
  keep=$((10#$keep)) # decimal, whatever the leading zeros ("08" is not octal here)
  [ "$keep" -ge 1 ] || die "--keep must be at least 1"
fi

fpr_ok() { [[ "$1" =~ ^[0-9A-Fa-f]{40}$ ]]; }
if [ "$no_encrypt" = 1 ]; then
  [ -z "$recipient" ] && [ -z "$signer" ] || die "--no-encrypt cannot be combined with --gpg-recipient or --gpg-sign"
  [ -n "$sign_key" ] || die "--no-encrypt still needs --sign-key (restore.sh refuses an unsigned archive)"
  [ -f "$sign_key" ] || die "no such signing key file: $sign_key"
  command -v openssl > /dev/null || die "openssl is not installed"
  openssl pkey -in "$sign_key" -noout 2> /dev/null || die "$sign_key is not a usable PEM private key"
  echo "backup.sh: WARNING: --no-encrypt writes the database in plaintext; it is signed, not secret." >&2
else
  [ -n "$recipient" ] && [ -n "$signer" ] \
    || die "refusing to write an unencrypted backup: give --gpg-recipient and --gpg-sign (full fingerprints), or --no-encrypt --sign-key"
  fpr_ok "$recipient" || die "--gpg-recipient must be a full 40-digit key fingerprint"
  fpr_ok "$signer" || die "--gpg-sign must be a full 40-digit key fingerprint"
  recipient="${recipient^^}"; signer="${signer^^}"
  command -v gpg > /dev/null || die "gpg is not installed"
  gpg --batch --list-keys "$recipient" > /dev/null 2>&1 || die "no public key $recipient in the keyring"
  gpg --batch --list-secret-keys "$signer" > /dev/null 2>&1 || die "no secret key $signer in the keyring"
fi

docker compose ps --status running --services 2> /dev/null | grep -qx db \
  || die "the db service of this compose project is not running"

umask 077
mkdir -p "$out"
if [ "$no_encrypt" = 1 ]; then
  # The signing key must not live where an attacker who can write backups can also read or swap it.
  out_real="$(cd "$out" && pwd -P)"
  key_real="$(cd "$(dirname "$sign_key")" && pwd -P)"
  case "$key_real/" in "$out_real"/*) die "the signing key must be kept outside the backup directory" ;; esac
fi
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="classbeat-$stamp.dump"
partial="$out/$name.partial"
trap 'rm -f "$partial"' EXIT

final="$out/$name"
if [ "$no_encrypt" = 0 ]; then
  # Streamed into gpg: no plaintext dump is ever written to disk, and pipefail makes a failing
  # pg_dump fail the script. The encrypted archive is not read back here (that needs the private
  # key); the restore drill and a monthly restore of a real backup are its test.
  final="$final.gpg"
  docker compose exec -T db pg_dump -U postgres -d classbeat --format=custom \
    | gpg --batch --yes --trust-model always --no-default-recipient --encrypt --sign \
      --local-user "$signer" --recipient "$recipient" --output "$partial"
  [ -s "$partial" ] || die "pg_dump or gpg produced an empty file"
  mv "$partial" "$final"
else
  docker compose exec -T db pg_dump -U postgres -d classbeat --format=custom > "$partial"
  [ -s "$partial" ] || die "pg_dump produced an empty file"
  # The archive must be readable by pg_restore and list its table of contents.
  docker compose exec -T db pg_restore --list < "$partial" > /dev/null \
    || die "the dump cannot be read back by pg_restore"
  openssl dgst -sha256 -sign "$sign_key" -out "$partial.sig" "$partial" || die "signing failed"
  mv "$partial.sig" "$final.sig"
  mv "$partial" "$final"
  chmod 600 "$final.sig"
fi
chmod 600 "$final"
echo "$final"

if [ -n "$keep" ]; then
  shopt -s nullglob
  # Names start with a UTC timestamp, so a reverse sort is newest first. Only the exact names
  # this script writes: classbeat-<YYYYmmddTHHMMSSZ>.dump[.gpg].
  mapfile -t all < <(printf '%s\n' "$out"/classbeat-*.dump "$out"/classbeat-*.dump.gpg \
    | grep -E '/classbeat-[0-9]{8}T[0-9]{6}Z\.dump(\.gpg)?$' | sort -r || true)
  if [ "${#all[@]}" -gt "$keep" ]; then
    for old in "${all[@]:$keep}"; do
      [ "$old" != "$final" ] || continue # never the backup just made
      rm -f "$old" "$old.sig"
      echo "backup.sh: pruned $old" >&2
    done
  fi
fi
