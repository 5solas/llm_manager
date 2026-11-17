# Claude Code status line: model, effort, working folder, context usage.
# Then forwards the same payload to Orca's statusline script (which prints nothing itself).
$ErrorActionPreference = 'SilentlyContinue'

# Read stdin as raw bytes so UTF-8 survives regardless of console code page.
$ms = New-Object System.IO.MemoryStream
[Console]::OpenStandardInput().CopyTo($ms)
$bytes = $ms.ToArray()
$data = [System.Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json

$e     = [char]27
$reset = "$e[0m"
$dim   = "$e[2m"
$sep   = " $dim|$reset "
$parts = @()

# Model (+ effort)
$name = $data.model.display_name
if (-not $name) { $name = $data.model.id }
if ($name) {
    $m = "$e[36m$name$reset"
    if ($data.effort.level) { $m += " $dim$($data.effort.level)$reset" }
    $parts += $m
}

# Working folder (leaf name only)
$dir = $data.workspace.current_dir
if (-not $dir) { $dir = $data.cwd }
if ($dir) { $parts += "$e[34m$(Split-Path $dir -Leaf)$reset" }

# Context usage: green < 50%, yellow < 80%, red otherwise
$pct = $data.context_window.used_percentage
if ($null -ne $pct) {
    $c = if ($pct -lt 50) { 32 } elseif ($pct -lt 80) { 33 } else { 31 }
    $parts += "ctx $e[${c}m$pct%$reset"
}

if ($parts.Count) {
    $out = [System.Text.Encoding]::UTF8.GetBytes(($parts -join $sep))
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($out, 0, $out.Length)
    $stdout.Flush()
}

$orca = Join-Path $env:USERPROFILE '.orca\agent-hooks\claude-statusline.cmd'
if ($env:ORCA_PANE_KEY -and (Test-Path $orca)) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = "/d /c `"`"$orca`"`""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.CreateNoWindow = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $p.StandardInput.Close()
    [void]$p.WaitForExit(3000)
}
exit 0
