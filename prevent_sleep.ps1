Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class PowerMgmt {
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);

    public const uint ES_CONTINUOUS      = 0x80000000;
    public const uint ES_SYSTEM_REQUIRED = 0x00000001;
}
"@

$flags = [PowerMgmt]::ES_CONTINUOUS -bor [PowerMgmt]::ES_SYSTEM_REQUIRED
$prev  = [PowerMgmt]::SetThreadExecutionState($flags)

if ($prev -eq 0) {
    Write-Host "ERROR: SetThreadExecutionState failed." -ForegroundColor Red
    exit 1
}

Write-Host "Sleep prevention ACTIVE. Display can still turn off." -ForegroundColor Green
Write-Host "Press Ctrl+C to exit and restore default power settings..." -ForegroundColor Yellow

try {
    while ($true) { Start-Sleep -Seconds 30 }
} finally {
    [PowerMgmt]::SetThreadExecutionState([PowerMgmt]::ES_CONTINUOUS)
    Write-Host "Power settings restored." -ForegroundColor Green
}
