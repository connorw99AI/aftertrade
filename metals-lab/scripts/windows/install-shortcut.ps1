# Puts a "Metal Lab" icon on the desktop (and in the Start menu) that runs launch.ps1.
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\windows\install-shortcut.ps1
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$launcher = Join-Path $PSScriptRoot "launch.ps1"
$icon = Join-Path $PSScriptRoot "metal-lab.ico"
$shell = New-Object -ComObject WScript.Shell
$places = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
foreach ($dir in $places) {
  $lnk = $shell.CreateShortcut((Join-Path $dir "Metal Lab.lnk"))
  $lnk.TargetPath = "powershell.exe"
  $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
  $lnk.WorkingDirectory = "$Root"
  $lnk.IconLocation = "$icon,0"
  $lnk.Description = "Start Metal Lab (updates first, never arms trading)"
  $lnk.Save()
  Write-Host "Created $(Join-Path $dir 'Metal Lab.lnk')"
}
