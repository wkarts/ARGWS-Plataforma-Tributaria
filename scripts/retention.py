#!/usr/bin/env python3
"""Inventário conservador; exclusão só manual com confirmação e allowlist por ID."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLICY = json.loads((ROOT / ".github/retention-policy.json").read_text(encoding="utf-8"))
NOW = dt.datetime.now(dt.timezone.utc)


def old_enough(value: str | None, days: int) -> bool:
    if not value:
        return False
    try:
        return NOW - dt.datetime.fromisoformat(value.replace("Z", "+00:00")) >= dt.timedelta(days=days)
    except ValueError:
        return False


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def request(self, path: str, method: str = "GET") -> tuple[object, str]:
        req = urllib.request.Request(
            "https://api.github.com" + path,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "reforma-tributaria-retention",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read(10 * 1024 * 1024)
            data = json.loads(payload) if payload else {}
            return data, response.headers.get("Link", "")

    def pages(self, path: str) -> list[object]:
        result: list[object] = []
        for page in range(1, 101):
            separator = "&" if "?" in path else "?"
            data, link = self.request(f"{path}{separator}per_page=100&page={page}")
            if isinstance(data, dict):
                items = data.get("actions_caches")
            else:
                items = data
            if not isinstance(items, list):
                raise ValueError("Resposta paginada inesperada da API GitHub")
            result.extend(items)
            if 'rel="next"' not in link:
                return result
        raise ValueError("Inventário incompleto após 100 páginas; nenhuma exclusão segura")


def plan(github: GitHub, repository: str, owner_type: str) -> dict:
    owner = repository.split("/", 1)[0]
    if owner.lower() != "wkarts" or repository.count("/") != 1:
        raise ValueError("Repositório fora do namespace permitido")
    if owner_type not in {"user", "org"}:
        raise ValueError("GITHUB_OWNER_TYPE deve ser user ou org")
    report: dict = {"schema": 1, "mode": "dry-run", "repository": repository, "caches": [], "ghcr": [], "errors": []}
    caches = github.pages(f"/repos/{repository}/actions/caches")
    for entry in caches:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key", "")
        if not isinstance(key, str) or not any(key.startswith(prefix) for prefix in POLICY["cache_key_prefixes"]):
            continue
        if old_enough(entry.get("last_accessed_at"), POLICY["cache_age_days"]):
            report["caches"].append({"id": entry["id"], "key": key, "last_accessed_at": entry["last_accessed_at"]})
    allowed = set(map(int, POLICY["allowed_untagged_version_ids"]))
    for package in POLICY["image_packages"]:
        if package in POLICY["protected_packages"]:
            raise ValueError("Pacote de imagem-base na lista de exclusão")
        encoded = urllib.parse.quote(package, safe="")
        path = f"/{'orgs' if owner_type == 'org' else 'users'}/{owner}/packages/container/{encoded}/versions"
        try:
            versions = github.pages(path)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                report["errors"].append(f"Pacote {package} ausente ou sem permissão de leitura")
                continue
            raise
        for item in versions:
            if not isinstance(item, dict):
                continue
            version_id = item.get("id")
            tags = item.get("metadata", {}).get("container", {}).get("tags") or []
            if not isinstance(version_id, int) or tags or not old_enough(item.get("created_at"), POLICY["ghcr_age_days"]):
                continue
            report["ghcr"].append({"package": package, "id": version_id, "eligible": version_id in allowed, "created_at": item["created_at"]})
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="retention.json")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repository:
        parser.error("GH_TOKEN e GITHUB_REPOSITORY são necessários")
    if args.apply and args.confirm != "APPLY_SAFE_RETENTION":
        parser.error("--apply exige --confirm APPLY_SAFE_RETENTION")
    github = GitHub(token)
    report = plan(github, repository, os.environ.get("GITHUB_OWNER_TYPE", "user"))
    report["mode"] = "apply" if args.apply else "dry-run"
    report["deleted"] = []
    if args.apply:
        remaining = POLICY["max_deletions_per_run"]
        for item in report["caches"]:
            if remaining <= 0:
                break
            github.request(f"/repos/{repository}/actions/caches/{item['id']}", "DELETE")
            report["deleted"].append({"type": "actions-cache", "id": item["id"]})
            remaining -= 1
        owner = repository.split("/", 1)[0]
        kind = "orgs" if os.environ.get("GITHUB_OWNER_TYPE", "user") == "org" else "users"
        for item in report["ghcr"]:
            if remaining <= 0:
                break
            if not item["eligible"]:
                continue
            package = urllib.parse.quote(item["package"], safe="")
            # Revalidar tags imediatamente antes do DELETE; tags que surgiram desde o plano são protegidas.
            details, _ = github.request(f"/{kind}/{owner}/packages/container/{package}/versions/{item['id']}")
            if not isinstance(details, dict) or details.get("metadata", {}).get("container", {}).get("tags"):
                continue
            github.request(f"/{kind}/{owner}/packages/container/{package}/versions/{item['id']}", "DELETE")
            report["deleted"].append({"type": "ghcr-version", "package": item["package"], "id": item["id"]})
            remaining -= 1
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"GHCR sem tag: {len(report['ghcr'])}; allowlisted: {sum(1 for x in report['ghcr'] if x['eligible'])}; caches: {len(report['caches'])}; excluídos: {len(report['deleted'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
