# Fiscal XML Collector para Windows

O instalador `fiscal-xml-collector-setup-windows-x64.exe` instala a interface gráfica,
um processo de coleta e o wrapper de serviço WinSW v2.12.0. Não exige Python nem
download adicional no computador cliente. Requer Windows x64 e autorização de
administrador para instalar o serviço e proteger os dados locais.

1. Verifique o SHA-256 do instalador com a linha correspondente de `SHA256SUMS.txt`.
2. Execute o instalador e abra **Fiscal XML Collector** pelo menu Iniciar.
3. Cadastre cada empresa na interface: URL HTTPS, UUID da empresa, CNPJ, diretórios
   de XML, credencial individual de ingestão e, se aplicável, certificado A1 em PEM
   para Distribuição NF-e. Salve e valide a configuração antes de ativar o serviço.
4. Use **Instalar/Iniciar serviço** na interface. A instalação cria o serviço
   `FiscalXmlCollector`, de inicialização automática, e valida os perfis localmente.
5. Confira o estado pela interface ou em `services.msc`. A pasta de dados padrão é
   `%ProgramData%\FiscalXmlCollector`; relatórios de execução do wrapper ficam em
   `logs` dentro dessa pasta.

O instalador coloca executáveis em `%ProgramFiles%\FiscalXmlCollector`. O catálogo
SQLite, perfis, fila de envio, tokens e certificados permanecem separados em
`%ProgramData%\FiscalXmlCollector`, com acesso restrito a SYSTEM e Administradores.
Uma atualização para a mesma instalação para o serviço antes de trocar binários,
preserva o banco e os segredos e reinicia somente se ele estava em execução.
Desinstalar remove o serviço e os executáveis, preservando os dados locais para
backup ou remoção manual deliberada.

O wrapper WinSW vem do release oficial v2.12.0 e é conferido por SHA-256 durante
o build e novamente ao registrar o serviço. Sua licença MIT está em
`LICENSE-WinSW.txt`. A identidade visual da janela é configurável por
`branding.json` no diretório de dados; o nome exibido do serviço é lido desse
arquivo no momento do registro. O identificador interno do serviço, metadados
do executável, atalhos e título do instalador são neutros nesta distribuição;
versões com ícone, atalhos e instalador personalizados exigem build por marca.
Para um build personalizado na infraestrutura do integrador, com o worker
frozen e o WinSW verificado já em `dist/collector`, use Python/PyInstaller e
Inno Setup 6.7.3:

```powershell
python tools/collector/windows/build_installer.py --source-root . `
  --compiler "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" --version 0.2.0 `
  --display-name "Marca do Cliente" --publisher "Empresa Cliente" `
  --icon "C:\Marca\icone.ico"
```

O comando rejeita caracteres de diretiva/atalho, verifica o SHA-256 do WinSW,
recompila a GUI com metadados e ícone próprios e gera o setup. Instale junto
um `branding.json` compatível em `%ProgramData%\FiscalXmlCollector` para a GUI
e o nome exibido do serviço. O serviço interno `FiscalXmlCollector`, o AppId,
os caminhos neutros em Program Files/ProgramData e os nomes dos executáveis
são estáveis entre builds para permitir atualização no mesmo computador;
instalações de marcas diferentes no mesmo computador exigem isolamento
adicional de estado e serviço.

O instalador é construído com Inno Setup 6.7.3. Antes de distribuí-lo
comercialmente, confira as condições do fabricante e regularize a licença
comercial aplicável: https://jrsoftware.org/isdl.php e
https://jrsoftware.org/files/is/license.txt. Mantenha os avisos exigidos pela
licença e não atribua a autoria do Inno Setup ao produto.

O coletor envia XML por HTTPS e mantém os originais. A consulta oficial nesta
versão cobre Distribuição DF-e de NF-e com certificado e autorização adequados;
apuração assistida e outras fontes fiscais exigem configuração e homologação.
