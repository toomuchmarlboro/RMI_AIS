# PowerShell Launcher for RMI AIS
$VenvPython = ".\.venv\Scripts\python.exe"

if (-Not (Test-Path $VenvPython)) {
    Write-Host "[!] Virtual environment not found. Creating and installing requirements..." -ForegroundColor Yellow
    python -m venv .venv
    .\.venv\Scripts\pip.exe install -r requirements.txt
}

Write-Host "[+] Starting RMI AIS Vessel Tracker..." -ForegroundColor Cyan
Write-Host "[+] Open http://127.0.0.1:8000 in your browser when started." -ForegroundColor Green
& $VenvPython -m src.main
