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
assert 'tools/collector/systemd/README-self.md release-files/README-XML-Collector-Linux.md' in workflow
assert 'tools/collector/windows/README-installer.md release-files/README-XML-Collector-Windows.md' in workflow
assert 'docs/CONTRATOS-OFICIAIS-2026.md release-files/CONTRATOS-OFICIAIS-2026.md' in workflow
for name in ('fiscal-xml-collector-gui-linux-x64.tar.gz', 'fiscal-xml-collector-setup-windows-x64.exe'):
    assert f'cp release-collector-' in workflow and f'/{name} release-files/' in workflow
    assert name in collector
assert 'Fiscal-Self-Deploy-$label.zip' in workflow
assert 'release-deployer/' in workflow
assert 'cp release-deployer/Fiscal-Deployer-GUI-Windows-x64.zip release-files/' in workflow
assert 'SHA256SUMS-deployer.txt' in workflow
assert 'Fiscal-Deploy-Matrix-$label.zip' in workflow
assert 'scripts/sync_deploy_env.py' in workflow
assert "if: needs.source.outputs.channel == 'main'" in workflow
assert 'group: fiscal-publish-${{ github.ref }}-${{ github.sha }}' in workflow
assert 'cancel-in-progress: false' in workflow
assert 'v${VERSION}-develop.${GITHUB_SHA::12}' in publisher
assert 'gh release create "$preview_tag" --verify-tag' in publisher
assert 'refs/tags/$preview_tag' in publisher
assert publisher.index('refs/tags/$preview_tag') < publisher.index('gh release create "$preview_tag" --verify-tag')
assert 'gh release edit "$preview_tag" --prerelease --draft=false' in publisher
assert 'gh release download "$preview_tag" --dir "$verified"' in publisher
assert 'gh release upload pre-release "$files"/* --clobber' in publisher
assert publisher.index('gh release edit "$preview_tag" --prerelease --draft=false') < publisher.index('gh release upload pre-release "$files"/* --clobber')
assert 'python3 scripts/build_deploy_matrix.py --output' in workflow
assert 'cp release-collector-linux/fiscal-xml-collector-linux-amd64' not in workflow
assert 'cp release-collector-windows/fiscal-xml-collector-windows-amd64.exe' not in workflow
for name in ('fiscal-xml-gui-linux-x64', 'fiscal-xml-worker-linux-x64',
             'fiscal-xml-gui-windows-x64', 'fiscal-xml-worker-windows-x64',
             'self-install.sh', 'fiscal-xml-self.service', 'manage-service.ps1'):
    assert name in collector or name in (ROOT / 'tools/collector/windows/installer.iss').read_text(encoding='utf-8')
assert (ROOT / 'tools/collector/windows/installer.iss').is_file()
assert collector.count('self-test --offline') >= 4
assert collector.count('--add-data') == 4 and collector.count('VERSION') >= 4
assert '--windowed --uac-admin' in collector
assert '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da' in collector
assert '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732' in collector
assert 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe' in collector
assert 'python3 scripts/verify_collector_assets.py --single linux' in collector
assert 'scripts/verify_collector_assets.py --single windows' in collector
assert workflow.index('verify_collector_assets.py') < workflow.index('sha256sum -- * > SHA256SUMS.txt') < workflow.index('bash scripts/publish_release.sh release-files')
assert publisher.index('gh release download "$tag" --dir "$verified"') < publisher.index('promote "$image:$VERSION" "$ref"')
assert publisher.index('promote "$image:$tag" "$ref"') < publisher.index('gh release edit "$tag" --draft=false')
for invocation in ('& $cli version', '& $cli list', '& $cli generate', '& $cli validate', '--smoke-test'):
    assert invocation in windows
print('OK: manifesto, retenção, coletor, labels e supressão de segredo em logs')
