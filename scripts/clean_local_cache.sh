#!/usr/bin/env bash
set -euo pipefail
label='org.opencontainers.image.source=https://github.com/wkarts/reforma-tributaria-platform'
docker images --filter dangling=true --filter "label=$label" --format '{{.ID}} {{.Size}}'
if [[ "${CLEAN_APPLY:-false}" != true ]]; then
  echo 'DRY RUN. CLEAN_APPLY=true apaga somente imagens dangling com label deste repositório.'
  exit 0
fi
docker image prune --force --filter "label=$label"
