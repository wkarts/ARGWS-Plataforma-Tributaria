#!/usr/bin/env bash
# Promove imagens já testadas por digest e publica assets, com retomada segura.
set -euo pipefail

files="${1:?Passe o diretório de assets da release}"
[[ -d "$files" && -f "$files/IMAGES.txt" && -f "$files/SHA256SUMS.txt" ]] || {
  echo 'Assets da release incompletos.' >&2; exit 1;
}
: "${GH_TOKEN:?Token GitHub requerido}"
: "${GITHUB_REPOSITORY:?Repositório requerido}"
: "${GITHUB_SHA:?Commit requerido}"
: "${VERSION:?Versão requerida}"
: "${CHANNEL:?Canal requerido}"
[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || exit 1
[[ "$CHANNEL" == develop || "$CHANNEL" == main ]] || exit 1
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ ]] || exit 1
files="$(cd "$files" && pwd)"
(cd "$files" && sha256sum -c SHA256SUMS.txt)

image_digest() {
  local output digest
  if output="$(docker buildx imagetools inspect "$1" 2>inspect-error.txt)"; then
    digest="$(awk '$1=="Digest:" {print $2; exit}' <<< "$output")"
    [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo "Digest inválido: $1" >&2; exit 1; }
    printf '%s' "$digest"
  elif grep -Eiq '(not found|manifest unknown|NAME_UNKNOWN|404)' inspect-error.txt; then
    return 1
  else
    echo "Falha ao inspecionar $1; promoção bloqueada." >&2
    sed -n '1,8p' inspect-error.txt >&2
    return 2
  fi
}

mapfile -t image_refs < "$files/IMAGES.txt"
[[ "${#image_refs[@]}" -eq 2 ]] || { echo 'Esperadas API e web em IMAGES.txt' >&2; exit 1; }
for ref in "${image_refs[@]}"; do
  [[ "$ref" =~ ^ghcr\.io/[a-z0-9-]+/reforma-tributaria-(api|web)@sha256:[a-f0-9]{64}$ ]] || {
    echo "Referência inesperada em IMAGES.txt: $ref" >&2; exit 1;
  }
  image="${ref%@*}"
  expected="${ref#*@}"
  sha_digest="$(image_digest "$image:sha-${GITHUB_SHA::12}")"
  [[ "$sha_digest" == "$expected" ]] || { echo "Tag de commit diverge do digest: $image" >&2; exit 1; }
done

ref_sha() {
  local tag="$1" result kind object
  if result="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$tag" 2>gh-error.txt)"; then
    kind="$(jq -r 'if type == "object" then (.object.type // empty) else empty end' <<< "$result")"
    object="$(jq -r 'if type == "object" then (.object.sha // empty) else empty end' <<< "$result")"
    [[ -n "$object" ]] || return 1
    if [[ "$kind" == tag ]]; then
      object="$(gh api "repos/$GITHUB_REPOSITORY/git/tags/$object" --jq '.object.sha')"
    fi
    printf '%s' "$object"
  elif grep -Eq '(404|Not Found)' gh-error.txt || \
       jq -e '.status == "404" or .status == 404' <<< "$result" >/dev/null 2>&1; then
    return 1
  else
    cat gh-error.txt >&2; return 2
  fi
}

release_exists() {
  if gh release view "$1" --json tagName >/dev/null 2>gh-error.txt; then
    return 0
  elif grep -Eq '(404|not found|release not found)' gh-error.txt; then
    return 1
  else
    cat gh-error.txt >&2; return 2
  fi
}

promote() {
  local target="$1" source="$2" desired="${2#*@}" actual status
  if actual="$(image_digest "$target")"; then
    [[ "$actual" == "$desired" ]] || { echo "Tag existente com digest diferente: $target" >&2; exit 1; }
  else
    status=$?
    [[ "$status" -eq 1 ]] || exit "$status"
    docker buildx imagetools create -t "$target" "$source"
    actual="$(image_digest "$target")"
    [[ "$actual" == "$desired" ]] || { echo "Promoção não confirmou digest: $target" >&2; exit 1; }
  fi
}

