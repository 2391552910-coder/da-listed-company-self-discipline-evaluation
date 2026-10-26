param([Parameter(Mandatory=$true)][ValidateSet('api','worker','migrate','doctor')][string]$Role,
      [Parameter(Mandatory=$true)][string]$EnvFile,[string]$Python='python',
      [ValidateSet('qwen_cuda','bm25_diagnostic')][string]$RetrievalProfile='qwen_cuda',
      [ValidateRange(1,65535)][int]$Port=8000,[switch]$VerifyCuda)
$ErrorActionPreference='Stop'
$environmentPath=(Resolve-Path -LiteralPath $EnvFile).Path
$previousEnvFile=$env:DALUN_ENV_FILE
$env:DALUN_ENV_FILE=$environmentPath
Push-Location (Join-Path $PSScriptRoot '..\backend')
try {
    if($Role -eq 'migrate'){& $Python -m alembic -c alembic.ini upgrade head}
    elseif($Role -eq 'worker'){& $Python -m app.documents.cli worker}
    elseif($Role -eq 'doctor'){
        $doctorArgs=@('-m','app.operations.preflight','--retrieval-profile',$RetrievalProfile)
        if($VerifyCuda){$doctorArgs+='--verify-cuda'}
        & $Python @doctorArgs
    } else {& $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port}
    if($LASTEXITCODE -ne 0){throw "Native process exited with code $LASTEXITCODE"}
} finally {Pop-Location;$env:DALUN_ENV_FILE=$previousEnvFile}
