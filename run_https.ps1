$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$port = 8012
$certDir = Join-Path $PSScriptRoot "certs"
$certFile = Join-Path $certDir "lan-cert.pem"
$keyFile = Join-Path $certDir "lan-key.pem"
$rootCopy = Join-Path $certDir "rootCA.pem"
$rootKey = Join-Path $certDir "rootCA-key.pem"

New-Item -ItemType Directory -Force -Path $certDir | Out-Null

$ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object {
    $_.IPAddress -notmatch '^127\.' -and
    $_.IPAddress -notmatch '^169\.254\.' -and
    $_.PrefixOrigin -ne 'WellKnown'
  } |
  Select-Object -ExpandProperty IPAddress -Unique

$names = @("localhost", "127.0.0.1", "::1", $env:COMPUTERNAME) + $ips
$names = $names | Where-Object { $_ } | Select-Object -Unique

$pythonCmd = "python"
$candidates = @(
  (Join-Path $PSScriptRoot ".venv\Scripts\python.exe"),
  "C:\Users\moham\KamilAgency\id-barcode-lab\backend\.venv\Scripts\python.exe",
  "python"
)

foreach ($candidate in $candidates) {
  $exists = $true
  if ($candidate -ne "python") {
    $exists = Test-Path $candidate
  }
  if (-not $exists) {
    continue
  }
  try {
    & $candidate -c "import fastapi, uvicorn, cv2, zxingcpp, cryptography" | Out-Null
    if ($LASTEXITCODE -eq 0) {
      $pythonCmd = $candidate
      break
    }
  } catch {
    continue
  }
}

if ($pythonCmd -eq "python") {
  Write-Host "Warning: using system python. Ensure dependencies are installed."
}

$mkcert = Get-Command mkcert -ErrorAction SilentlyContinue
if ($mkcert) {
  Write-Host "Using mkcert for trusted LAN certs..."
  & mkcert -install
  & mkcert -cert-file $certFile -key-file $keyFile @names
  $caroot = (& mkcert -CAROOT).Trim()
  $rootCaPem = Join-Path $caroot "rootCA.pem"
  if (Test-Path $rootCaPem) {
    Copy-Item $rootCaPem $rootCopy -Force
  }
} else {
  Write-Host "mkcert not found. Generating local CA + LAN cert with cryptography..."
  $args = @(
    "tools\generate_lan_certs.py",
    "--cert-file", $certFile,
    "--key-file", $keyFile,
    "--root-cert-file", $rootCopy,
    "--root-key-file", $rootKey
  )
  foreach ($n in $names) {
    $args += @("--name", $n)
  }
  & $pythonCmd @args
}

$lanIp = ($ips | Select-Object -First 1)
if (-not $lanIp) {
  $lanIp = "<YOUR_PC_LAN_IP>"
}

Write-Host ""
Write-Host "HTTPS ready."
Write-Host "Phone URL: https://$lanIp`:$port"
Write-Host "Install and trust this CA on your phone: $rootCopy"
Write-Host ""

& $pythonCmd -m uvicorn app.main:app --host 0.0.0.0 --port $port --ssl-keyfile $keyFile --ssl-certfile $certFile