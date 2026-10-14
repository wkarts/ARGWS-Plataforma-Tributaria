#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Execute como root.' >&2
  exit 2
fi
systemctl disable --now fiscal-xml-self.service 2>/dev/null || true
rm -f /etc/systemd/system/fiscal-xml-self.service
systemctl daemon-reload
echo 'Serviço removido. Binários, configuração, tokens, XMLs e SQLite foram preservados.'
