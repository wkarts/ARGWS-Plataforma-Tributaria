#!/usr/bin/env python3
"""Gates estáticos de distribuição, nomes GHCR e segurança do webhook."""
from __future__ import annotations

import json
import re
from pathlib import Path

from version import read_version

ROOT = Path(__file__).resolve().parent.parent
read_version()
bases = json.loads((ROOT / '.github/base-images.json').read_text(encoding='utf-8'))
retention = json.loads((ROOT / '.github/retention-policy.json').read_text(encoding='utf-8'))
items = bases['bases']
assert bases['owner'] == 'wkarts'
assert len(items) == len({item['package'] for item in items})
assert len(items) == len({item['tag'] for item in items})
assert all(item['package'].startswith('reforma-tributaria-base-') for item in items)
assert all((ROOT / item['dockerfile']).is_file() for item in items)
assert set(retention['protected_packages']) == {item['package'] for item in items}
assert set(retention['image_packages']) == {'reforma-tributaria-api', 'reforma-tributaria-web'}
assert not set(retention['protected_packages']) & set(retention['image_packages'])
allowed = retention['allowed_untagged_version_ids']
assert isinstance(allowed, list) and len(allowed) == len(set(allowed))
assert all(isinstance(item, int) and item > 0 for item in allowed)
compose = (ROOT / 'deploy/compose.yaml').read_text(encoding='utf-8')
for item in items:
    if item['component'] in ('python', 'node', 'nginx'):
        dockerfile = ROOT / ('infra/docker/api.Dockerfile' if item['component'] == 'python' else 'infra/docker/web.Dockerfile')
        assert f"{item['package']}:{item['tag']}" in dockerfile.read_text(encoding='utf-8')
    else:
        assert f"{item['package']}:{item['tag']}" in compose
nginx = (ROOT / 'infra/docker/nginx.conf').read_text(encoding='utf-8')
assert re.search(r'location \^~ /api/v1/webhooks/cbs/\s*\{\s*access_log off;', nginx)
assert '--no-access-log' in compose
assert 'minio/minio:' not in compose and 'MINIO_' not in compose
publisher = (ROOT / 'scripts/publish_release.sh').read_text(encoding='utf-8')
workflow = (ROOT / '.github/workflows/ghcr-publish.yml').read_text(encoding='utf-8')
windows = (ROOT / '.github/workflows/deployer-windows.yml').read_text(encoding='utf-8')
collector = (ROOT / '.github/workflows/collector-binaries.yml').read_text(encoding='utf-8')
assert workflow.index('tags: ${{ steps.sha.outputs.image }}:${{ steps.sha.outputs.tag }}') < workflow.index('needs: [source, stage, images, deployer, collector]')
assert 'needs: stage' in workflow and 'staging_tag: ${{ needs.stage.outputs.tag }}' in workflow
assert 'gh release download "$STAGING_TAG"' in workflow
assert 'gh release delete "$STAGING_TAG" --yes --cleanup-tag' in workflow
assert 'actions/download-artifact@' not in workflow
assert 'actions/upload-artifact@' not in collector + windows
assert 'gh release upload $env:STAGING_TAG' in collector + windows
assert 'steps.sha.outputs.tag' in workflow and 'tag=sha-${GITHUB_SHA::12}' in workflow
assert 'bash scripts/publish_release.sh release-files' in workflow
assert 'uses: ./.github/workflows/collector-binaries.yml' in workflow
assert 'python3 scripts/verify_collector_assets.py release-collector-linux release-collector-windows' in workflow
assert 'docs/COLETOR-LOCAL-OPERACAO.md release-files/OPERACAO-XML-Collector.md' in workflow
assert 'docs/CONTRATOS-OFICIAIS-2026.md release-files/CONTRATOS-OFICIAIS-2026.md' in workflow
for name in ('fiscal-xml-collector-linux-amd64', 'fiscal-xml-collector-windows-amd64.exe'):
    assert f'cp release-collector-' in workflow and f'/{name} release-files/' in workflow
    assert name in collector
for name in ('fiscal-xml-collector.service', 'install.sh', 'uninstall.sh',
             'install-winsw.ps1', 'uninstall-winsw.ps1'):
    assert f'tools/collector/' in workflow and name in workflow
assert collector.count('self-test --offline') >= 4
assert collector.count('--add-data') == 2 and collector.count('VERSION') >= 3
assert workflow.index('verify_collector_assets.py') < workflow.index('sha256sum -- * > SHA256SUMS.txt') < workflow.index('bash scripts/publish_release.sh release-files')
assert publisher.index('gh release download "$tag" --dir "$verified"') < publisher.index('promote "$image:$VERSION" "$ref"')
assert publisher.index('promote "$image:$tag" "$ref"') < publisher.index('gh release edit "$tag" --draft=false')
for invocation in ('& $cli version', '& $cli list', '& $cli generate', '& $cli validate', '--smoke-test'):
    assert invocation in windows
print('OK: manifesto, retenção, coletor, labels e supressão de segredo em logs')
