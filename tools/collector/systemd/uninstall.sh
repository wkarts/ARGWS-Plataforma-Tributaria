#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Execute como root.' >&2
  exit 2
fi
systemctl disable --now fiscal-xml-collector.service 2>/dev/null || true
rm -f /etc/systemd/system/fiscal-xml-collector.service
systemctl daemon-reload
echo 'Serviço removido. Binário, configuração, token, XMLs e estado foram preservados.'
