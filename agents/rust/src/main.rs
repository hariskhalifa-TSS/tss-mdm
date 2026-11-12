// TSS MDM agent (Rust). Tiny native agent: long-polls the server so it idles at ~0% CPU
// and commands arrive within ~1s. Cross-platform; Windows is the primary target.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::Write;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, Instant};

const VERSION: &str = env!("CARGO_PKG_VERSION");
const INVENTORY_EVERY: Duration = Duration::from_secs(300); // full inventory at most every 5 min
const LONGPOLL_WAIT: u32 = 25; // seconds the server holds a check-in open

#[derive(Serialize, Deserialize, Default)]
struct Conf {
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
fn conf_path() -> PathBuf { conf_dir().join("agent.json") }

fn load_conf() -> Option<Conf> {
    std::fs::read_to_string(conf_path()).ok().and_then(|s| serde_json::from_str(&s).ok())
}
fn save_conf(c: &Conf) -> std::io::Result<()> {
    std::fs::create_dir_all(conf_dir())?;
    std::fs::write(conf_path(), serde_json::to_string_pretty(c)?)
}

// ---------------- inventory ----------------
fn collect_inventory(full: bool) -> Value {
    let mut inv = json!({ "agent_version": format!("{}-rust", VERSION) });
    // Hostname is cheap; always send it.
    if let Ok(h) = hostname() { inv["name"] = json!(h); }
    if !full { return inv; }

    use sysinfo::{Disks, System};
    let mut sys = System::new();
    sys.refresh_memory();
    sys.refresh_cpu_all();
    inv["os_version"] = json!(format!("{} {}",
        System::name().unwrap_or_default(), System::os_version().unwrap_or_default()));
    inv["ram_total_gb"] = json!((sys.total_memory() as f64 / 1e9 * 10.0).round() / 10.0);
    if let Some(cpu) = sys.cpus().first() { inv["cpu"] = json!(cpu.brand().trim()); }
    let disks = Disks::new_with_refreshed_list();
    if let Some(d) = disks.list().iter().max_by_key(|d| d.total_space()) {
        inv["storage_total_gb"] = json!((d.total_space() as f64 / 1e9 * 10.0).round() / 10.0);
        inv["storage_free_gb"] = json!((d.available_space() as f64 / 1e9 * 10.0).round() / 10.0);
    }
    inv["platform_arch"] = json!(std::env::consts::ARCH);
    inv
}

fn hostname() -> std::io::Result<String> {
    Ok(sysinfo::System::host_name().unwrap_or_else(|| "unknown".into()))
}

// ---------------- HTTP ----------------
fn enroll(server: &str, token: &str) -> Result<Conf, String> {
    let url = format!("{}/api/agent/enroll", server.trim_end_matches('/'));
    let body = json!({ "token": token, "platform": platform(), "inventory": collect_inventory(true) });
    let resp = ureq::post(&url).send_json(body).map_err(|e| format!("enroll request failed: {e}"))?;
    let v: Value = resp.into_json().map_err(|e| format!("bad enroll response: {e}"))?;
    Ok(Conf {
        server: server.trim_end_matches('/').to_string(),
        device_id: v["device_id"].as_str().unwrap_or_default().to_string(),
        device_secret: v["device_secret"].as_str().unwrap_or_default().to_string(),
    })
}

fn platform() -> &'static str {
    if cfg!(windows) { "windows" } else if cfg!(target_os = "macos") { "macos" } else { "linux" }
}

