param([Parameter(Mandatory=$true)][string]$EnvFile,[switch]$Server)
$ErrorActionPreference='Stop'
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$environmentPath=(Resolve-Path -LiteralPath $EnvFile).Path
$composeArgs=@('compose','--env-file',$environmentPath,'-f',(Join-Path $repoRoot 'compose.yml'))
if($Server){$composeArgs+=@('-f',(Join-Path $repoRoot 'compose.server.yml'))}
& docker @composeArgs stop
if($LASTEXITCODE -ne 0){throw 'Container stop failed.'}
Write-Output 'Containers stopped; database and asset volumes are retained.'
