<#
.SYNOPSIS
  TSS MDM agent for Windows 10/11.
.EXAMPLE
  # Enroll and install as a scheduled task (run PowerShell as Administrator)
  .\tss-agent.ps1 -Server https://mdm.example.com -Token <TOKEN> -Install
.EXAMPLE
  # Single check-in with saved credentials (what the scheduled task runs)
  .\tss-agent.ps1 -Once
#>
param(
  [string]$Server,
  [string]$Token,
  [switch]$Install,
  [switch]$Once,
  [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$Version  = '0.3.0'
$ConfDir  = Join-Path $env:ProgramData 'TSSMDM'
$ConfFile = Join-Path $ConfDir 'agent.json'
$TaskName = 'TSS MDM Agent'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-Inventory {
  $cs   = Get-CimInstance Win32_ComputerSystem
  $bios = Get-CimInstance Win32_BIOS
  $prod = Get-CimInstance Win32_ComputerSystemProduct
  $os   = Get-CimInstance Win32_OperatingSystem
  $cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1
  $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
  $inv = [ordered]@{
    name             = $env:COMPUTERNAME
    os_version       = "$($os.Caption) $($os.Version)"
    manufacturer     = $cs.Manufacturer
    model            = $cs.Model
    serial_number    = $bios.SerialNumber
    hardware_id      = $prod.UUID
    cpu              = $cpu.Name.Trim()
    ram_total_gb     = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
    storage_total_gb = [math]::Round($disk.Size / 1GB, 1)
    storage_free_gb  = [math]::Round($disk.FreeSpace / 1GB, 1)
    agent_version    = $Version
    domain           = $cs.Domain
    logged_in_user   = $cs.UserName
    last_boot        = $os.LastBootUpTime.ToString('o')
  }
  $batt = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($batt) { $inv.battery_level = [double]$batt.EstimatedChargeRemaining; $inv.is_charging = ($batt.BatteryStatus -eq 2) }
  try { $bl = Get-BitLockerVolume -MountPoint 'C:' -ErrorAction Stop; $inv.is_encrypted = ($bl.ProtectionStatus -eq 'On') } catch {}
  $nic = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' |
         Where-Object { $_.DefaultIPGateway } | Select-Object -First 1
  if ($nic) { $inv.ip_address = ($nic.IPAddress | Where-Object { $_ -match '^\d+\.' } | Select-Object -First 1); $inv.mac_address = $nic.MACAddress }
  $ssid = (netsh wlan show interfaces 2>$null | Select-String '^\s+SSID\s+:' | Select-Object -First 1)
  if ($ssid) { $inv.wifi_ssid = ($ssid.ToString() -split ':', 2)[1].Trim() }
  $keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
          'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
  $inv.installed_apps = @(Get-ItemProperty $keys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -and -not $_.SystemComponent } |
    Sort-Object DisplayName -Unique |
    ForEach-Object { @{ name = $_.DisplayName; version = "$($_.DisplayVersion)"; publisher = "$($_.Publisher)" } })
  return $inv
}

function Invoke-Mdm($Path, $Body, $Conf) {
  $headers = @{ 'User-Agent' = "tss-agent-win/$Version" }
  if ($Conf) { $headers.Authorization = "Device $($Conf.device_id):$($Conf.device_secret)" }
  $base = if ($Conf) { $Conf.server } else { $Server.TrimEnd('/') }
  Invoke-RestMethod -Uri "$base$Path" -Method Post -Headers $headers -ContentType 'application/json' `
    -Body ($Body | ConvertTo-Json -Depth 6 -Compress)
}

function Save-Conf($Conf) {
  New-Item -ItemType Directory -Force -Path $ConfDir | Out-Null
  $Conf | ConvertTo-Json | Set-Content -Path $ConfFile -Encoding UTF8
  # Only SYSTEM and Administrators can read the device credential
  icacls $ConfFile /inheritance:r /grant:r 'SYSTEM:F' 'Administrators:F' | Out-Null
}

function Invoke-Command1($cmd) {
  switch ($cmd.command) {
    'refresh_inventory' { return @{ id = $cmd.id; status = 'succeeded'; result = 'inventory sent' } }
    'send_message' {
      $text = "$($cmd.payload.title): $($cmd.payload.body)"
      msg.exe * /TIME:300 $text 2>$null
      return @{ id = $cmd.id; status = 'succeeded'; result = 'message shown' }
    }
    'reboot' {
      shutdown.exe /r /t 120 /c "Your IT team is restarting this PC in 2 minutes."
      return @{ id = $cmd.id; status = 'succeeded'; result = 'reboot scheduled in 120s' }
    }
    'shutdown' {
      shutdown.exe /s /t 120 /c "Your IT team is shutting down this PC in 2 minutes."
      return @{ id = $cmd.id; status = 'succeeded'; result = 'shutdown scheduled in 120s' }
    }
    'lock' {
      rundll32.exe user32.dll,LockWorkStation
      return @{ id = $cmd.id; status = 'succeeded'; result = 'workstation locked' }
    }
    'run_script' {
      $code = [string]$cmd.payload.script
      $shell = [string]$cmd.payload.shell
      try {
        if ($shell -eq 'cmd') { $out = cmd.exe /c $code 2>&1 | Out-String }
        else { $out = powershell -NoProfile -ExecutionPolicy Bypass -Command $code 2>&1 | Out-String }
        $ok = ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE)
        if (-not $out) { $out = "(no output, exit code $LASTEXITCODE)" }
        if ($out.Length -gt 3900) { $out = $out.Substring(0, 3900) + "`n...(truncated)" }
        return @{ id = $cmd.id; status = $(if ($ok) { 'succeeded' } else { 'failed' }); result = $out }
      } catch {
        return @{ id = $cmd.id; status = 'failed'; result = ("ERROR: " + $_) }
      }
    }
    default { return @{ id = $cmd.id; status = 'failed'; result = "'$($cmd.command)' not supported by this agent yet" } }
  }
}

