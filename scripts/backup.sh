#!/usr/bin/env bash
# Backup físico lógico do PostgreSQL sem incluir credenciais na linha de comando.
set -euo pipefail
deploy_dir="${DEPLOY_DIR:-deploy/production}"
deploy_dir="$(realpath "$deploy_dir")"
[[ -f "$deploy_dir/compose.yaml" && -f "$deploy_dir/.env" ]] || { echo 'Implantação incompleta' >&2; exit 2; }
backup_dir="${BACKUP_DIR:-$deploy_dir/backups}"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="$backup_dir/postgres-$stamp.dump"
compose=(docker compose --project-directory "$deploy_dir" --env-file "$deploy_dir/.env" -f "$deploy_dir/compose.yaml")
umask 077
temp="$(mktemp "$backup_dir/.postgres-$stamp.XXXXXX")"
trap 'rm -f "$temp"' EXIT
"${compose[@]}" exec -T postgres sh -ec 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$temp"
[[ -s "$temp" ]] || { echo 'Backup vazio; abortado' >&2; exit 1; }
mv -- "$temp" "$file"
trap - EXIT
sha256sum "$file" > "$file.sha256"
echo "Backup PostgreSQL gerado em $file (SHA-256: $file.sha256). Objetos S3 exigem backup separado."
