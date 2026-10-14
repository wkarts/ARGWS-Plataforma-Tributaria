param(
  [Parameter(Mandatory=$true)][string]$WinSWPath,
  [Parameter(Mandatory=$true)][string]$CollectorPath,
  [Parameter(Mandatory=$true)][string]$ConfigPath,
  [Parameter(Mandatory=$true)][string]$TokenFile,
  [string]$InstallDir = "$env:ProgramFiles\FiscalXmlCollector",
  [string]$ServiceId = "FiscalXmlCollector"
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -Path $WinSWPath -PathType Leaf)) { throw 'WinSW não encontrado.' }
if (-not (Test-Path -Path $CollectorPath -PathType Leaf)) { throw 'Binário do coletor não encontrado.' }
if (-not (Test-Path -Path $ConfigPath -PathType Leaf)) { throw 'Configuração não encontrada.' }
if (-not (Test-Path -Path $TokenFile -PathType Leaf)) { throw 'Arquivo do token não encontrado.' }
$config = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
if ($config.token -or $config.password) { throw 'Não armazene token no JSON de configuração.' }
$env:FISCAL_COLLECTOR_TOKEN_FILE = (Resolve-Path $TokenFile).Path
& $CollectorPath check --config $ConfigPath
if ($LASTEXITCODE -ne 0) { throw 'Falha na validação do coletor.' }
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$wrapper = Join-Path $InstallDir "$ServiceId.exe"
$xmlPath = Join-Path $InstallDir "$ServiceId.xml"
if (Test-Path $xmlPath) { throw 'Serviço já instalado. Remova antes de reinstalar.' }
function Set-AdminOnlyAcl([string]$Path, [bool]$IsDirectory) {
  $acl = Get-Acl -LiteralPath $Path
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($rule in @($acl.Access)) { $acl.RemoveAccessRuleAll($rule) | Out-Null }
  foreach ($sidValue in @('S-1-5-18', 'S-1-5-32-544')) {
    $sid = [System.Security.Principal.SecurityIdentifier]::new($sidValue)
    if ($IsDirectory) {
      $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid, [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit',
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow)
    } else {
      $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid, [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow)
    }
    $acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
}
Set-AdminOnlyAcl $InstallDir $true
$statePath = [string]$config.state_dir
if (-not [System.IO.Path]::IsPathRooted($statePath)) { throw 'state_dir deve ser absoluto.' }
New-Item -ItemType Directory -Path $statePath -Force | Out-Null
Set-AdminOnlyAcl $statePath $true
Get-ChildItem -LiteralPath $statePath -Force -Recurse | ForEach-Object {
  if ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw 'state_dir não pode conter symlinks/junctions.'
  }
  Set-AdminOnlyAcl $_.FullName $_.PSIsContainer
}
$installedCollector = Join-Path $InstallDir 'fiscal-xml-collector-windows-amd64.exe'
$installedConfig = Join-Path $InstallDir 'config.json'
$installedToken = Join-Path $InstallDir 'agent-token'
Copy-Item -LiteralPath $WinSWPath -Destination $wrapper -ErrorAction Stop
Copy-Item -LiteralPath $CollectorPath -Destination $installedCollector -ErrorAction Stop
Copy-Item -LiteralPath $ConfigPath -Destination $installedConfig -ErrorAction Stop
Copy-Item -LiteralPath $TokenFile -Destination $installedToken -ErrorAction Stop
$installedPemFiles = @()
if ($config.sefaz_nfe) {
  $certSource = [string]$config.sefaz_nfe.cert_pem
  $keySource = [string]$config.sefaz_nfe.key_pem
  if (-not (Test-Path -LiteralPath $certSource -PathType Leaf) -or
      -not (Test-Path -LiteralPath $keySource -PathType Leaf)) {
    throw 'Certificado ou chave PEM da SEFAZ não encontrado.'
  }
  $installedCert = Join-Path $InstallDir 'sefaz-cert.pem'
  $installedKey = Join-Path $InstallDir 'sefaz-key.pem'
  Copy-Item -LiteralPath $certSource -Destination $installedCert -ErrorAction Stop
  Copy-Item -LiteralPath $keySource -Destination $installedKey -ErrorAction Stop
  $config.sefaz_nfe.cert_pem = $installedCert
  $config.sefaz_nfe.key_pem = $installedKey
  $json = $config | ConvertTo-Json -Depth 20
  [System.IO.File]::WriteAllText($installedConfig, $json,
    [System.Text.UTF8Encoding]::new($false))
  $installedPemFiles = @($installedCert, $installedKey)
}
$xml = New-Object System.Xml.XmlDocument
$service = $xml.CreateElement('service')
$xml.AppendChild($service) | Out-Null
$values = [ordered]@{
  id = $ServiceId; name = 'Coletor local de XML fiscal';
  description = 'Envio seguro de XML fiscal assinado para a aplicação configurada pelo cliente';
  executable = $installedCollector;
  arguments = ('serve --config "{0}"' -f $installedConfig);
  startmode = 'Automatic';
  workingdirectory = $InstallDir
}
foreach ($entry in $values.GetEnumerator()) {
  $node = $xml.CreateElement($entry.Key)
  $node.InnerText = [string]$entry.Value
  $service.AppendChild($node) | Out-Null
}
$environment = $xml.CreateElement('env')
$environment.SetAttribute('name', 'FISCAL_COLLECTOR_TOKEN_FILE')
$environment.SetAttribute('value', $installedToken)
$service.AppendChild($environment) | Out-Null
$log = $xml.CreateElement('log')
$log.SetAttribute('mode', 'roll')
$service.AppendChild($log) | Out-Null
$failure = $xml.CreateElement('onfailure')
$failure.SetAttribute('action', 'restart')
$failure.SetAttribute('delay', '30 sec')
$service.AppendChild($failure) | Out-Null
$xml.Save($xmlPath)
foreach ($path in (@($wrapper, $installedCollector, $installedConfig, $installedToken, $xmlPath) + $installedPemFiles)) {
  Set-AdminOnlyAcl $path $false
}
$env:FISCAL_COLLECTOR_TOKEN_FILE = $installedToken
& $installedCollector check --config $installedConfig
if ($LASTEXITCODE -ne 0) { throw 'Falha na validação dos arquivos instalados.' }
& $wrapper install
if ($LASTEXITCODE -ne 0) { throw "WinSW falhou ao instalar o serviço ($LASTEXITCODE)." }
& $wrapper start
if ($LASTEXITCODE -ne 0) { throw "WinSW falhou ao iniciar o serviço ($LASTEXITCODE)." }
Write-Host "Serviço $ServiceId instalado. Limite a leitura do token à conta do serviço e aos administradores."
