param([Parameter(Mandatory=$true)][string]$EnvFile,[switch]$Server,[switch]$NoBuild,
      [switch]$Wait,[ValidateRange(30,1800)][int]$WaitTimeout=240)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'compose-command.ps1')
$compose=Get-DalunComposeCommand
$composeExecutable=$compose.Executable
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$environmentPath=(Resolve-Path -LiteralPath $EnvFile).Path
$composeArgs=@($compose.Prefix)+@('--env-file',$environmentPath,'-f',(Join-Path $repoRoot 'compose.yml'))
if($Server){$composeArgs+=@('-f',(Join-Path $repoRoot 'compose.server.yml'))}
$checkArgs=$composeArgs+@('config','--quiet')
if((Invoke-DalunCompose -Executable $composeExecutable -Arguments $checkArgs) -ne 0){throw 'Compose configuration validation failed; inspect the private environment file.'}
$upArgs=@('up','-d')
if($NoBuild){$upArgs+='--no-build'}else{$upArgs+='--build'}
if($Wait){$upArgs+=@('--wait','--wait-timeout',[string]$WaitTimeout)}
$startArgs=$composeArgs+$upArgs
if((Invoke-DalunCompose -Executable $composeExecutable -Arguments $startArgs) -ne 0){throw 'Container startup failed.'}
Write-Output 'Services started. Inspect /health/ready and /health/workers before creating work.'
