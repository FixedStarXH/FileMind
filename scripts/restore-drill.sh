#!/usr/bin/env bash
set -Eeuo pipefail

backup="${1:?Usage: AGE_IDENTITY=... scripts/restore-drill.sh <backup.age> <filemind-restore-name>}"
project="${2:?A fresh project name beginning filemind-restore- is required}"
identity="${AGE_IDENTITY:?Set AGE_IDENTITY to the private age identity file}"
if [[ ! "$project" =~ ^filemind-restore-[a-z0-9-]+$ ]]; then
  echo "Restore drills must target an isolated project named filemind-restore-*" >&2
  exit 2
fi
command -v age >/dev/null || { echo "age is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

if [[ -n "$(docker compose -p "$project" --profile production --profile ops ps -q 2>/dev/null || true)" ]]; then
  echo "Target Compose project already contains containers; choose a fresh project name" >&2
  exit 2
fi
if [[ -n "$(docker volume ls -q --filter "name=${project}_" 2>/dev/null || true)" ]]; then
  echo "Target Compose project already contains volumes; choose a fresh project name" >&2
  exit 2
fi

stage="$(mktemp -d "${TMPDIR:-/tmp}/filemind-restore.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT INT TERM
age -d -i "$identity" -o "$stage/backup.tar.gz" "$backup"
tar -xzf "$stage/backup.tar.gz" -C "$stage"
(cd "$stage" && sha256sum -c SHA256SUMS)

# Let Docker select free ephemeral host ports, bound only to loopback.
export API_BIND=127.0.0.1:
export POSTGRES_BIND=127.0.0.1:
export REDIS_BIND=127.0.0.1:
export WEB_BIND=127.0.0.1:
export APP_ENV=production
export APP_ORIGIN=http://127.0.0.1

docker compose -p "$project" up -d postgres redis
for attempt in $(seq 1 60); do
  if docker compose -p "$project" exec -T postgres pg_isready -U filemind -d filemind >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 60 ]]; then echo "Restored PostgreSQL did not become ready" >&2; exit 1; fi
  sleep 2
done

docker compose -p "$project" exec -T postgres \
  pg_restore -U filemind -d filemind --no-owner --no-privileges --clean --if-exists < "$stage/database.dump"
docker compose -p "$project" exec -T postgres \
  psql -U filemind -d filemind -c 'TRUNCATE TABLE sessions'
docker compose -p "$project" --profile ops run --rm -T --no-deps volume-tools \
  -C /data -xzf - < "$stage/files.tar.gz"

docker compose -p "$project" --profile production up -d --build
api_address="$(docker compose -p "$project" port api 8000 | tail -n 1)"
web_address="$(docker compose -p "$project" --profile production port web 8080 | tail -n 1)"
api_port="${api_address##*:}"
web_port="${web_address##*:}"
if [[ ! "$api_port" =~ ^[0-9]+$ || ! "$web_port" =~ ^[0-9]+$ ]]; then
  echo "Could not determine isolated API/frontend ports" >&2
  exit 1
fi
export APP_ORIGIN="http://127.0.0.1:$web_port"
docker compose -p "$project" up -d --no-deps --force-recreate api
for attempt in $(seq 1 90); do
  if curl --fail --silent "http://127.0.0.1:$api_port/health/ready" >/dev/null; then
    echo "Restore drill is ready at http://127.0.0.1:$web_port"
    exit 0
  fi
  sleep 2
done
echo "Restore completed but readiness did not recover; inspect docker compose -p $project logs" >&2
exit 1
