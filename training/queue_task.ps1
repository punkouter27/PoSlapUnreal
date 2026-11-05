# Restart a training queue after a logoff or a reboot (2026-11-04: the v3 queue died at 12:29 when Windows logged the
# user off: exit 0x40010004, then 0xC000026B, and nothing restarted it).
#
#   powershell -ExecutionPolicy Bypass -File training\queue_task.ps1 -Queue retrain_v4_queue.json -Hours 16            # register
#   powershell -ExecutionPolicy Bypass -File training\queue_task.ps1 -Queue retrain_v4_queue.json -Hours 16 -S4U       # survives logoff
#   powershell -ExecutionPolicy Bypass -File training\queue_task.ps1 -Remove                                           # unregister
#
# The task runs `overnight.py --resume --hours <Hours> --queue <Queue>` from training/ with the venv's python, hidden,
# appending to runs/<queue>_task.log. overnight.py --resume picks the queue up where it stopped (the queue file holds
# each stage's run; a half-done stage carries on from its latest.pt for the minutes it has left), and its lock file
# makes a second start (the trigger firing while a driver already runs) a no-op.
#
# Triggers: at logon of this user (default), and at start-up. -S4U registers it to run whether the user is logged on
# or not (Task Scheduler's "S4U" logon: no stored password, no network shares; CUDA works in that session on this
# machine's driver - check `nvidia-smi` from a test run first). Without -S4U a logoff still kills the run, and the next
# logon restarts it. Registering needs no admin rights for the logon trigger; the start-up trigger and -S4U may.
# Nothing here assumes a drive letter: every path comes from this script's own location.

[CmdletBinding()]
param(
    [string]$Queue = "retrain_v4_queue.json",
    [double]$Hours = 16,
    [switch]$S4U,
    [switch]$Remove,
    [string]$Name = "PoSlap training queue"
)

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Here ".venv\Scripts\python.exe"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "removed '$Name'"
    exit 0
}
if (-not (Test-Path (Join-Path $Here "runs\$Queue"))) { Write-Error "no training\runs\$Queue"; exit 1 }
$Log = Join-Path $Here ("runs\" + [IO.Path]::GetFileNameWithoutExtension($Queue) + "_task.log")
$Cmd = "/c `"`"$Py`" -u overnight.py --resume --hours $Hours --queue $Queue >> `"$Log`" 2>&1`""
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $Cmd -WorkingDirectory $Here
$Triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME), (New-ScheduledTaskTrigger -AtStartup))
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours ([math]::Ceiling($Hours) + 2)) -MultipleInstances IgnoreNew
$Logon = if ($S4U) { "S4U" } else { "Interactive" }
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType $Logon -RunLevel Limited
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Triggers -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host "registered '$Name' ($Logon): overnight.py --resume --hours $Hours --queue $Queue -> $Log"
Write-Host "start it now: Start-ScheduledTask -TaskName '$Name'   (or run the same command by hand)"
