param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('Install','Start','Stop','Status','Uninstall','Protect')]
  [string]$Action,
  [Alias('Home')][string]$DataHome = (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'FiscalXmlCollector'),
  [string]$InstallDir = ''
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($InstallDir)) {
  $scriptFile = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
  if ([string]::IsNullOrWhiteSpace($scriptFile)) { throw 'Diretório do script de serviço não encontrado.' }
  $InstallDir = $scriptFile
}
if (-not [IO.Path]::IsPathRooted($InstallDir)) { throw 'InstallDir deve ser absoluto.' }
$serviceId = 'FiscalXmlCollector'
$wrapper = Join-Path $InstallDir "$serviceId.exe"
$serviceXml = Join-Path $InstallDir "$serviceId.xml"
$worker = Join-Path $InstallDir 'fiscal-xml-worker-windows-x64.exe'
# WinSW v2.12.0 x64, official immutable release asset; checked before packaging and use.
$winSwHash = '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da'

function Assert-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Execute a operação como administrador.'
  }
}

function Assert-Home {
  if (-not [IO.Path]::IsPathRooted($DataHome)) { throw 'Home deve ser um caminho absoluto.' }
  if ($DataHome -match '"|[\r\n]') { throw 'Home contém caracteres inválidos.' }
  # A ação Protect reescreve ACLs recursivamente: jamais aceite uma árvore
  # selecionada pelo usuário, um diretório pai ou um caminho de sistema.
  $systemData = [Environment]::GetFolderPath('CommonApplicationData')
  $expected = [IO.Path]::GetFullPath((Join-Path $systemData 'FiscalXmlCollector')).TrimEnd('\','/')
  $actual = [IO.Path]::GetFullPath($DataHome).TrimEnd('\','/')
  if ($actual -ine $expected) { throw 'O serviço só aceita o diretório dedicado em ProgramData\FiscalXmlCollector.' }
  $programDataItem = Get-Item -LiteralPath $systemData -Force
  if ($programDataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'ProgramData não pode ser um link ou junction.'
  }
  if (Test-Path -LiteralPath $DataHome) {
    $item = Get-Item -LiteralPath $DataHome -Force
    if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw 'Home não pode ser arquivo, link ou junction.'
    }
  }
}

function Set-PrivateAcl([string]$Path, [bool]$Directory) {
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Link ou junction dentro do estado privado: $Path"
  }
  $acl = Get-Acl -LiteralPath $Path
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRuleAll($rule) }
  foreach ($sidString in @('S-1-5-18', 'S-1-5-32-544')) {
    $sid = [Security.Principal.SecurityIdentifier]::new($sidString)
    $inherit = if ($Directory) {
      [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    } else { [Security.AccessControl.InheritanceFlags]::None }
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
      $sid, [Security.AccessControl.FileSystemRights]::FullControl, $inherit,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
}

function Protect-Home {
  Assert-Administrator
  Assert-Home
  New-Item -ItemType Directory -Path $DataHome -Force | Out-Null
  # Preserve the existing SQLite, profiles and XML spool on upgrades; limit access in place.
  # Faça a verificação de links antes de alterar qualquer ACL do estado.
  $items = @(Get-ChildItem -LiteralPath $DataHome -Force -Recurse -ErrorAction Stop)
  foreach ($item in $items) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
      throw "Link ou junction dentro do estado privado: $($item.FullName)"
    }
  }
  Set-PrivateAcl $DataHome $true
  $items | ForEach-Object {
    Set-PrivateAcl $_.FullName $_.PSIsContainer
  }
}

function Get-InstalledService {
  Get-Service -Name $serviceId -ErrorAction SilentlyContinue
}

function Invoke-Wrapper([string]$Command) {
  if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) { throw "Wrapper ausente: $wrapper" }
  & $wrapper $Command
  if ($LASTEXITCODE -ne 0) { throw "WinSW $Command falhou ($LASTEXITCODE)." }
}

