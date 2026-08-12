param(
  [string]$FrontendUrl = 'http://127.0.0.1:5173',
  [string]$ApiUrl = 'http://127.0.0.1:8765'
)

$ErrorActionPreference = 'Stop'

try {
  $frontend = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 10
  if ($frontend.StatusCode -ne 200) { throw "Frontend returned HTTP $($frontend.StatusCode)" }
  $health = Invoke-RestMethod -Uri ($ApiUrl + '/api/health') -TimeoutSec 10
  if (-not $health.ok) { throw 'Backend health endpoint did not report ok' }
  $result = Invoke-RestMethod -Uri ($ApiUrl + '/api/lab/flows/simulations/authoring') -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 60
  $result | ConvertTo-Json -Depth 8
  if (-not $result.ok) { exit 1 }
} catch {
  [pscustomobject]@{
    ok = $false
    error = $_.Exception.Message
    frontend = $FrontendUrl
    api = $ApiUrl
  } | ConvertTo-Json -Depth 4
  exit 1
}
