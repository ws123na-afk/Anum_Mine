#!/usr/bin/env bash
# Backups and restores for ANUM on one VM (docs/deploy-oracle.md#backups). Run as root
# (sudo) or a docker-group user, from anywhere.
#
#   backup.sh backup                 nightly job (anum-backup.timer): dump ANUM's database
#                                    (anum_backup.py, manifest with row counts per tenant)
#                                    and Keycloak's database, keep ANUM_BACKUP_LOCAL_KEEP_DAYS
#                                    days on the VM (fetched and drill copies 7 days),
#                                    copy everything new to the off-site bucket, encrypted
#                                    on the VM first, and warn when .env changed since the
#                                    owner last copied it (it is in no backup)
#   backup.sh list                   what is in the off-site bucket
#   backup.sh fetch <name prefix>    download matching off-site files into <backup dir>/fetched
#   backup.sh reconcile [--delete]   list off-site objects no backup manifest references (dry run);
#                                    --delete removes those older than 35 days (backup_reconcile.py)
#   backup.sh files-reconcile [--delete]  list Files bucket objects no database row references
#                                    (dry run); --delete removes those older than 7 days
#                                    (anum_api.files_reconcile, in the retention job's container)
#   backup.sh drill                  restore drill: backup -> restore into a scratch
#                                    database -> verify (rows per tenant, RLS) -> drop
#   backup.sh restore <dump>         restore an ANUM dump (path under the backup dir) into a
#                                    new anum_restore_<UTC> database and verify it
#   backup.sh promote <database>     replace the live `anum` database with a verified
#                                    anum_restore_* database (asks for confirmation)
#   backup.sh restore-keycloak <dump>  replace Keycloak's database with a dump (asks)
#
# Dumps hold every tenant's data. They are written 0600 in 0700 directories owned by
# uid 10001, and leave the VM only through rclone crypt (ANUM_BACKUP_ENCRYPTION_PASSPHRASE).
# Connection strings and passwords stay in the containers' environment; nothing here
# prints them.
set -euo pipefail

ORACLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ORACLE_DIR/.env"
dc() { docker compose --project-directory "$ORACLE_DIR" -f "$ORACLE_DIR/compose.yaml" "$@"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
env_value() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'$/\1/"; }
stamp() { date -u +%Y%m%dT%H%M%SZ; }

# The outcome of the last backup, for the platform console's Ready to sell checklist
# (docs/selling-readiness.md#backups): times, ok and the local keep days only, world-readable
# in <backup dir>/status so the API container can mount that folder read-only.
record_status() {
  local kind="$1" ok="$2" status_dir="$BACKUP_DIR/status" file now last
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$status_dir" && chmod 755 "$status_dir"
  file="$status_dir/last-$kind.json"
  last="null"
  if [ "$ok" = "true" ]; then
    last="\"$now\""
  elif [ -f "$file" ]; then
    last="$(sed -n 's/.*"last_success_at": *\("[^"]*"\|null\).*/\1/p' "$file" | head -n 1)"
    last="${last:-null}"
  fi
  printf '{"ok": %s, "finished_at": "%s", "last_success_at": %s, "keep_days": %s}\n' "$ok" "$now" "$last" "$KEEP_DAYS" > "$file.tmp"
  chmod 644 "$file.tmp" && mv "$file.tmp" "$file"
}

[ -f "$ENV_FILE" ] || die "$ENV_FILE is missing; run install.sh first"
BACKUP_DIR="$(env_value ANUM_BACKUP_DIR)"
BACKUP_DIR="${BACKUP_DIR:-/var/lib/anum/backups}"
KEEP_DAYS="$(env_value ANUM_BACKUP_LOCAL_KEEP_DAYS)"
KEEP_DAYS="${KEEP_DAYS:-7}"
[[ "$KEEP_DAYS" =~ ^[0-9]+$ ]] || die "ANUM_BACKUP_LOCAL_KEEP_DAYS must be a number of days"

# .env holds every secret (the key that decrypts provider keys, the backup passphrase,
# the bucket keys) and is in no backup: a dump is useless without it. The owner copies it
# to a password manager and touches the marker; a newer .env brings the warning back.
ENV_COPIED_MARKER="$BACKUP_DIR/status/env-copied"
env_copy_reminder() {
  if [ ! -e "$ENV_COPIED_MARKER" ] || [ "$ENV_FILE" -nt "$ENV_COPIED_MARKER" ]; then
    printf '\n%s\n' "WARNING: $ENV_FILE has changed since it was last copied off this VM, and it is NOT in any backup." >&2
    echo "Without it a restored database cannot be read (ANUM_SECRETS_KEY) and the off-site copies cannot" >&2
    echo "be decrypted (ANUM_BACKUP_ENCRYPTION_PASSPHRASE). Copy its contents into your password manager:" >&2
    echo "  sudo cat $ENV_FILE" >&2
    echo "then record that you did:" >&2
    echo "  sudo touch $ENV_COPIED_MARKER" >&2
  fi
}

# A file under the backup directory, as the containers see it (/backups/...).
container_path() {
  local path="$1" relative
  case "$path" in
    "$BACKUP_DIR"/*) relative="${path#"$BACKUP_DIR"/}" ;;
    /*) die "$path is not under $BACKUP_DIR" ;;
    *) relative="$path" ;;
  esac
  case "/$relative/" in */../*) die "no .. in paths" ;; esac
  [ -f "$BACKUP_DIR/$relative" ] || die "$BACKUP_DIR/$relative not found"
  printf '/backups/%s' "$relative"
}

confirm() {
  local expected="$1" answer
  [ -t 0 ] || die "this step needs a terminal to confirm"
  read -r -p "Type '$expected' to continue: " answer
  [ "$answer" = "$expected" ] || die "not confirmed; nothing was changed"
}

admin_psql() {
  dc run --rm --no-deps -T --entrypoint psql restore-tools -X -q -v ON_ERROR_STOP=1 --dbname=postgres "$@"
}

case "${1:-}" in
  backup)
    echo "== ANUM database"
    if ! dc run --rm backup; then record_status local false; exit 1; fi
    echo "== Keycloak database"
    # $0 is the timestamp; the inner script runs in the backup image as uid 10001.
    # shellcheck disable=SC2016 # expanded by the container's shell
    dc run --rm --no-deps --entrypoint /bin/sh backup -c '
      set -eu
      umask 077
      mkdir -p /backups/keycloak
      chmod 700 /backups/keycloak
      file="/backups/keycloak/keycloak-$0.dump"
      pg_dump --format=custom --compress=6 --no-password --dbname=keycloak --file="$file"
      printf "{\"keycloak_dump\": \"%s\", \"size_bytes\": %s}\n" "$file" "$(stat -c %s "$file")"
    ' "$(stamp)" || { record_status local false; exit 1; }
    record_status local true
    echo "== Pruning local copies older than $KEEP_DAYS days"
    for directory in "$BACKUP_DIR/anum" "$BACKUP_DIR/keycloak"; do
      if [ -d "$directory" ]; then
        find "$directory" -maxdepth 1 -type f \( -name 'anum-*' -o -name 'keycloak-*' \) -mtime +"$KEEP_DAYS" -print -delete
      fi
    done
    # Downloaded copies (fetch, decrypted) and restore-drill leftovers are full dumps
    # too: a week is enough to finish a restore, and they must not fill the disk. By
    # change time, the day the file arrived here: rclone keeps the original modification
    # time, so an old backup fetched yesterday would otherwise be deleted tonight.
    for directory in "$BACKUP_DIR/fetched" "$BACKUP_DIR/drill"; do
      if [ -d "$directory" ]; then
        find "$directory" -mindepth 1 -type f -ctime +7 -print -delete
        find "$directory" -mindepth 1 -type d -empty -delete
      fi
    done
    echo "== Off-site copy (encrypted)"
    if ! dc run --rm backup-upload; then record_status offsite false; env_copy_reminder; exit 1; fi
    record_status offsite true
    env_copy_reminder
    ;;
  list)
    dc run --rm backup-upload lsl sealed:
    ;;
  fetch)
    prefix="${2:?usage: backup.sh fetch <name prefix, e.g. anum-20261011>}"
    [[ "$prefix" =~ ^[A-Za-z0-9_.-]+$ ]] || die "the prefix may contain letters, digits, ., _ and - only"
    dc run --rm backup-upload copy sealed: /backups/fetched --include "/anum/$prefix*" --include "/keycloak/$prefix*"
    echo "Downloaded into $BACKUP_DIR/fetched"
    ;;
  reconcile)
    # One-off: off-site objects no manifest references. Dry run unless --delete; never touches
    # anything younger than the retention window (docs/deploy-oracle.md#reconciling-the-off-site-bucket).
    command -v python3 >/dev/null || die "python3 is needed for the reconcile"
    python3 "$ORACLE_DIR/backup_reconcile.py" --compose-dir "$ORACLE_DIR" "${@:2}"
    ;;
  files-reconcile)
    # One-off: Files bucket objects whose rows were purged before November 2026. Dry run unless
    # --delete (docs/deploy-oracle.md#reconciling-the-files-bucket). The retention job's container
    # has the API's bucket settings and the application login.
    dc run --rm --no-deps retention python -m anum_api.files_reconcile "${@:2}"
    ;;
  drill)
    dc run --rm restore-tools drill --work-dir /backups/drill
    ;;
  restore)
    dump="$(container_path "${2:?usage: backup.sh restore <path to anum-*.dump under $BACKUP_DIR>}")"
    manifest="${dump%.dump}.manifest.json"
    [ -f "$BACKUP_DIR/${manifest#/backups/}" ] || die "manifest ${manifest#/backups/} not found next to the dump"
    target="anum_restore_$(date -u +%Y%m%d%H%M%S)"
    dc run --rm restore-tools restore --dump "$dump" --manifest "$manifest" --target "$target"
    # verify connects to the restored database with the admin login.
    # shellcheck disable=SC2016 # expanded by the container's shell
    dc run --rm --no-deps --entrypoint /bin/sh restore-tools -c \
      'exec python /usr/local/bin/anum_backup.py verify --database-url "${ANUM_RESTORE_ADMIN_URL%/postgres}/$0" --manifest "$1"' \
      "$target" "$manifest"
    echo "Restored and verified into $target. To make it the live database: backup.sh promote $target"
    ;;
  promote)
    target="${2:?usage: backup.sh promote <anum_restore_... database>}"
    [[ "$target" =~ ^anum_restore_[a-z0-9_]+$ ]] || die "the database must be an anum_restore_* database"
    previous="anum_replaced_$(date -u +%Y%m%d%H%M%S)"
    echo "This stops the API, renames the live database anum to $previous and $target to anum."
    echo "Everything written after that backup is lost (it stays in $previous)."
    confirm "$target"
    dc stop api
    admin_psql -c "select pg_terminate_backend(pid) from pg_stat_activity where datname in ('anum', '$target') and pid <> pg_backend_pid()" \
      -c "alter database anum rename to $previous" \
      -c "alter database $target rename to anum"
    # Re-applies ownership, grants, CONNECT rules and the passwords from .env.
    dc run --rm --no-deps db-init
    dc up -d
    echo "Done. The previous database is kept as $previous; drop it once you are sure:"
    echo "  backup.sh drop-database $previous"
    ;;
  drop-database)
    target="${2:?usage: backup.sh drop-database <anum_restore_* or anum_replaced_* or keycloak_replaced_*>}"
    [[ "$target" =~ ^(anum_restore|anum_replaced|keycloak_replaced)_[a-z0-9_]+$ ]] || die "only scratch or replaced databases can be dropped"
    confirm "$target"
    admin_psql -c "drop database $target with (force)"
    ;;
  restore-keycloak)
    dump="$(container_path "${2:?usage: backup.sh restore-keycloak <path to keycloak-*.dump under $BACKUP_DIR>}")"
    previous="keycloak_replaced_$(date -u +%Y%m%d%H%M%S)"
    echo "This stops Keycloak, renames its database to $previous and restores $dump as keycloak."
    confirm "keycloak"
    dc stop keycloak
    admin_psql -c "select pg_terminate_backend(pid) from pg_stat_activity where datname = 'keycloak' and pid <> pg_backend_pid()" \
      -c "alter database keycloak rename to $previous" \
      -c "create database keycloak owner keycloak"
    dc run --rm --no-deps -T --entrypoint pg_restore restore-tools \
      --no-owner --role=keycloak --exit-on-error --dbname=keycloak "$dump"
    dc run --rm --no-deps db-init
    dc up -d keycloak
    echo "Done. The previous Keycloak database is kept as $previous (backup.sh drop-database $previous)."
    ;;
  *)
    sed -n '2,29p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
