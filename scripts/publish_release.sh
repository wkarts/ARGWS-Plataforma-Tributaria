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
    kind="$(jq -r '.object.type' <<< "$result")"
    object="$(jq -r '.object.sha' <<< "$result")"
    if [[ "$kind" == tag ]]; then
      object="$(gh api "repos/$GITHUB_REPOSITORY/git/tags/$object" --jq '.object.sha')"
    fi
    printf '%s' "$object"
  elif grep -q '404' gh-error.txt; then
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
  # Nunca deslocar as tags móveis por um workflow antigo ainda na fila.
  head_sha="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/develop" --jq '.object.sha')"
  if [[ "$head_sha" != "$GITHUB_SHA" ]]; then
    echo "Develop avançou para $head_sha; build $GITHUB_SHA não será promovido."
    exit 0
  fi

  # A release móvel fica privada durante a troca dos arquivos. Em falha, o retry
  # recomeça sobre o mesmo draft sem anunciar binários e checksums misturados.
  if release_exists pre-release; then
    gh release edit pre-release --draft=true --prerelease --latest=false
  else
    status=$?
    [[ "$status" -eq 1 ]] || exit "$status"
    gh release create pre-release --target "$GITHUB_SHA" --title "Develop $VERSION" \
      --notes "Build contínuo develop em $GITHUB_SHA" --prerelease --draft --latest=false
  fi
  gh release upload pre-release "$files"/* --clobber

  verified="$(mktemp -d)"
  trap 'rm -rf "$verified"' EXIT
  gh release download pre-release --dir "$verified"
  python3 - "$files" "$verified" <<'PY'
import pathlib
import sys

expected, actual = ({p.name for p in pathlib.Path(arg).iterdir() if p.is_file()} for arg in sys.argv[1:])
if expected != actual:
    raise SystemExit(f'Assets da pre-release faltantes ou extras: {sorted(expected ^ actual)}')
PY
  cmp "$files/SHA256SUMS.txt" "$verified/SHA256SUMS.txt"
  (cd "$verified" && sha256sum -c SHA256SUMS.txt)

  head_sha="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/develop" --jq '.object.sha')"
  if [[ "$head_sha" != "$GITHUB_SHA" ]]; then
    echo "Develop avançou para $head_sha durante a validação; build antigo fica em draft."
    exit 0
  fi
  for ref in "${image_refs[@]}"; do
    image="${ref%@*}"
    docker buildx imagetools create -t "$image:develop" "$ref"
    [[ "$(image_digest "$image:develop")" == "${ref#*@}" ]]
  done
  if ref_sha pre-release >/dev/null; then
    gh api --method PATCH "repos/$GITHUB_REPOSITORY/git/refs/tags/pre-release" \
      -f sha="$GITHUB_SHA" -F force=true >/dev/null
  else
    status=$?
    [[ "$status" -eq 1 ]] || exit "$status"
    gh api --method POST "repos/$GITHUB_REPOSITORY/git/refs" \
      -f ref=refs/tags/pre-release -f sha="$GITHUB_SHA" >/dev/null
  fi
  [[ "$(ref_sha pre-release)" == "$GITHUB_SHA" ]]
  gh release edit pre-release --title "Develop $VERSION" --notes "Build contínuo develop em $GITHUB_SHA" \
    --prerelease --draft=false --latest=false
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
    --title "Reforma Tributária $tag" --notes "Release estável do commit $GITHUB_SHA. Confira SHA256SUMS.txt e IMAGES.txt."
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