if [[ "$CHANNEL" == develop ]]; then
  # Cada commit recebe uma release imutável, sem substituir a anterior.
  # A tag móvel GHCR :develop só é atualizada pelo HEAD corrente.
  preview_tag="v${VERSION}-develop.${GITHUB_SHA::12}"
  if current="$(ref_sha "$preview_tag")"; then
    [[ "$current" == "$GITHUB_SHA" ]] || { echo "Tag de prévia já pertence a outro commit." >&2; exit 1; }
  else
    status=$?
    [[ "$status" -eq 1 ]] || exit "$status"
  fi

  remote="$(mktemp -d)"
  verified="$(mktemp -d)"
  trap 'rm -rf "$remote" "$verified"' EXIT

  if release_exists "$preview_tag"; then
    draft="$(gh release view "$preview_tag" --json isDraft --jq '.isDraft' --repo "$GITHUB_REPOSITORY")"
    count="$(gh release view "$preview_tag" --json assets --jq '.assets | length' --repo "$GITHUB_REPOSITORY")"
    if (( count > 0 )); then
      gh release download "$preview_tag" --dir "$remote" --repo "$GITHUB_REPOSITORY"
    fi
  else
    status=$?
    [[ "$status" -eq 1 ]] || exit "$status"
    gh release create "$preview_tag" --target "$GITHUB_SHA" --draft --prerelease --latest=false \
      --title "Prévia $VERSION (${GITHUB_SHA::12})" \
      --notes "Build do commit $GITHUB_SHA. Artefatos conferidos por SHA256SUMS.txt e digests de IMAGES.txt." \
      --repo "$GITHUB_REPOSITORY"
    draft=true
  fi
  [[ "$(ref_sha "$preview_tag")" == "$GITHUB_SHA" ]] || {
    echo "Tag de prévia diverge do commit validado" >&2; exit 1;
  }

  if [[ "$draft" == true ]]; then
    # Reexecuções podem reconstruir um draft privado e incompleto.
    # Só releases publicadas têm assets imutáveis.
    for old_asset in "$remote"/*; do
      [[ -f "$old_asset" ]] || continue
      name="${old_asset##*/}"
      if [[ ! -f "$files/$name" ]]; then
        gh release delete-asset "$preview_tag" "$name" --yes --repo "$GITHUB_REPOSITORY"
      fi
    done
    gh release upload "$preview_tag" "$files"/* --clobber --repo "$GITHUB_REPOSITORY"
  else
    python3 - "$files" "$remote" <<'PY'
import hashlib
from pathlib import Path
import sys

expected, present = (Path(p) for p in sys.argv[1:])
local = {p.name: p for p in expected.iterdir() if p.is_file()}
remote = {p.name: p for p in present.iterdir() if p.is_file()}
if unknown := remote.keys() - local.keys():
    raise SystemExit(f"Assets remotos inesperados: {sorted(unknown)}")
for name, path in remote.items():
    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(local[name].read_bytes()).digest():
        raise SystemExit(f"Pré-release publicada divergente do commit: {name}")
PY
  fi

  gh release download "$preview_tag" --dir "$verified" --repo "$GITHUB_REPOSITORY"
  python3 - "$files" "$verified" <<'PY'
from pathlib import Path
import sys

expected, actual = ({p.name for p in Path(arg).iterdir() if p.is_file()} for arg in sys.argv[1:])
if expected != actual:
    raise SystemExit(f"Assets faltantes ou extras: {sorted(expected ^ actual)}")
PY
  cmp "$files/SHA256SUMS.txt" "$verified/SHA256SUMS.txt"
  (cd "$verified" && sha256sum -c SHA256SUMS.txt)

  if [[ "$draft" == true ]]; then
    gh release edit "$preview_tag" --prerelease --draft=false --latest=false --repo "$GITHUB_REPOSITORY"
  fi

  head_sha="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/develop" --jq '.object.sha')"
  if [[ "$head_sha" == "$GITHUB_SHA" ]]; then
    for ref in "${image_refs[@]}"; do
      image="${ref%@*}"
      docker buildx imagetools create -t "$image:develop" "$ref"
      [[ "$(image_digest "$image:develop")" == "${ref#*@}" ]] || exit 1
    done
  else
    echo "Commit $GITHUB_SHA publicado com tag imutável; HEAD develop atual é $head_sha. Tag móvel preservada."
  fi
  echo "Pré-release $preview_tag publicada e verificada."
  exit 0
fi

tag="v$VERSION"
if current="$(ref_sha "$tag")"; then
  [[ "$current" == "$GITHUB_SHA" ]] || { echo "Tag $tag pertence a outro commit." >&2; exit 1; }
else
  status=$?
  [[ "$status" -eq 1 ]] || exit "$status"
fi

# Uma tag SemVer de imagem só pode reaparecer se o digest permanecer idêntico.
for ref in "${image_refs[@]}"; do
  image="${ref%@*}"
  for name in "$VERSION" "$tag"; do
    if actual="$(image_digest "$image:$name")"; then
      [[ "$actual" == "${ref#*@}" ]] || {
        echo "A tag estável $image:$name já pertence a outro digest." >&2; exit 1;
      }
    else
      status=$?
      [[ "$status" -eq 1 ]] || exit "$status"
    fi
  done
done

remote="$(mktemp -d)"
trap 'rm -rf "$remote"' EXIT
if release_exists "$tag"; then
  existing_sha="$(ref_sha "$tag")"
  [[ "$existing_sha" == "$GITHUB_SHA" ]] || { echo 'Release existente aponta para outro commit.' >&2; exit 1; }
  asset_count="$(gh release view "$tag" --json assets --jq '.assets | length')"
  if (( asset_count > 0 )); then gh release download "$tag" --dir "$remote"; fi
else
  status=$?
  [[ "$status" -eq 1 ]] || exit "$status"
fi

# Assets já publicados têm prioridade no retry: nenhum arquivo estável é sobrescrito.
# Se existe manifest, os assets ausentes precisam reproduzir exatamente seu hash.
python3 - "$files" "$remote" <<'PY'
import hashlib
import pathlib
import shutil
import sys

local, remote = (pathlib.Path(arg) for arg in sys.argv[1:])
expected = {p.name for p in local.iterdir() if p.is_file() and p.name != 'SHA256SUMS.txt'}
actual = {p.name for p in remote.iterdir() if p.is_file()}
if not actual <= expected | {'SHA256SUMS.txt'}:
    raise SystemExit(f'Assets remotos inesperados: {sorted(actual - expected - {"SHA256SUMS.txt"})}')
manifest = remote / 'SHA256SUMS.txt'
if manifest.exists():
    records = {}
    for line in manifest.read_text(encoding='ascii').splitlines():
        digest, sep, name = line.partition('  ')
        if not sep or len(digest) != 64 or not all(c in '0123456789abcdef' for c in digest):
            raise SystemExit('Manifest de checksum remoto inválido')
        if name in records:
            raise SystemExit('Asset duplicado no manifest remoto')
        records[name] = digest
    if set(records) != expected:
        raise SystemExit('Manifest remoto diverge da lista de assets da release')
    for name, digest in records.items():
        path = remote / name if (remote / name).exists() else local / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit(f'Checksum divergente para {name}; não substituir asset estável')
    shutil.copy2(manifest, local / 'SHA256SUMS.txt')
else:
    for name in actual:
        shutil.copy2(remote / name, local / name)
    with (local / 'SHA256SUMS.txt').open('w', encoding='ascii') as out:
        for name in sorted(expected):
            digest = hashlib.sha256((local / name).read_bytes()).hexdigest()
            out.write(f'{digest}  {name}\n')
PY

if release_exists "$tag"; then
  :
else
  status=$?
  [[ "$status" -eq 1 ]] || exit "$status"
  gh release create "$tag" --target "$GITHUB_SHA" --draft --latest=false \
    --title "Gestão Fiscal $tag" --notes "Versão estável do commit $GITHUB_SHA. Confira SHA256SUMS.txt e IMAGES.txt."
fi
for path in "$files"/*; do
  name="${path##*/}"
  [[ "$name" == SHA256SUMS.txt || -f "$remote/$name" ]] && continue
  gh release upload "$tag" "$path"
