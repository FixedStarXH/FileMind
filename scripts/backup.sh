#!/usr/bin/env bash
set -Eeuo pipefail

project="${COMPOSE_PROJECT_NAME:-filemind}"
destination="${1:?Usage: AGE_RECIPIENT=... scripts/backup.sh <backup-directory>}"
recipient="${AGE_RECIPIENT:?Set AGE_RECIPIENT to an age public recipient}"
command -v age >/dev/null || { echo "age is required" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "sha256sum is required" >&2; exit 1; }

mkdir -p "$destination"
umask 077
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage="$(mktemp -d "${TMPDIR:-/tmp}/filemind-backup.XXXXXX")"
payload="$stage/filemind-$stamp.tar.gz"
partial="$destination/.filemind-$stamp.tar.gz.age.partial"
final="$destination/filemind-$stamp.tar.gz.age"
stopped=0

resume_services() {
  if [[ "$stopped" == 1 ]]; then
    docker compose -p "$project" start api dispatcher worker-ingestion worker-agent worker-maintenance >/dev/null || true
    docker compose -p "$project" --profile production start web >/dev/null 2>&1 || true
  fi
  rm -rf -- "$stage"
  rm -f -- "$partial"
}
trap resume_services EXIT INT TERM

# Stop all writers so PostgreSQL and the private file volume represent one point in time.
stopped=1
docker compose -p "$project" stop api dispatcher worker-ingestion worker-agent worker-maintenance
docker compose -p "$project" --profile production stop web >/dev/null 2>&1 || true

docker compose -p "$project" exec -T postgres pg_dump -U filemind -d filemind -Fc > "$stage/database.dump"
docker compose -p "$project" --profile ops run --rm -T --no-deps volume-tools \
  -C /data -czf - . > "$stage/files.tar.gz"
(cd "$stage" && sha256sum database.dump files.tar.gz > SHA256SUMS)
tar -C "$stage" -czf "$payload" database.dump files.tar.gz SHA256SUMS
age -r "$recipient" -o "$partial" "$payload"
mv -- "$partial" "$final"
echo "Encrypted backup created: $final"
