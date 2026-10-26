param([Parameter(Mandatory=$true)][string]$EnvFile,[switch]$Server,[switch]$NoBuild)
$ErrorActionPreference='Stop'
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$environmentPath=(Resolve-Path -LiteralPath $EnvFile).Path
$composeArgs=@('compose','--env-file',$environmentPath,'-f',(Join-Path $repoRoot 'compose.yml'))
if($Server){$composeArgs+=@('-f',(Join-Path $repoRoot 'compose.server.yml'))}
& docker @composeArgs config --quiet
if($LASTEXITCODE -ne 0){throw 'Compose configuration validation failed; inspect the private environment file.'}
$upArgs=@('up','-d')
if(-not $NoBuild){$upArgs+='--build'}
& docker @composeArgs @upArgs
if($LASTEXITCODE -ne 0){throw 'Container startup failed.'}
Write-Output 'Services started. Inspect docker compose ps and /health/ready plus /health/workers before creating work.'
