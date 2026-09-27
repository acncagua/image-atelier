param([switch]$PreviewOnly)
$ErrorActionPreference = 'Stop'
$services = @(
    @{ Title = 'AssetManagementApp'; Batch = 'D:\CODEX Project\AssetManagementApp\start_app.bat' },
    @{ Title = 'pixiv-library'; Batch = 'D:\CODEX Project\pixiv-library\pixiv-library-private\run_viewer.bat' },
    @{ Title = 'obsidian-mcp-server'; Batch = 'D:\CODEX Project\obsidian-mcp-server\obsidian-mcp-server.bat' },
    @{ Title = 'Image Atelier'; Batch = (Join-Path $PSScriptRoot 'start-atelier.bat') },
    @{ Title = 'ComfyUI(Qwen)'; Batch = (Join-Path $PSScriptRoot 'start-qwen.bat') }
)
$wtArgs = @()
foreach ($service in $services) {
    if (!(Test-Path -LiteralPath $service.Batch)) { throw "Batch file not found: $($service.Batch)" }
    if ($wtArgs.Count) { $wtArgs += ';' }
    # Start-Process joins arguments with spaces: keep actual quotes around titles.
    $wtArgs += @('new-tab', '--title', ('"' + $service.Title + '"'), 'cmd.exe', '/k', ('"' + $service.Batch + '"'))
}
if ($PreviewOnly) { $wtArgs -join ' '; exit 0 }
Start-Process -FilePath 'wt.exe' -ArgumentList $wtArgs
