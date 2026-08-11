# Stops every process started by scripts/dev.py. Services first, then PGlite GRACEFULLY (stop file → flush + close):
# force-killing PGlite mid-write can corrupt its data directory.
$root = Split-Path -Parent $PSScriptRoot
$stopFile = Join-Path $root 'var\pglite\STOP'

function Get-Ours($pattern) {
  Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='python.exe'" | Where-Object { $_.CommandLine -match $pattern }
}

# 1) launcher + services + web
Get-Ours 'uvicorn|orchestrator\.worker|scheduler\.main|outbox_relay\.main|scripts[\\/]dev\.py|next dev|next start|next-server|next[\\/]dist' |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 2) PGlite: ask it to flush and close
$pg = @(Get-Ours 'serve\.mjs')
if ($pg.Count -gt 0) {
  New-Item -ItemType Directory -Force (Split-Path $stopFile) | Out-Null
  Set-Content -Path $stopFile -Value 'stop'
  $deadline = (Get-Date).AddSeconds(30)
  while ((Get-Date) -lt $deadline -and @(Get-Ours 'serve\.mjs').Count -gt 0) { Start-Sleep -Milliseconds 300 }
  $left = @(Get-Ours 'serve\.mjs')
  if ($left.Count -gt 0) {
    "pglite did not stop in 30 s - forcing (data may need recovery on next start)"
    $left | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  } else { "pglite closed cleanly" }
}
if (Test-Path $stopFile) { Remove-Item $stopFile -Force }
"stopped"
