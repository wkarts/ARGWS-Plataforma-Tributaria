#!/usr/bin/env bash
# Deploy não destrutivo por imagens já publicadas, com migração e bootstrap one-shot.
set -euo pipefail
deploy_dir="${DEPLOY_DIR:-deploy/production}"
deploy_dir="$(realpath "$deploy_dir")"
channel="${DEPLOY_CHANNEL:-production}"
[[ -f "$deploy_dir/compose.yaml" && -f "$deploy_dir/.env" ]] || { echo 'Implantação incompleta' >&2; exit 2; }
python3 "$(dirname "$0")/preflight.py" --env-file "$deploy_dir/.env" --channel "$channel"
compose=(docker compose --project-directory "$deploy_dir" --env-file "$deploy_dir/.env" -f "$deploy_dir/compose.yaml")
"${compose[@]}" config --quiet
if [[ -n "$("${compose[@]}" ps -q postgres)" ]]; then
  DEPLOY_DIR="$deploy_dir" "$(dirname "$0")/backup.sh"
fi
"${compose[@]}" pull
"${compose[@]}" up -d --wait --wait-timeout 180
"${compose[@]}" ps
echo 'Deploy concluído; verifique saúde, fila, objetos S3 e logs. Nenhum volume foi removido.'
