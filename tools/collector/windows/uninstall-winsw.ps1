param(
  [string]$InstallDir = "$env:ProgramFiles\FiscalXmlCollector",
  [string]$ServiceId = "FiscalXmlCollector"
)
$ErrorActionPreference = 'Stop'
$wrapper = Join-Path $InstallDir "$ServiceId.exe"
if (-not (Test-Path -Path $wrapper -PathType Leaf)) { throw 'WinSW do serviço não encontrado.' }
& $wrapper stop
& $wrapper uninstall
if ($LASTEXITCODE -ne 0) { throw "WinSW falhou ao remover o serviço ($LASTEXITCODE)." }
Write-Host 'Serviço removido. Configuração, token, XMLs originais e banco de estado foram preservados.'