fn checkin(conf: &Conf, inventory: Option<Value>, results: Vec<Value>) -> Result<Value, u16> {
    let url = format!("{}/api/agent/checkin?wait={}", conf.server, LONGPOLL_WAIT);
    let auth = format!("Device {}:{}", conf.device_id, conf.device_secret);
    let mut body = json!({ "command_results": results });
    if let Some(inv) = inventory { body["inventory"] = inv; }
    match ureq::post(&url)
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
    match Command::new(cmd).args(args).output() {
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
            } else { ("failed", "lock not supported on this OS".into()) }
        }
        "reboot" | "shutdown" => {
            let delay = payload["delay_seconds"].as_i64().unwrap_or(60).to_string();
            if cfg!(windows) {
                let flag = if name == "reboot" { "/r" } else { "/s" };
                let (ok, o) = run("shutdown.exe", &[flag, "/t", &delay,
                    "/c", "Your IT team scheduled this action."]);
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
                if shell == "cmd" { run("cmd.exe", &["/c", script]) }
                else { run("powershell.exe", &["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]) }
            } else {
                run("bash", &["-c", script])
            };
            if out.len() > 3900 { out.truncate(3900); out.push_str("\n...(truncated)"); }
            if out.is_empty() { out = "(no output)".into(); }
            (if ok { "succeeded" } else { "failed" }, out)
        }
        other => ("failed", format!("'{other}' not supported by this agent")),
    };
    json!({ "id": id, "status": status, "result": result })
}

// ---------------- main loop ----------------
fn run_loop(conf: Conf) {
    let mut results: Vec<Value> = Vec::new();
    let mut last_inventory = Instant::now()
        .checked_sub(INVENTORY_EVERY).unwrap_or_else(Instant::now); // send inventory on first poll
    let mut send_full_next = true;

    loop {
        let inv = if send_full_next || last_inventory.elapsed() >= INVENTORY_EVERY {
            send_full_next = false;
            last_inventory = Instant::now();
            Some(collect_inventory(true))
        } else {
            Some(collect_inventory(false)) // tiny heartbeat (hostname + version only)
        };

        match checkin(&conf, inv, std::mem::take(&mut results)) {
            Ok(resp) => {
                if let Some(cmds) = resp["commands"].as_array() {
                    for c in cmds {
                        if c["command"] == "refresh_inventory" { send_full_next = true; }
                        results.push(execute(c));
                    }
                }
                // If we ran commands, report immediately with a fresh poll (no sleep).
                if !results.is_empty() { continue; }
            }
            Err(401) | Err(410) => {
                eprintln!("Device retired or removed; stopping.");
                return;
            }
            Err(code) => {
                eprintln!("check-in failed ({code}); retrying shortly");
                std::thread::sleep(Duration::from_secs(5));
            }
        }
        // Long-poll already waited server-side; a tiny gap avoids a hot loop on empty returns.
        std::thread::sleep(Duration::from_millis(500));
    }
}

// ---------------- install (Windows scheduled task, SYSTEM, persistent) ----------------
fn install() -> std::io::Result<()> {
    let exe = std::env::current_exe()?;
    let dest = conf_dir().join("tss-agent.exe");
    std::fs::create_dir_all(conf_dir())?;
    if exe != dest { std::fs::copy(&exe, &dest)?; }
    if cfg!(windows) {
        let dest_s = dest.to_string_lossy().to_string();
        // Persistent task as SYSTEM, starts at boot and now, restarts if it ever stops.
        let _ = run("schtasks.exe", &["/Create", "/TN", "TSS MDM Agent", "/TR",
            &format!("\"{}\"", dest_s), "/SC", "ONSTART", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"]);
        // Also start it right now in the background.
        #[cfg(windows)]
        { let _ = Command::new(&dest).spawn(); }
        println!("Installed scheduled task 'TSS MDM Agent' (persistent).");
    } else {
        println!("Install as a service manually on this OS, or run the binary at startup.");
    }
    Ok(())
}

fn uninstall() {
    if cfg!(windows) {
        let _ = run("schtasks.exe", &["/Delete", "/TN", "TSS MDM Agent", "/F"]);
    }
    let _ = std::fs::remove_dir_all(conf_dir());
    println!("TSS MDM agent removed.");
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let get = |flag: &str| -> Option<String> {
        args.iter().position(|a| a == flag).and_then(|i| args.get(i + 1)).cloned()
    };
    let has = |flag: &str| args.iter().any(|a| a == flag);

    if has("--uninstall") { uninstall(); return; }

    if let Some(token) = get("--token") {
        let server = get("--server").unwrap_or_default();
        if server.is_empty() { eprintln!("--server is required with --token"); std::process::exit(1); }
        match enroll(&server, &token) {
            Ok(conf) => {
                if let Err(e) = save_conf(&conf) { eprintln!("could not save config: {e}"); std::process::exit(1); }
                println!("Enrolled. Device ID {}", conf.device_id);
                if has("--install") {
                    if let Err(e) = install() { eprintln!("install failed: {e}"); std::process::exit(1); }
                    return;
                }
                if has("--once") { let _ = checkin(&conf, Some(collect_inventory(true)), vec![]); return; }
                run_loop(conf);
            }
            Err(e) => { eprintln!("Enrollment failed: {e}"); std::process::exit(1); }
        }
        return;
    }

    match load_conf() {
        Some(conf) => {
            if has("--once") {
                let _ = checkin(&conf, Some(collect_inventory(true)), vec![]);
                let _ = std::io::stdout().flush();
            } else {
                run_loop(conf);
            }
        }
        None => { eprintln!("Not enrolled. Run with --server and --token."); std::process::exit(1); }
    }
}
