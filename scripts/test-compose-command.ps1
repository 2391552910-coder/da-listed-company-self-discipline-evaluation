$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $root 'scripts/compose-command.ps1')
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('dalun-compose-'+[guid]::NewGuid())
$bin=Join-Path $testRoot 'commands with spaces'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$oldPath=$env:PATH;$oldExt=$env:PATHEXT
$oldMode=$env:DALUN_TEST_PLUGIN;$oldLog=$env:DALUN_TEST_LOG
try {
  $env:DALUN_TEST_LOG=Join-Path $testRoot 'arguments.txt'
  Add-Type -OutputAssembly (Join-Path $bin 'docker.exe') -OutputType ConsoleApplication -TypeDefinition @'
using System;
class DockerStub {static int Main(string[] a){return Environment.GetEnvironmentVariable("DALUN_TEST_PLUGIN")=="1"?0:42;}}
'@
  Add-Type -OutputAssembly (Join-Path $bin 'docker-compose.exe') -OutputType ConsoleApplication -TypeDefinition @'
using System;
using System.IO;
class ComposeStub {static int Main(string[] a){File.AppendAllText(Environment.GetEnvironmentVariable("DALUN_TEST_LOG"),String.Join("|",a)+Environment.NewLine);return a.Length>0&&a[0]=="fail"?7:0;}}
'@
  $env:PATH=$bin
  $env:PATHEXT='.CPL'
  $env:DALUN_TEST_PLUGIN='1'
  $plugin=Get-DalunComposeCommand
  if($plugin.Executable -ne (Join-Path $bin 'docker.exe') -or $plugin.Prefix[0] -ne 'compose'){throw 'Plugin selection failed'}
  if($env:PATHEXT -ne '.CPL'){throw 'Probe changed caller PATHEXT'}
  $env:DALUN_TEST_PLUGIN='0'
  $standalone=Get-DalunComposeCommand
  if($standalone.Executable -ne (Join-Path $bin 'docker-compose.exe') -or $standalone.Prefix.Count -ne 0){throw 'Standalone fallback failed'}
  $result=Invoke-DalunCompose -Executable $standalone.Executable -Arguments @('fail') -Quiet
  if($result -ne 7 -or $env:PATHEXT -ne '.CPL'){throw 'Exit code or environment restoration failed'}
  $threw=$false
  try {Invoke-DalunCompose -Executable (Join-Path $bin 'missing.exe') -Arguments @('version') -Quiet | Out-Null}catch{$threw=$true}
  if(-not $threw -or $env:PATHEXT -ne '.CPL'){throw 'Exception environment restoration failed'}
  $envFile=Join-Path $testRoot 'private environment.env'
  Set-Content -LiteralPath $envFile -Value '# isolated command test'
  & (Join-Path $root 'scripts/start.ps1') -EnvFile $envFile -NoBuild -Wait -WaitTimeout 90
  $lines=Get-Content -LiteralPath $env:DALUN_TEST_LOG
  $up=@($lines | Where-Object {$_ -match '\|up\|'})[-1]
  if(-not $up -or -not $up.Contains($envFile) -or -not $up.Contains('--no-build') -or -not $up.Contains('--wait-timeout|90')){throw 'Start argument preservation failed'}
  & (Join-Path $root 'scripts/stop.ps1') -EnvFile $envFile
  $last=(Get-Content -LiteralPath $env:DALUN_TEST_LOG)[-1]
  if(-not $last.EndsWith('|stop') -or $last.Contains('|-v')){throw 'Stop did not preserve volumes'}
  if($env:PATHEXT -ne '.CPL'){throw 'Entry point changed caller PATHEXT'}
  Write-Output 'PASS: plugin, standalone, native exit code, argument paths, wait/no-build, stop and environment restoration'
} finally {
  $env:PATH=$oldPath;$env:PATHEXT=$oldExt
  $env:DALUN_TEST_PLUGIN=$oldMode;$env:DALUN_TEST_LOG=$oldLog
}
