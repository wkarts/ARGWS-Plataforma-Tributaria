"""Read verified public release choices for the local deployment tool.

The repository is fixed, never an operator-supplied HTTP endpoint. Production
chooses the newest *published stable SemVer*, then pins that exact tag in .env.
"""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SOURCE_REPOSITORY = "ARGWS-Plataforma-Tributaria"
OWNER = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,37}[a-z0-9])?\Z")
STABLE = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
PREVIEW = re.compile(
    r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)-develop\.([0-9a-f]{12})\Z"
)
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_RESPONSE_BYTES = 3_000_000


class ReleaseCatalogError(Exception):
    pass


def published_versions(rows: list[dict], channel: str) -> dict:
    """Return only completed releases with usable immutable image references."""
    if channel not in {"production", "develop"}:
        raise ReleaseCatalogError("Canal inválido para seleção de versões")
    stable: dict[str, str] = {}
    previews: dict[str, tuple[str, str]] = {}
    for item in rows:
        if not isinstance(item, dict) or item.get("draft") is not False:
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or not tag:
            continue
        assets = item.get("assets")
        assets = assets if isinstance(assets, list) else []
        names = {asset.get("name") for asset in assets if isinstance(asset, dict)}
        if not {"IMAGES.txt", "SHA256SUMS.txt"}.issubset(names):
            continue
        if item.get("prerelease") is False:
            match = STABLE.fullmatch(tag)
            if match:
                stable["v" + ".".join(match.groups())] = str(item.get("published_at") or "")
        elif item.get("prerelease") is True:
            match = PREVIEW.fullmatch(tag)
            if match:
                key = "sha-" + match.group(1)
                previews[key] = (tag, str(item.get("published_at") or ""))
    stable_tags = sorted(
        stable, key=lambda tag: tuple(int(n) for n in tag.removeprefix("v").split(".")),
        reverse=True,
    )
    if channel == "production":
        return {"channel": channel, "latest": stable_tags[0] if stable_tags else None,
                "versions": stable_tags,
                "labels": {tag: f"{tag} · estável" for tag in stable_tags}}
    previews_sorted = sorted(previews, key=lambda tag: previews[tag][1], reverse=True)
    versions = ["develop", *previews_sorted, *stable_tags]
    return {"channel": channel, "latest": "develop", "versions": versions,
            "labels": {
                "develop": "develop · atualização do canal",
                **{tag: f"{previews[tag][0]} · commit fixo" for tag in previews_sorted},
                **{tag: f"{tag} · estável" for tag in stable_tags},
            }}


def load_catalog(owner: str, channel: str) -> dict:
    """Paginate public GitHub Releases over HTTPS, without API tokens or URL input."""
    if not OWNER.fullmatch(owner):
        raise ReleaseCatalogError("Namespace GHCR inválido")
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = (f"https://api.github.com/repos/{owner}/{SOURCE_REPOSITORY}"
               f"/releases?per_page={PAGE_SIZE}&page={page}")
        req = Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "fiscal-deployer-version-selector",
        })
        try:
            with urlopen(req, timeout=8) as response:
                blob = response.read(MAX_RESPONSE_BYTES + 1)
            if len(blob) > MAX_RESPONSE_BYTES:
                raise ReleaseCatalogError("Catálogo de versões excedeu o limite de segurança")
            page_rows = json.loads(blob)
        except HTTPError as exc:
            raise ReleaseCatalogError(
                f"O GitHub retornou HTTP {exc.code} ao consultar versões. "
                "Verifique acesso HTTPS, permissões do repositório e limite da API."
            ) from None
        except (URLError, OSError, ValueError) as exc:
            raise ReleaseCatalogError(
                "Não foi possível consultar as versões publicadas. "
                "Verifique a conexão ou escolha uma tag estável manualmente."
            ) from exc
        if not isinstance(page_rows, list):
            raise ReleaseCatalogError("A resposta do GitHub não é um catálogo de releases")
        rows.extend(page_rows)
        if len(page_rows) < PAGE_SIZE:
            break
    catalog = published_versions(rows, channel)
    return {**catalog, "owner": owner, "source": "github_releases",
            "count": len(catalog["versions"])}
