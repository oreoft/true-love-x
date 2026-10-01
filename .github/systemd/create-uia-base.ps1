#Requires -RunAsAdministrator

$TaskName = "UIA-TL-BASE"
# The runner lives next to this script and is updated by git pull, so the task runs it in place
$RunnerScript = Join-Path $PSScriptRoot "run-true-love-base.ps1"

Write-Host "=== Creating Scheduled Task: $TaskName ===" -ForegroundColor Cyan

# 删除已存在的任务
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
{
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the task: it still runs on the logged-in desktop (UIA needs it), only the console window is hidden; logs go to Loki
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunnerScript`""

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

$Task = New-ScheduledTask -Action $Action -Principal $Principal -Settings $Settings

Register-ScheduledTask -TaskName $TaskName -InputObject $Task | Out-Null

Write-Host ""
Write-Host "=== Task Created Successfully ===" -ForegroundColor Green
Write-Host "Task Name : $TaskName"
Write-Host "Runner    : $RunnerScript"
Write-Host ""
Write-Host "Commands:" -ForegroundColor Cyan
Write-Host "  Start  : schtasks /run /tn `"$TaskName`""
Write-Host "  Stop   : schtasks /end /tn `"$TaskName`""
Write-Host "  Status : Get-ScheduledTask -TaskName `"$TaskName`""
Write-Host "  Delete : Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"