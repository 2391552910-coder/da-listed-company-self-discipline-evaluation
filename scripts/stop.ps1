param([Parameter(Mandatory=$true)][string]$EnvFile,[switch]$Server)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'compose-command.ps1')
$compose=Get-DalunComposeCommand
$composeExecutable=$compose.Executable
$repoRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$environmentPath=(Resolve-Path -LiteralPath $EnvFile).Path
$composeArgs=@($compose.Prefix)+@('--env-file',$environmentPath,'-f',(Join-Path $repoRoot 'compose.yml'))
if($Server){$composeArgs+=@('-f',(Join-Path $repoRoot 'compose.server.yml'))}
$stopArgs=$composeArgs+@('stop')
if((Invoke-DalunCompose -Executable $composeExecutable -Arguments $stopArgs) -ne 0){throw 'Container stop failed.'}
Write-Output 'Containers stopped; database and asset volumes are retained.'
