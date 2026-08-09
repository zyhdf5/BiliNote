$ErrorActionPreference = "Stop"
$Service = if ($env:SERVICE) { $env:SERVICE } else { "bilinote-summary" }
$Port = if ($env:APP_PORT) { $env:APP_PORT } else { "8080" }
$BaseUrl = if ($env:BASE_URL) { $env:BASE_URL } else { "http://127.0.0.1:$Port" }

function Run-Step([string]$Label, [scriptblock]$Command) {
    Write-Host "`n> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

Run-Step "docker info" { docker info | Out-Null }
Run-Step "python version" { docker exec $Service python --version }
Run-Step "yt-dlp version" { docker exec $Service yt-dlp --version }
Run-Step "ffmpeg version" { docker exec $Service ffmpeg -version }
Run-Step "nvidia-smi" { docker exec $Service nvidia-smi }
Run-Step "CTranslate2 CUDA discovery" { docker exec $Service python -c 'import ctranslate2; n=ctranslate2.get_cuda_device_count(); print("CTranslate2 CUDA devices:", n); raise SystemExit(0 if n > 0 else 2)' }

Write-Host "`n> GET $BaseUrl/healthz"
Invoke-RestMethod "$BaseUrl/healthz" | ConvertTo-Json -Depth 8
Write-Host "`n> GET $BaseUrl/readyz"
Invoke-RestMethod "$BaseUrl/readyz" | ConvertTo-Json -Depth 8

Write-Host "`n[OK] container, dependencies, CUDA and readiness checks passed"
Write-Host "Next: open $BaseUrl/settings, run Test LLM + Load Whisper model, then submit one real video URL."
