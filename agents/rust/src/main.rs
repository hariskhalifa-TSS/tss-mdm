// TSS MDM agent.
//
// One executable, two jobs:
//   * double-clicked  -> a small window that asks for an enrollment key, enrols this
//                        PC and installs itself as a background scheduled task.
//   * --service       -> the background task: long-polls the server so it idles at
//                        ~0% CPU and commands arrive within ~1s.
//
// The server address is baked in at build time (MDM_SERVER_URL), which is what lets
// enrollment be nothing but a key.
#![cfg_attr(windows, windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::PathBuf;
use std::process::Command;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

#[cfg(windows)]
mod gui;

const VERSION: &str = env!("CARGO_PKG_VERSION");
const DEFAULT_SERVER: &str = "https://tss-mdm-production.up.railway.app";
const INVENTORY_EVERY: Duration = Duration::from_secs(300); // full inventory at most every 5 min
const LONGPOLL_WAIT: u32 = 25; // seconds the server holds a check-in open
const TASK_NAME: &str = "TSS MDM Agent";

/// The MDM server this build talks to. Set MDM_SERVER_URL at build time to change it.
pub fn server_url() -> String {
    option_env!("MDM_SERVER_URL")
        .unwrap_or(DEFAULT_SERVER)
        .trim()
        .trim_end_matches('/')
        .to_string()
}

/// Host part of the server URL, for showing in the window.
pub fn server_host() -> String {
    let s = server_url();
    s.split("://").nth(1).unwrap_or(&s).to_string()
}

// ---------------- enrollment keys ----------------
// Mirrors server/app/enroll_key.py: Crockford base32, so the characters people
// actually mistype (1/I/L, 0/O) can be corrected instead of rejected.
const ALPHABET: &str = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const PREFIX: &str = "TSSM";
const BODY_LEN: usize = 8;

pub fn normalize_key(raw: &str) -> Option<String> {
    let mut s: String = raw
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| match c.to_ascii_uppercase() {
            'O' => '0',
            'I' | 'L' => '1',
            'U' => 'V',
            other => other,
        })
        .collect();
    // Only strip the prefix when the length says it really is one.
    if s.len() == PREFIX.len() + BODY_LEN && s.starts_with(PREFIX) {
        s = s[PREFIX.len()..].to_string();
    }
    if s.len() != BODY_LEN || !s.chars().all(|c| ALPHABET.contains(c)) {
        return None;
    }
    Some(format!("{}-{}-{}", PREFIX, &s[..4], &s[4..]))
}

// ---------------- config ----------------
#[derive(Serialize, Deserialize, Default)]
pub struct Conf {
    server: String,
    device_id: String,
    device_secret: String,
}

fn conf_dir() -> PathBuf {
    if cfg!(windows) {
        let base = std::env::var("ProgramData").unwrap_or_else(|_| "C:\\ProgramData".into());
        PathBuf::from(base).join("TSSMDM")
    } else {
        PathBuf::from("/etc/tss-mdm")
    }
}
fn conf_path() -> PathBuf {
    conf_dir().join("agent.json")
}

pub fn load_conf() -> Option<Conf> {
    std::fs::read_to_string(conf_path())
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
}
fn save_conf(c: &Conf) -> std::io::Result<()> {
    std::fs::create_dir_all(conf_dir())?;
    std::fs::write(conf_path(), serde_json::to_string_pretty(c)?)
}

// ---------------- hardware identity ----------------
// Collected once per process: these never change while the machine is running, and
// `hardware_id` is what stops a re-run of the installer creating a duplicate device.
pub struct HwFacts {
    pub hardware_id: String,
    pub serial: String,
    pub manufacturer: String,
    pub model: String,
}

fn hw_facts() -> &'static HwFacts {
    static CELL: OnceLock<HwFacts> = OnceLock::new();
    CELL.get_or_init(collect_hw_facts)
}

/// Some OEMs ship a placeholder SMBIOS UUID; treat those as no id at all.
fn usable_id(s: &str) -> bool {
    let t = s.trim().trim_matches('"');
    let stripped: String = t.chars().filter(|c| *c != '-' && *c != '0' && *c != ' ').collect();
    t.len() >= 8 && !stripped.is_empty() && !t.eq_ignore_ascii_case("FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF")
}

