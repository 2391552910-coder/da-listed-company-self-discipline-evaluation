param([ValidateSet('api','worker','migrate')][string]$Mode='api',[string]$Python='python')
$ErrorActionPreference='Stop'
Push-Location (Join-Path $PSScriptRoot '..\backend')
try {
    if($Mode -eq 'migrate'){ & $Python -m alembic -c alembic.ini upgrade head }
    elseif($Mode -eq 'worker'){ & $Python -m app.documents.cli worker }
    else{ & $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 }
    if($LASTEXITCODE -ne 0){throw "Stage2 process exited: $LASTEXITCODE"}
} finally { Pop-Location }
