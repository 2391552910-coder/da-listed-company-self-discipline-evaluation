function Invoke-DalunCompose {
    param([string]$Executable,[string[]]$Arguments,[switch]$Quiet)
    $previousPathExt=$env:PATHEXT
    try {
        # Some hosted shells omit .EXE; change only this process while invoking
        # the resolved native executable, and always restore the caller's value.
        if($env:PATHEXT -notmatch '(?i)(^|;)\.EXE(;|$)'){$env:PATHEXT='.EXE;'+$env:PATHEXT}
        if($Quiet){& $Executable @Arguments *> $null}else{& $Executable @Arguments | Out-Host}
        $nativeExitCode=$LASTEXITCODE
    } finally {$env:PATHEXT=$previousPathExt}
    return $nativeExitCode
}
function Get-DalunComposeCommand {
    foreach($name in @('docker.exe','docker')){
        $command=Get-Command $name -ErrorAction SilentlyContinue
        if(-not $command){continue}
        try {
            if((Invoke-DalunCompose -Executable $command.Source -Arguments @('compose','version') -Quiet) -eq 0){
                return [pscustomobject]@{Executable=$command.Source;Prefix=@('compose')}
            }
        } catch {}
    }
    foreach($name in @('docker-compose.exe','docker-compose')){
        $command=Get-Command $name -ErrorAction SilentlyContinue
        if(-not $command){continue}
        try {
            if((Invoke-DalunCompose -Executable $command.Source -Arguments @('version') -Quiet) -eq 0){
                return [pscustomobject]@{Executable=$command.Source;Prefix=@()}
            }
        } catch {}
    }
    throw 'Docker Compose is unavailable. Install the Compose plugin or standalone docker-compose.'
}