#[cfg(windows)]
fn collect_hw_facts() -> HwFacts {
    let ps = "$ErrorActionPreference='SilentlyContinue';\
              $p=Get-CimInstance Win32_ComputerSystemProduct;\
              $c=Get-CimInstance Win32_ComputerSystem;\
              $b=Get-CimInstance Win32_BIOS;\
              Write-Output \"$($p.UUID)|$($b.SerialNumber)|$($c.Manufacturer)|$($c.Model)\"";
    let (_, out) = run("powershell.exe",
        &["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps]);
    let parts: Vec<String> = out.trim().split('|').map(|s| s.trim().to_string()).collect();
    let get = |i: usize| parts.get(i).cloned().unwrap_or_default();

    let mut hardware_id = get(0);
    if !usable_id(&hardware_id) {
        // Fall back to the Windows machine GUID, which is always present.
        let (_, reg) = run("reg.exe",
            &["query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"]);
        hardware_id = reg
            .lines()
            .find(|l| l.contains("MachineGuid"))
            .and_then(|l| l.split_whitespace().last())
            .unwrap_or("")
            .to_string();
    }
    HwFacts { hardware_id, serial: get(1), manufacturer: get(2), model: get(3) }
}

#[cfg(target_os = "macos")]
fn collect_hw_facts() -> HwFacts {
    let (_, out) = run("ioreg", &["-rd1", "-c", "IOPlatformExpertDevice"]);
    let field = |key: &str| -> String {
        out.lines()
            .find(|l| l.contains(key))
            .and_then(|l| l.split('=').nth(1))
            .map(|v| v.trim().trim_matches('"').to_string())
            .unwrap_or_default()
    };
    let (_, model) = run("sysctl", &["-n", "hw.model"]);
    let uuid = field("IOPlatformUUID");
    let serial = field("IOPlatformSerialNumber");
    HwFacts {
        hardware_id: if usable_id(&uuid) { uuid } else { serial.clone() },
        serial,
        manufacturer: "Apple".into(),
        model: model.trim().to_string(),
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
fn collect_hw_facts() -> HwFacts {
    let read = |p: &str| std::fs::read_to_string(p).unwrap_or_default().trim().to_string();
    let mut hardware_id = read("/etc/machine-id");
    if !usable_id(&hardware_id) {
        hardware_id = read("/sys/class/dmi/id/product_uuid");
    }
    HwFacts {
        hardware_id,
        serial: read("/sys/class/dmi/id/product_serial"),
        manufacturer: read("/sys/class/dmi/id/sys_vendor"),
        model: read("/sys/class/dmi/id/product_name"),
    }
}

// ---------------- inventory ----------------
fn collect_inventory(full: bool) -> Value {
    let mut inv = json!({ "agent_version": format!("{}-rust", VERSION) });
    // Hostname and hardware id are cheap and cached; always send them. hardware_id in
    // particular must be on every enrollment or the server can't recognise the device.
    inv["name"] = json!(sysinfo::System::host_name().unwrap_or_else(|| "unknown".into()));
    let hw = hw_facts();
    if !hw.hardware_id.is_empty() {
        inv["hardware_id"] = json!(hw.hardware_id);
    }
    if !full {
        return inv;
    }

    use sysinfo::{Disks, System};
    let mut sys = System::new();
    sys.refresh_memory();
    sys.refresh_cpu_all();
    inv["os_version"] = json!(format!("{} {}",
        System::name().unwrap_or_default(), System::os_version().unwrap_or_default()).trim());
    inv["ram_total_gb"] = json!((sys.total_memory() as f64 / 1e9 * 10.0).round() / 10.0);
    if let Some(cpu) = sys.cpus().first() {
        inv["cpu"] = json!(cpu.brand().trim());
    }
    let disks = Disks::new_with_refreshed_list();
    if let Some(d) = disks.list().iter().max_by_key(|d| d.total_space()) {
        inv["storage_total_gb"] = json!((d.total_space() as f64 / 1e9 * 10.0).round() / 10.0);
        inv["storage_free_gb"] = json!((d.available_space() as f64 / 1e9 * 10.0).round() / 10.0);
    }
    if !hw.serial.is_empty() {
        inv["serial_number"] = json!(hw.serial);
    }
    if !hw.manufacturer.is_empty() {
        inv["manufacturer"] = json!(hw.manufacturer);
    }
    if !hw.model.is_empty() {
        inv["model"] = json!(hw.model);
    }
    inv["platform_arch"] = json!(std::env::consts::ARCH);
    inv
}

// ---------------- HTTP ----------------
/// One shared HTTP agent, built on the OS TLS stack (see Cargo.toml).
fn http() -> &'static ureq::Agent {
    static AGENT: OnceLock<ureq::Agent> = OnceLock::new();
    AGENT.get_or_init(|| {
        let mut b = ureq::AgentBuilder::new().user_agent(&format!("tss-agent/{VERSION}"));
        if let Ok(c) = native_tls::TlsConnector::new() {
            b = b.tls_connector(std::sync::Arc::new(c));
        }
        b.build()
    })
}

fn platform() -> &'static str {
    if cfg!(windows) {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

/// Turn a transport/HTTP failure into something a non-technical user can act on.
fn friendly_error(e: ureq::Error) -> String {
    match e {
        ureq::Error::Status(code, resp) => {
            let detail = resp
                .into_json::<Value>()
                .ok()
                .and_then(|v| v["detail"].as_str().map(str::to_string))
                .unwrap_or_default();
            if !detail.is_empty() {
                detail
            } else if code == 429 {
                "Too many attempts. Wait a few minutes and try again.".into()
            } else {
                format!("The server rejected this enrollment (error {code}).")
            }
        }
        _ => format!("Could not reach {}. Check this PC's internet connection.", server_host()),
    }
}

fn enroll(server: &str, key: &str) -> Result<Conf, String> {
    let url = format!("{}/api/agent/enroll", server.trim_end_matches('/'));
    let body = json!({ "token": key, "platform": platform(), "inventory": collect_inventory(true) });
    let resp = http()
        .post(&url)
        .timeout(Duration::from_secs(60))
        .send_json(body)
        .map_err(friendly_error)?;
    let v: Value = resp
        .into_json()
        .map_err(|e| format!("The server sent back something unexpected: {e}"))?;
    let device_id = v["device_id"].as_str().unwrap_or_default().to_string();
    let device_secret = v["device_secret"].as_str().unwrap_or_default().to_string();
    if device_id.is_empty() || device_secret.is_empty() {
        return Err("The server did not return device credentials.".into());
    }
    Ok(Conf { server: server.trim_end_matches('/').to_string(), device_id, device_secret })
}

fn checkin(conf: &Conf, inventory: Option<Value>, results: Vec<Value>) -> Result<Value, u16> {
    let url = format!("{}/api/agent/checkin?wait={}", conf.server, LONGPOLL_WAIT);
    let auth = format!("Device {}:{}", conf.device_id, conf.device_secret);
    let mut body = json!({ "command_results": results });
    if let Some(inv) = inventory {
        body["inventory"] = inv;
    }
    match http()
        .post(&url)
        .set("Authorization", &auth)
        .timeout(Duration::from_secs((LONGPOLL_WAIT + 15) as u64))
        .send_json(body)
    {
        Ok(resp) => resp.into_json::<Value>().map_err(|_| 0u16),
        Err(ureq::Error::Status(code, _)) => Err(code),
        Err(_) => Err(0), // network error
    }
}

// ---------------- command execution ----------------
fn run(cmd: &str, args: &[&str]) -> (bool, String) {
    let mut c = Command::new(cmd);
    c.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        c.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: never flash a console
    }
    match c.output() {
        Ok(o) => {
            let mut s = String::from_utf8_lossy(&o.stdout).to_string();
            s.push_str(&String::from_utf8_lossy(&o.stderr));
            (o.status.success(), s)
        }
        Err(e) => (false, format!("{e}")),
    }
}

fn execute(cmd: &Value) -> Value {
    let id = cmd["id"].clone();
    let name = cmd["command"].as_str().unwrap_or("");
    let payload = &cmd["payload"];
    let (status, result): (&str, String) = match name {
        "refresh_inventory" => ("succeeded", "inventory will be sent".into()),
        "lock" => {
            if cfg!(windows) {
                let (ok, o) = run("rundll32.exe", &["user32.dll,LockWorkStation"]);
                (if ok { "succeeded" } else { "failed" }, o)
            } else {
                ("failed", "lock not supported on this OS".into())
            }
        }
        "reboot" | "shutdown" => {
            let delay = payload["delay_seconds"].as_i64().unwrap_or(60).to_string();
            if cfg!(windows) {
                let flag = if name == "reboot" { "/r" } else { "/s" };
                let (ok, o) = run("shutdown.exe",
                    &[flag, "/t", &delay, "/c", "Your IT team scheduled this action."]);
                (if ok { "succeeded" } else { "failed" }, o)
            } else {
                let arg = if name == "reboot" { "-r" } else { "-h" };
                let (ok, o) = run("sh", &["-c", &format!("shutdown {arg} +1")]);
                (if ok { "succeeded" } else { "failed" }, o)
            }
        }
        "send_message" => {
            let title = payload["title"].as_str().unwrap_or("IT");
            let bodytext = payload["body"].as_str().unwrap_or("");
            let text = format!("{title}: {bodytext}");
            if cfg!(windows) {
                let (ok, o) = run("msg.exe", &["*", "/TIME:300", &text]);
                (if ok { "succeeded" } else { "failed" }, o)
            } else {
                println!("[message] {text}");
                ("succeeded", "logged".into())
            }
        }
        "run_script" => {
            let script = payload["script"].as_str().unwrap_or("");
            let shell = payload["shell"].as_str().unwrap_or("powershell");
            let (ok, mut out) = if cfg!(windows) {
                if shell == "cmd" {
                    run("cmd.exe", &["/c", script])
                } else {
                    run("powershell.exe",
                        &["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
                }
            } else {
                run("bash", &["-c", script])
            };
            if out.len() > 3900 {
                out.truncate(3900);
                out.push_str("\n...(truncated)");
            }
            if out.is_empty() {
                out = "(no output)".into();
            }
            (if ok { "succeeded" } else { "failed" }, out)
        }
        other => ("failed", format!("'{other}' not supported by this agent")),
    };
    json!({ "id": id, "status": status, "result": result })
}

// ---------------- background service loop ----------------
fn run_loop(conf: Conf) {
    let mut results: Vec<Value> = Vec::new();
    let mut last_inventory = Instant::now()
        .checked_sub(INVENTORY_EVERY)
        .unwrap_or_else(Instant::now); // send inventory on first poll
    let mut send_full_next = true;

    loop {
        let inv = if send_full_next || last_inventory.elapsed() >= INVENTORY_EVERY {
            send_full_next = false;
            last_inventory = Instant::now();
            Some(collect_inventory(true))
        } else {
            Some(collect_inventory(false)) // tiny heartbeat (hostname + id + version)
        };

        match checkin(&conf, inv, std::mem::take(&mut results)) {
            Ok(resp) => {
                if let Some(cmds) = resp["commands"].as_array() {
                    for c in cmds {
                        if c["command"] == "refresh_inventory" {
                            send_full_next = true;
                        }
                        results.push(execute(c));
                    }
                }
                // If we ran commands, report immediately with a fresh poll (no sleep).
                if !results.is_empty() {
                    continue;
                }
            }
            Err(401) | Err(410) => {
                // Retired or deleted server-side. Stop; the scheduled task will try again
                // later, so the device recovers on its own if it is re-enrolled.
                return;
            }
            Err(_) => std::thread::sleep(Duration::from_secs(5)),
        }
        // Long-poll already waited server-side; a tiny gap avoids a hot loop on empty returns.
        std::thread::sleep(Duration::from_millis(500));
    }
}

// ---------------- install ----------------
/// Scheduled-task definition. Registered from XML rather than plain `schtasks /Create`
/// so we can set the three things that decide whether this keeps working:
///   ExecutionTimeLimit PT0S   - otherwise Windows kills the agent after 72 hours
///   MultipleInstancesPolicy   - IgnoreNew, so the repeating trigger never doubles it up
///   a 5-minute repeating trigger - restarts the agent if it ever dies, without a reboot
fn task_xml(exe: &str) -> String {
    let exe = exe.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;");
    format!(
        r#"<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Keeps this device enrolled in TSS MDM.</Description>
    <URI>\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled></BootTrigger>
    <TimeTrigger>
      <StartBoundary>2024-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <Arguments>--service</Arguments>
    </Exec>
  </Actions>
</Task>"#
    )
}

/// schtasks only reliably reads UTF-16LE task XML.
fn write_utf16(path: &PathBuf, text: &str) -> std::io::Result<()> {
    let mut bytes = vec![0xFF, 0xFE]; // BOM
    for unit in text.encode_utf16() {
        bytes.extend_from_slice(&unit.to_le_bytes());
    }
    std::fs::write(path, bytes)
}

fn install() -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let dir = conf_dir();
    std::fs::create_dir_all(&dir).map_err(|e| format!("could not create {}: {e}", dir.display()))?;
    let dest = dir.join("tss-agent.exe");

    if exe != dest {
        // A previously installed agent may be holding the destination open.
        let mut copied = std::fs::copy(&exe, &dest).map(|_| ()).map_err(|e| e.to_string());
        if copied.is_err() {
            let _ = run("schtasks.exe", &["/End", "/TN", TASK_NAME]);
            for _ in 0..5 {
                std::thread::sleep(Duration::from_millis(400));
                copied = std::fs::copy(&exe, &dest).map(|_| ()).map_err(|e| e.to_string());
                if copied.is_ok() {
                    break;
                }
            }
        }
        // If the copy still fails but a usable agent is already there, carry on with it.
        if let Err(e) = copied {
            if !dest.exists() {
                return Err(format!("could not install the agent to {}: {e}", dest.display()));
            }
        }
    }

    if !cfg!(windows) {
        return Ok(()); // macOS/Linux: run the binary from your own service manager
    }

    let xml_path = dir.join("task.xml");
    write_utf16(&xml_path, &task_xml(&dest.to_string_lossy()))
        .map_err(|e| format!("could not write the task definition: {e}"))?;
    let (ok, out) = run("schtasks.exe",
        &["/Create", "/TN", TASK_NAME, "/XML", &xml_path.to_string_lossy(), "/F"]);
    let _ = std::fs::remove_file(&xml_path);
    if !ok {
        return Err(format!("could not register the background task: {}", out.trim()));
    }
    let (started, out) = run("schtasks.exe", &["/Run", "/TN", TASK_NAME]);
    if !started {
        return Err(format!("the background task was created but would not start: {}", out.trim()));
    }
    Ok(())
}

fn uninstall() {
    if cfg!(windows) {
        let _ = run("schtasks.exe", &["/End", "/TN", TASK_NAME]);
        let _ = run("schtasks.exe", &["/Delete", "/TN", TASK_NAME, "/F"]);
    }
    let _ = std::fs::remove_file(conf_path());
    let _ = std::fs::remove_dir_all(conf_dir());
}

/// The whole enrollment, as the window (and `--key`) sees it.
pub fn enroll_and_install(key: &str) -> Result<String, String> {
    let conf = enroll(&server_url(), key)?;
    let device_id = conf.device_id.clone();
    save_conf(&conf).map_err(|e| format!("Enrolled, but could not save the config on this PC: {e}"))?;
    install()?;
    Ok(device_id)
}

// ---------------- entry point ----------------
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let get = |flag: &str| -> Option<String> {
        args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned()
    };
    let has = |flag: &str| args.iter().any(|a| a == flag);

    if has("--version") {
        attach_console();
        println!("tss-agent {VERSION} -> {}", server_url());
        return;
    }
    if has("--uninstall") {
        uninstall();
        return;
    }
    // The scheduled task runs this; it must never show a window.
    if has("--service") {
        if let Some(conf) = load_conf() {
            run_loop(conf);
        }
        return;
    }
    if has("--once") {
        attach_console();
        match load_conf() {
            Some(conf) => {
                let r = checkin(&conf, Some(collect_inventory(true)), vec![]);
                println!("check-in: {}", if r.is_ok() { "ok" } else { "failed" });
            }
            None => println!("not enrolled"),
        }
        return;
    }

    // Headless enrollment, for mass deployment (GPO / Intune / a login script).
    if let Some(raw) = get("--key").or_else(|| get("--token")) {
        attach_console();
        let Some(key) = normalize_key(&raw) else {
            println!("'{raw}' is not an enrollment key. Keys look like TSSM-4K7P-9XQ2.");
            std::process::exit(2);
        };
        match enroll_and_install(&key) {
            Ok(id) => println!("Enrolled. Device {id}"),
            Err(e) => {
                println!("Enrollment failed: {e}");
                std::process::exit(1);
            }
        }
        return;
    }

    // No arguments: somebody double-clicked it. Ask for the key.
    #[cfg(windows)]
    gui::run();
    #[cfg(not(windows))]
    console_enroll();
}

/// On Windows this is a GUI-subsystem binary, so it has no console of its own.
/// Borrow the calling cmd window's one when there is one, for the --key/--once paths.
fn attach_console() {
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::System::Console::{AttachConsole, ATTACH_PARENT_PROCESS};
        AttachConsole(ATTACH_PARENT_PROCESS);
    }
}

#[cfg(not(windows))]
fn console_enroll() {
    use std::io::Write;
    println!("\n  TSS MDM - enrol this device");
    println!("  Server: {}\n", server_host());
    print!("  Enrollment key: ");
    let _ = std::io::stdout().flush();
    let mut line = String::new();
    if std::io::stdin().read_line(&mut line).is_err() {
        return;
    }
    let Some(key) = normalize_key(&line) else {
        println!("\n  That is not an enrollment key. Keys look like TSSM-4K7P-9XQ2.");
        std::process::exit(2);
    };
    println!("\n  Enrolling...");
    match enroll_and_install(&key) {
        Ok(id) => println!("  Done. This device is now managed by TSS MDM (device {id})."),
        Err(e) => {
            println!("  {e}");
            std::process::exit(1);
        }
    }
}