function Update-AgentIfNeeded($Conf, $Latest) {
  if (-not $Latest) { return }
  try { if ([version]$Latest -le [version]$Version) { return } } catch { return }
  $dest = Join-Path $ConfDir 'tss-agent.ps1'
  try {
    Invoke-WebRequest "$($Conf.server)/downloads/tss-agent.ps1" -OutFile "$dest.new" -UseBasicParsing
    Move-Item "$dest.new" $dest -Force
    Write-Host "Updated agent $Version -> $Latest (new version runs at next check-in)"
  } catch { Write-Warning "Agent self-update failed: $_" }
}

function Invoke-Checkin($Conf) {
  $results = @()
  $latest = $null
  for ($i = 0; $i -lt 2; $i++) {
    $r = Invoke-Mdm '/api/agent/checkin' @{ inventory = (Get-Inventory); command_results = $results } $Conf
    $latest = $r.latest_agent_version
    $results = @($r.commands | ForEach-Object { Invoke-Command1 $_ })
    if ($results.Count -eq 0) { break }
  }
  Update-AgentIfNeeded $Conf $latest
}

if ($Uninstall) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Remove-Item $ConfDir -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host 'TSS MDM agent removed.'
  return
}

if ($Token) {
  if (-not $Server) { throw '-Server is required with -Token' }
  $r = Invoke-Mdm '/api/agent/enroll' @{ token = $Token; platform = 'windows'; inventory = (Get-Inventory) } $null
  $conf = [ordered]@{ server = $Server.TrimEnd('/'); device_id = $r.device_id; device_secret = $r.device_secret }
  Save-Conf $conf
  Write-Host "Enrolled in TSS MDM. Device ID $($r.device_id)" -ForegroundColor Green
  if ($Install) {
    $dest = Join-Path $ConfDir 'tss-agent.ps1'
    # Make sure the agent file exists at $dest. When this ran in-memory (from the .exe) there is
    # no source file, so fetch it from the server instead.
    if ($PSCommandPath -and (Test-Path $PSCommandPath) -and ($PSCommandPath -ne $dest)) {
      Copy-Item $PSCommandPath $dest -Force
    } elseif (-not (Test-Path $dest)) {
      Invoke-WebRequest -UseBasicParsing "$($conf.server)/downloads/tss-agent.ps1" -OutFile $dest
    }
    # Run the agent IN-MEMORY (script block from file content), so execution-policy / script-blocking
    # never stops the scheduled task.
    $inner = "& ([scriptblock]::Create((Get-Content -Raw '$dest'))) -Once"
    $action  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$inner`""
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $boot    = New-ScheduledTaskTrigger -AtStartup
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger, $boot -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
    Write-Host "Installed scheduled task '$TaskName' (checks in every minute)." -ForegroundColor Green
  }
  return
}

if (-not (Test-Path $ConfFile)) { throw 'Not enrolled. Run with -Server and -Token.' }
$conf = Get-Content $ConfFile -Raw | ConvertFrom-Json
do {
  try { Invoke-Checkin $conf }
  catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -in 401, 410) { Write-Warning 'Device retired from MDM; removing agent.'; & $PSCommandPath -Uninstall; return }
    Write-Warning "Check-in failed: $_"
  }
  if (-not $Once) { Start-Sleep -Seconds 300 }
} while (-not $Once)
