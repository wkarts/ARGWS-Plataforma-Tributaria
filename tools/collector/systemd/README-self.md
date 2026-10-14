# Fiscal XML Collector para Linux x64

O pacote `fiscal-xml-collector-gui-linux-x64.tar.gz` contém a GUI, o processo
de coleta multiperfil e scripts de instalação systemd. Não exige Python na
máquina cliente. Verifique o SHA-256 do tar.gz antes de extrair.

1. Extraia o pacote e execute `./fiscal-xml-gui-linux-x64` com um diretório de
   dados privado: `--home /var/lib/fiscal-xml-collector`. Para criar os perfis
   nesse caminho é necessária uma conta administrativa; confira também as
   permissões de leitura dos diretórios XML e certificados da empresa.
2. Cadastre ao menos uma empresa e valide sua configuração na GUI.
3. Execute como root:

   `bash self-install.sh ./fiscal-xml-worker-linux-x64 ./fiscal-xml-gui-linux-x64 /var/lib/fiscal-xml-collector`

4. Confira `systemctl status fiscal-xml-self.service` e os logs com
   `journalctl -u fiscal-xml-self.service`.

O serviço roda sob a conta `fiscal-collector` e mantém catálogo SQLite,
configurações, segredos, fila de documentos e XMLs no diretório de dados com
acesso restrito. `self-install.sh` substitui os binários em atualizações,
mantém os dados e reinicia a unidade. `self-uninstall.sh` remove só a unidade,
preservando os dados e os binários para revisão.

A marca da interface é configurável por `branding.json` no diretório de dados.
O nome da unidade e dos binários desta distribuição é neutro. A consulta
oficial disponível nesta versão é Distribuição DF-e de NF-e, condicionada à
credencial e autorização adequadas; apuração assistida requer homologação
das demais fontes e etapas.