Assert-Home
if ($Action -eq 'Status') {
  $service = Get-InstalledService
  [pscustomobject]@{ installed = ($null -ne $service); status = $(if ($service) { "$($service.Status)" } else { 'NotInstalled' }); service_id = $serviceId; home = $DataHome } |
    ConvertTo-Json -Compress
  exit 0
}

Assert-Administrator
switch ($Action) {
  'Protect' {
    Protect-Home
  }
  'Install' {
    if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) { throw "Worker ausente: $worker" }
    if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) { throw "WinSW ausente: $wrapper" }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $wrapper).Hash.ToLowerInvariant()
    if ($actualHash -ne $winSwHash) { throw 'Hash do WinSW diverge da versão verificada.' }
    Protect-Home
    $profiles = @(Get-ChildItem -LiteralPath (Join-Path $DataHome 'companies') -Directory -ErrorAction SilentlyContinue |
      Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'config.json') -PathType Leaf })
    if ($profiles.Count -eq 0) { throw 'Cadastre ao menos uma empresa na interface antes de instalar o serviço.' }
    & $worker --home $DataHome check
    if ($LASTEXITCODE -ne 0) { throw 'Validação offline do worker falhou; serviço não instalado.' }
    $displayName = 'Fiscal XML Collector'
    $brandFile = Join-Path $DataHome 'branding.json'
    if (Test-Path -LiteralPath $brandFile -PathType Leaf) {
      if ((Get-Item -LiteralPath $brandFile -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Arquivo de marca não pode ser link.'
      }
      $brand = Get-Content -LiteralPath $brandFile -Raw | ConvertFrom-Json
      $candidate = [string]$brand.display_name
      if ($candidate.Length -gt 0 -and $candidate.Length -le 80 -and $candidate -notmatch '[\r\n\x00-\x1f]') {
        $displayName = $candidate.Trim()
      }
    }
    $xml = [xml]'<service></service>'
    $entries = [ordered]@{
      id = $serviceId
      name = $displayName
      description = 'Coleta fiscal local por empresas configuradas neste computador'
      executable = $worker
      arguments = ('--home "{0}" serve' -f $DataHome)
      workingdirectory = $InstallDir
      startmode = 'Automatic'
      logpath = (Join-Path $DataHome 'logs')
    }
    New-Item -ItemType Directory -Path $entries.logpath -Force | Out-Null
    Set-PrivateAcl $entries.logpath $true
    foreach ($entry in $entries.GetEnumerator()) {
      $node = $xml.CreateElement($entry.Key)
      $node.InnerText = [string]$entry.Value
      [void]$xml.DocumentElement.AppendChild($node)
    }
    $log = $xml.CreateElement('log')
    $log.SetAttribute('mode', 'roll')
    [void]$xml.DocumentElement.AppendChild($log)
    $failure = $xml.CreateElement('onfailure')
    $failure.SetAttribute('action', 'restart')
    $failure.SetAttribute('delay', '30 sec')
    [void]$xml.DocumentElement.AppendChild($failure)
    $xml.Save($serviceXml)
    if (-not (Get-InstalledService)) { Invoke-Wrapper 'install' }
    if ((Get-InstalledService).Status -ne 'Running') { Invoke-Wrapper 'start' }
  }
  'Start' {
    if (-not (Get-InstalledService)) { throw 'Serviço não instalado.' }
    if ((Get-InstalledService).Status -ne 'Running') { Invoke-Wrapper 'start' }
  }
  'Stop' {
    $service = Get-InstalledService
    if ($service -and $service.Status -ne 'Stopped') {
      Invoke-Wrapper 'stop'
      (Get-Service -Name $serviceId).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(45))
    }
  }
  'Uninstall' {
    $service = Get-InstalledService
    if ($service) {
      if ($service.Status -ne 'Stopped') {
        Invoke-Wrapper 'stop'
        (Get-Service -Name $serviceId).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(45))
      }
      Invoke-Wrapper 'uninstall'
    }
    # Home, SQLite, XML, tokens and certificates are intentionally preserved.
  }
}

Write-Output "Operação $Action concluída para $serviceId."
