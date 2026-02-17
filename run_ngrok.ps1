param(
  [string]$Authtoken = ""
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$ngrok = "C:\Users\moham\AppData\Local\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"
if (-not (Test-Path $ngrok)) {
  throw "ngrok.exe not found at expected path."
}

if ($Authtoken) {
  & $ngrok config add-authtoken $Authtoken
}

$python = "C:\Users\moham\KamilAgency\id-barcode-lab\backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  $python = "python"
}

# Restart backend on plain HTTP for tunneling.
Get-NetTCPConnection -LocalPort 8012 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

$backend = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8012' -WorkingDirectory $PSScriptRoot -PassThru
Start-Sleep -Seconds 2

# Start ngrok.
$ng = Start-Process -FilePath $ngrok -ArgumentList 'http','8012' -PassThru
Start-Sleep -Seconds 3

try {
  $resp = Invoke-WebRequest -Uri "http://127.0.0.1:4040/api/tunnels" -UseBasicParsing -TimeoutSec 5
  $json = $resp.Content | ConvertFrom-Json
  $url = $json.tunnels | Where-Object { $_.public_url -like 'https://*' } | Select-Object -First 1 -ExpandProperty public_url
  if ($url) {
    Write-Host "ngrok URL: $url"
  } else {
    Write-Host "ngrok started but no HTTPS tunnel found. Check ngrok dashboard/logs."
  }
} catch {
  Write-Host "Could not query ngrok API. If you see ERR_NGROK_4018, set your authtoken first."
}

Write-Host "Backend PID: $($backend.Id)"
Write-Host "ngrok PID: $($ng.Id)"