done
if [[ ! -f "$remote/SHA256SUMS.txt" ]]; then
  gh release upload "$tag" "$files/SHA256SUMS.txt"
fi

verified="$(mktemp -d)"
trap 'rm -rf "$remote" "$verified"' EXIT
gh release download "$tag" --dir "$verified"
python3 - "$files" "$verified" <<'PY'
import pathlib, sys
expected, actual = ({p.name for p in pathlib.Path(arg).iterdir() if p.is_file()} for arg in sys.argv[1:])
if expected != actual:
    raise SystemExit(f'Assets faltantes ou extras: {sorted(expected ^ actual)}')
PY
(cd "$verified" && sha256sum -c SHA256SUMS.txt)

# Só promove depois de confirmar o conjunto inteiro de arquivos da release.
for ref in "${image_refs[@]}"; do
  image="${ref%@*}"
  promote "$image:$VERSION" "$ref"
  promote "$image:$tag" "$ref"
done
latest=false
if [[ "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')" == "$GITHUB_SHA" ]]; then
  latest=true
  for ref in "${image_refs[@]}"; do
    image="${ref%@*}"
    for moving in main stable latest; do
      docker buildx imagetools create -t "$image:$moving" "$ref"
      [[ "$(image_digest "$image:$moving")" == "${ref#*@}" ]]
    done
  done
fi
if [[ "$(gh release view "$tag" --json isDraft --jq '.isDraft')" == true ]]; then
  gh release edit "$tag" --draft=false --latest="$latest"
fi
echo "Release $tag conferida e publicada."
