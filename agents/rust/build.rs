// Embeds the application manifest straight through the MSVC linker, so the build
// needs no extra crates or resource compiler.
//
// The manifest is what makes double-clicking the .exe prompt for admin (enrollment
// writes to ProgramData and registers a SYSTEM task) and gives the window modern
// controls and crisp text on high-DPI screens.
fn main() {
    println!("cargo:rerun-if-env-changed=MDM_SERVER_URL");

    let windows = std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows");
    let msvc = std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc");
    if !(windows && msvc) {
        return;
    }

    let dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let manifest = std::path::Path::new(&dir).join("tss-agent.manifest");
    println!("cargo:rerun-if-changed={}", manifest.display());
    println!("cargo:rustc-link-arg-bins=/MANIFEST:EMBED");
    println!("cargo:rustc-link-arg-bins=/MANIFESTINPUT:{}", manifest.display());
    // The manifest carries its own <requestedExecutionLevel>; :NO stops the linker
    // generating a competing asInvoker one.
    println!("cargo:rustc-link-arg-bins=/MANIFESTUAC:NO");
}
