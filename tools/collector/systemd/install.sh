#!/usr/bin/env bash
set -euo pipefail

if [[ ( $# -ne 3 && $# -ne 5 ) || $EUID -ne 0 ]]; then
  echo 'Uso (root): install.sh <binario-linux> <config.json> <arquivo-token> [<cert.pem> <key.pem>]' >&2
  exit 2
fi
source_bin=$(realpath "$1")
source_config=$(realpath "$2")
source_token=$(realpath "$3")
for item in "$source_bin" "$source_config" "$source_token"; do
  [[ -f "$item" ]] || { echo "Arquivo inexistente: $item" >&2; exit 2; }
done
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if ! id fiscal-collector >/dev/null 2>&1; then
  useradd --system --home /var/lib/fiscal-xml-collector --shell /usr/sbin/nologin fiscal-collector
fi
install -d -m 0750 -o root -g fiscal-collector /etc/fiscal-xml-collector
install -d -m 0755 /opt/fiscal-xml-collector
install -m 0755 "$source_bin" /opt/fiscal-xml-collector/fiscal-xml-collector-linux-amd64
install -m 0640 -o root -g fiscal-collector "$source_config" /etc/fiscal-xml-collector/config.json
install -m 0600 -o root -g root "$source_token" /etc/fiscal-xml-collector/agent-token
if [[ $# -eq 5 ]]; then
  source_cert=$(realpath "$4")
  source_key=$(realpath "$5")
  install -m 0640 -o root -g fiscal-collector "$source_cert" /etc/fiscal-xml-collector/cert.pem
  install -m 0600 -o fiscal-collector -g fiscal-collector "$source_key" /etc/fiscal-xml-collector/key.pem
fi
FISCAL_COLLECTOR_TOKEN_FILE=/etc/fiscal-xml-collector/agent-token \
  /opt/fiscal-xml-collector/fiscal-xml-collector-linux-amd64 check \
    --config /etc/fiscal-xml-collector/config.json \
    --expect-state-dir /var/lib/fiscal-xml-collector
runuser -u fiscal-collector -- /opt/fiscal-xml-collector/fiscal-xml-collector-linux-amd64 check \
  --config /etc/fiscal-xml-collector/config.json --no-token \
  --expect-state-dir /var/lib/fiscal-xml-collector
install -m 0644 "$script_dir/fiscal-xml-collector.service" /etc/systemd/system/fiscal-xml-collector.service
systemctl daemon-reload
systemctl enable fiscal-xml-collector.service
systemctl restart fiscal-xml-collector.service
systemctl is-active --quiet fiscal-xml-collector.service
echo 'Serviço iniciado. Configuração, token, diretórios XML e estado permanecem separados.'
