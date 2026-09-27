param(
    [Parameter(Mandatory=$true)][ValidateSet('atelier','qwen')][string]$Target,
    [string]$PortableRoot = 'K:\ComfyUI'
)
$ErrorActionPreference = 'Stop'
$portNumber = if ($Target -eq 'atelier') { 18791 } else { 8188 }
$mutex = New-Object System.Threading.Mutex($false, "Local\ImageAtelier-Startup-$portNumber")
$locked = $false
try {
    try { $locked = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (!$locked) {
        Write-Host "$Target is already running or starting in another window."
        exit 0
    }
    $connection = New-Object System.Net.Sockets.TcpClient
    $listening = $false
    try {
        $task = $connection.ConnectAsync('127.0.0.1', $portNumber)
        if ($task.Wait(500)) { $listening = $connection.Connected }
    } catch { } finally { $connection.Dispose() }
    if ($listening) {
        Write-Host "Port $portNumber is already listening. No second process was started."
        Write-Host 'If it was launched in the background, stop that instance before starting it in this tab.'
        exit 0
    }
    Write-Host "Starting $Target in this terminal. URL: http://127.0.0.1:$portNumber"
    Write-Host 'Ctrl+C or closing this terminal stops the service.'
    if ($Target -eq 'atelier') {
        & (Join-Path $PSScriptRoot 'start.ps1')
    } else {
        & (Join-Path $PSScriptRoot 'start-comfyui.ps1') -PortableRoot $PortableRoot -Port $portNumber
    }
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
