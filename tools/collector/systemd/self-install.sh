#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || $EUID -ne 0 ]]; then
  echo 'Uso (root): self-install.sh <worker-linux> <gui-linux> <home-absoluto>' >&2
  exit 2
fi
[[ ! -L "$1" && ! -L "$2" ]] || { echo 'Binário de entrada não pode ser link.' >&2; exit 2; }
worker=$(realpath -- "$1")
gui=$(realpath -- "$2")
[[ "$3" == /var/lib/fiscal-xml-collector ]] || {
  echo 'O serviço só aceita o diretório dedicado /var/lib/fiscal-xml-collector.' >&2
  exit 2
}
home_path=/var/lib/fiscal-xml-collector
for parent in /var /var/lib "$home_path"; do
  [[ ! -L "$parent" ]] || { echo "Diretório de estado não pode ser link: $parent" >&2; exit 2; }
done
for file in "$worker" "$gui"; do
  [[ -f "$file" && ! -L "$file" ]] || { echo "Binário ausente ou link: $file" >&2; exit 2; }
done
[[ "$home_path" =~ ^/[A-Za-z0-9_./-]+$ && "$home_path" != / ]] || {
  echo 'O diretório de estado deve ser absoluto, sem espaços ou caracteres especiais.' >&2
  exit 2
}
[[ -d "$home_path/companies" ]] || { echo 'Cadastre uma empresa na GUI antes de instalar o serviço.' >&2; exit 2; }
find "$home_path/companies" -mindepth 2 -maxdepth 2 -name config.json -type f -print -quit | grep -q . || {
  echo 'Nenhum perfil de empresa válido foi encontrado.' >&2
  exit 2
}
if find "$home_path" -type l -print -quit | grep -q .; then
  echo 'O diretório privado não pode conter links simbólicos.' >&2
  exit 2
fi
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
unit_template="$source_dir/fiscal-xml-self.service"
[[ -f "$unit_template" ]] || { echo 'Template systemd ausente.' >&2; exit 2; }

if systemctl is-active --quiet fiscal-xml-self.service; then
  systemctl stop fiscal-xml-self.service
fi
if ! id fiscal-collector >/dev/null 2>&1; then
  useradd --system --home "$home_path" --shell /usr/sbin/nologin fiscal-collector
fi
install -d -m 0755 /opt/fiscal-xml-collector
install -m 0755 "$worker" /opt/fiscal-xml-collector/fiscal-xml-worker-linux-x64
install -m 0755 "$gui" /opt/fiscal-xml-collector/fiscal-xml-gui-linux-x64
install -d -m 0700 -o fiscal-collector -g fiscal-collector "$home_path"
chown -R -- fiscal-collector:fiscal-collector "$home_path"
chmod -R go-rwx -- "$home_path"
runuser -u fiscal-collector -- /opt/fiscal-xml-collector/fiscal-xml-worker-linux-x64 --home "$home_path" check
sed "s|@HOME@|$home_path|g" "$unit_template" > /etc/systemd/system/fiscal-xml-self.service
chmod 0644 /etc/systemd/system/fiscal-xml-self.service
systemctl daemon-reload
systemctl enable --now fiscal-xml-self.service
systemctl is-active --quiet fiscal-xml-self.service
echo "Serviço fiscal-xml-self instalado; SQLite, tokens e XML preservados em $home_path."
