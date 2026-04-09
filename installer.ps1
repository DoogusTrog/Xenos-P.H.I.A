Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Starting Python package + Ollama setup " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/4] Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

Write-Host ""
Write-Host "[2/4] Installing Python packages..." -ForegroundColor Yellow
Write-Host " - psutil"
Write-Host " - pyautogui"
Write-Host " - ollama"
Write-Host " - pywin32"
Write-Host " - GPUtil"
Write-Host " - numpy"
Write-Host " - pillow"
Write-Host " - SpeechRecognition"
Write-Host " - pycaw"
Write-Host " - comtypes"
Write-Host " - soundcard"
python -m pip install psutil pyautogui ollama pywin32 GPUtil numpy pillow SpeechRecognition pycaw comtypes soundcard

Write-Host ""
Write-Host "[Note] tkinter is usually included with Python on Windows and is not installed via pip." -ForegroundColor DarkCyan

Write-Host ""
Write-Host "[3/4] Checking whether Ollama is already installed..." -ForegroundColor Yellow
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama was not found. Installing Ollama now..." -ForegroundColor Magenta
    irm https://ollama.com/install.ps1 | iex
} else {
    Write-Host "Ollama is already installed." -ForegroundColor Green
}

Write-Host ""
Write-Host "[4/4] Pulling Ollama model: llama3.2-vision" -ForegroundColor Yellow
ollama pull llama3.2-vision

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host " Setup complete." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
