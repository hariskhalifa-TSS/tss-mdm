/* TSS MDM admin console — dependency-free SPA */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ROLES = ["viewer", "technician", "admin", "super_admin"];
  const ROLE_LABEL = { viewer: "Viewer", technician: "Technician", admin: "Admin", super_admin: "Super Admin" };
  const PLATFORMS = { android: "Android", ios: "iOS", ipados: "iPadOS", macos: "macOS", windows: "Windows", linux: "Linux" };
  const ICON = { android: "🤖", ios: "📱", ipados: "📱", macos: "💻", windows: "🪟", linux: "🐧" };

  const state = { token: null, user: null };
  try { state.token = localStorage.getItem("mdm_token"); } catch (_) {}

  // ---------- helpers ----------
  function toast(msg, err = false) {
    const t = $("#toast"); t.textContent = msg; t.className = "show" + (err ? " err" : "");
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = ""), 3200);
  }
  async function api(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (state.token) headers.Authorization = "Bearer " + state.token;
    if (opts.body && typeof opts.body !== "string") { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.body); }
    const res = await fetch(path, { ...opts, headers });
    if (res.status === 401 && !path.includes("/auth/login")) { logout(); throw new Error("Session expired"); }
    if (opts.raw) { if (!res.ok) throw new Error("Request failed"); return res; }
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) {
      let d = data && data.detail; if (Array.isArray(d)) d = d.map((x) => x.msg).join("; ");
      throw new Error(d || `Request failed (${res.status})`);
    }
    return data;
  }
  const can = (role) => state.user && ROLES.indexOf(state.user.role) >= ROLES.indexOf(role);
  function ago(iso) {
    if (!iso) return "never";
    const s = (Date.now() - new Date(iso)) / 1000;
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago";
  }
  const dt = (iso) => (iso ? new Date(iso).toLocaleString() : "—");
  const gb = (v) => (v == null ? "—" : `${(+v).toFixed(1)} GB`);
  const yesno = (v, good = true) => v == null ? '<span class="muted">Unknown</span>' : `<span class="badge ${v === good ? "ok" : "bad"}">${v ? "Yes" : "No"}</span>`;
  const onlineBadge = (d) => d.status === "retired" ? '<span class="badge">Retired</span>'
    : d.online ? '<span class="badge ok"><span class="dot"></span>Online</span>' : '<span class="badge"><span class="dot"></span>Offline</span>';
  const battery = (v) => v == null ? "—" : `<span class="badge ${v < 20 ? "bad" : v < 50 ? "warn" : "ok"}">${Math.round(v)}%</span>`;
  function modal(html) { $("#modal-box").innerHTML = html; $("#modal").classList.remove("hidden"); return $("#modal-box"); }
  function closeModal() { $("#modal").classList.add("hidden"); $("#modal-box").innerHTML = ""; }
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.dataset.close !== undefined) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  const formData = (form) => Object.fromEntries(new FormData(form).entries());
  async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Copied"); } catch (_) { toast("Copy failed", true); } }

  function setToken(t) { state.token = t; try { t ? localStorage.setItem("mdm_token", t) : localStorage.removeItem("mdm_token"); } catch (_) {} }
  function logout() { setToken(null); state.user = null; location.hash = "#/login"; render(); }

  // ---------- shell ----------
  const NAV = [
    ["dashboard", "Dashboard", "▦", "viewer"], ["devices", "Devices", "▭", "viewer"], ["enrollment", "Enrollment", "⊕", "technician"],
    ["groups", "Groups", "◫", "viewer"], ["users", "Admin users", "☺", "super_admin"], ["audit", "Audit log", "☰", "admin"], ["account", "My account", "⚙", "viewer"],
  ];
  function shell(active, title, actions = "") {
    $("#app").innerHTML = `<div class="shell">
      <aside class="side" id="side"><div class="brand"><div class="logo"><span></span></div>TSS MDM</div>
        <nav class="nav">${NAV.filter((n) => can(n[3])).map((n) => `<a href="#/${n[0]}" class="${active === n[0] ? "active" : ""}"><span class="ico">${n[2]}</span>${n[1]}</a>`).join("")}</nav>
        <div class="me"><b>${esc(state.user.name)}</b>${esc(state.user.email)}<br><span class="badge red" style="margin-top:6px">${ROLE_LABEL[state.user.role]}</span>
          <div style="margin-top:10px"><button class="btn sm" id="logout">Sign out</button></div></div></aside>
      <main class="main"><div class="topbar"><div class="row"><button class="btn sm menu-btn" id="menu">☰</button><h1>${esc(title)}</h1></div><div class="row">${actions}</div></div><div id="page"></div></main></div>`;
    $("#logout").onclick = logout;
    $("#menu").onclick = () => $("#side").classList.toggle("open");
    return $("#page");
  }

  // ---------- auth pages ----------
  function loginPage() {
    $("#app").innerHTML = `<div class="auth"><form class="card" id="f">
      <div class="brand"><div class="logo"><span></span></div>TSS MDM</div>
      <h2>Sign in to the admin console</h2>
      <div class="field"><label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required></div>
      <div class="field"><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required></div>
      <div class="error" id="err"></div><button class="btn primary" style="width:100%;justify-content:center">Sign in</button>
      <p class="muted small" style="margin-bottom:0">The Souled Store · Device Management</p></form></div>`;
    $("#f").onsubmit = async (e) => {
      e.preventDefault(); $("#err").textContent = "";
      try {
        const r = await api("/api/auth/login", { method: "POST", body: formData(e.target) });
        setToken(r.access_token); state.user = r.user; location.hash = "#/dashboard"; render();
      } catch (err) { $("#err").textContent = err.message; }
    };
    $("#email").focus();
  }

  function changePasswordForm(forced) {
    return `<form id="pw" class="${forced ? "" : "card"}" style="max-width:420px">
      ${forced ? "" : "<h2>Change password</h2>"}
      <div class="field"><label>Current password</label><input name="current_password" type="password" autocomplete="current-password" required></div>
      <div class="field"><label>New password</label><input name="new_password" type="password" autocomplete="new-password" required minlength="12"></div>
      <div class="field"><label>Confirm new password</label><input name="confirm" type="password" autocomplete="new-password" required></div>
      <p class="muted small">At least 12 characters with upper & lower case, a number and a symbol.</p>
      <div class="error" id="pwerr"></div><button class="btn primary">Update password</button></form>`;
  }
  function bindPasswordForm(after) {
    $("#pw").onsubmit = async (e) => {
      e.preventDefault(); const d = formData(e.target); $("#pwerr").textContent = "";
      if (d.new_password !== d.confirm) { $("#pwerr").textContent = "Passwords don't match"; return; }
      try {
        const r = await api("/api/auth/change-password", { method: "POST", body: { current_password: d.current_password, new_password: d.new_password } });
        setToken(r.access_token); state.user = r.user; toast("Password updated"); after();
      } catch (err) { $("#pwerr").textContent = err.message; }
    };
  }
  function forcePasswordPage() {
    $("#app").innerHTML = `<div class="auth"><div class="card"><div class="brand"><div class="logo"><span></span></div>TSS MDM</div>
      <h2>Set a new password</h2><p class="muted">Welcome, ${esc(state.user.name)}. For security, choose your own password before continuing.</p>
      ${changePasswordForm(true)}<p><a href="#" id="out" class="small">Sign out</a></p></div></div>`;
    bindPasswordForm(() => { location.hash = "#/dashboard"; render(); });
    $("#out").onclick = (e) => { e.preventDefault(); logout(); };
  }

  // ---------- dashboard ----------
  async function dashboardPage() {
    const page = shell("dashboard", "Dashboard", can("technician") ? '<a class="btn primary" href="#/enrollment">+ Enroll devices</a>' : "");
    const d = await api("/api/dashboard");
    const max = Math.max(1, ...Object.values(d.by_platform));
    const emax = Math.max(1, ...d.enrollments_14d.map((x) => x.count));
    page.innerHTML = `<div class="grid g4">
        <div class="card stat"><div class="label">Managed devices</div><div class="value">${d.total}</div></div>
        <div class="card stat"><div class="label">Online</div><div class="value" style="color:var(--ok)">${d.online}</div></div>
        <div class="card stat"><div class="label">Offline</div><div class="value">${d.offline}</div></div>
        <div class="card stat"><div class="label">Retired</div><div class="value muted">${d.retired}</div></div></div>
      <div class="grid g3" style="margin-top:16px">
        <div class="card"><h2>Devices by platform</h2><div class="bars">${Object.keys(PLATFORMS).map((p) => `<div class="bar"><span>${PLATFORMS[p]}</span><div class="track"><div class="fill" style="width:${((d.by_platform[p] || 0) / max) * 100}%"></div></div><b>${d.by_platform[p] || 0}</b></div>`).join("")}</div></div>
        <div class="card"><h2>Enrollments · last 14 days</h2><div class="spark">${d.enrollments_14d.map((x) => `<div class="col" title="${x.date}: ${x.count}"><div class="b" style="height:${(x.count / emax) * 100}%"></div><span class="t">${x.date.slice(8)}</span></div>`).join("")}</div></div>
        <div class="card"><h2>Attention needed</h2>
          <div class="bars"><div class="bar"><span>Low battery</span><span class="muted small">&lt; 20%</span><b>${d.alerts.low_battery}</b></div>
          <div class="bar"><span>Low storage</span><span class="muted small">&lt; 5 GB free</span><b>${d.alerts.low_storage}</b></div>
          <div class="bar"><span>Unencrypted</span><span class="muted small">disk / storage</span><b>${d.alerts.unencrypted}</b></div></div>
          <p class="muted small">Ownership: ${d.by_ownership.corporate || 0} corporate · ${d.by_ownership.byod || 0} BYOD</p></div></div>
      ${d.total === 0 ? `<div class="card empty" style="margin-top:16px">No devices yet. ${can("technician") ? '<a href="#/enrollment">Create an enrollment token</a> to add your first device.' : ""}</div>` : ""}`;
  }

  // ---------- devices ----------
  const devState = { page: 1, q: "", platform: "", status: "active", online: "", group_id: "" };
  async function devicesPage() {
    const page = shell("devices", "Devices", '<button class="btn" id="export">⇩ Export CSV</button>');
    const groups = await api("/api/groups");
    page.innerHTML = `<div class="card"><form class="filters" id="flt">
        <input name="q" placeholder="Search name, serial, model, user, IP" value="${esc(devState.q)}">
        <select name="platform"><option value="">All platforms</option>${Object.entries(PLATFORMS).map(([k, v]) => `<option value="${k}" ${devState.platform === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <select name="online"><option value="">Any connectivity</option><option value="true" ${devState.online === "true" ? "selected" : ""}>Online</option><option value="false" ${devState.online === "false" ? "selected" : ""}>Offline</option></select>
        <select name="group_id"><option value="">All groups</option>${groups.map((g) => `<option value="${g.id}" ${String(devState.group_id) === String(g.id) ? "selected" : ""}>${esc(g.name)}</option>`).join("")}</select>
        <select name="status"><option value="active" ${devState.status === "active" ? "selected" : ""}>Active</option><option value="retired" ${devState.status === "retired" ? "selected" : ""}>Retired</option><option value="" ${devState.status === "" ? "selected" : ""}>All statuses</option></select>
        <button class="btn">Filter</button></form><div id="list"></div></div>`;
    const params = () => { const p = new URLSearchParams(); Object.entries(devState).forEach(([k, v]) => v !== "" && k !== "page" && p.set(k, v)); return p; };
    async function load() {
      const p = params(); p.set("page", devState.page); p.set("page_size", 25);
      const r = await api("/api/devices?" + p);
      const pages = Math.max(1, Math.ceil(r.total / 25));
      $("#list").innerHTML = r.items.length ? `<div class="table-wrap"><table><thead><tr><th>Device</th><th>Platform</th><th>Model</th><th>User</th><th>Group</th><th>Battery</th><th>Storage free</th><th>Status</th><th>Last seen</th></tr></thead><tbody>
        ${r.items.map((d) => `<tr class="click" data-id="${esc(d.id)}"><td><b>${esc(d.name)}</b><div class="muted small mono">${esc(d.serial_number || "—")}</div></td>
          <td>${ICON[d.platform] || ""} ${PLATFORMS[d.platform] || esc(d.platform)} <span class="muted">${esc(d.os_version)}</span></td>
          <td>${esc([d.manufacturer, d.model].filter(Boolean).join(" ") || "—")}</td><td>${esc(d.assigned_user || "—")}</td><td>${esc(d.group_name || "—")}</td>
          <td>${battery(d.battery_level)}</td><td>${gb(d.storage_free_gb)}</td><td>${onlineBadge(d)}</td><td title="${esc(dt(d.last_seen_at))}">${ago(d.last_seen_at)}</td></tr>`).join("")}
        </tbody></table></div><div class="pager"><span class="muted">${r.total} device${r.total === 1 ? "" : "s"}</span><div class="row">
        <button class="btn sm" id="prev" ${devState.page <= 1 ? "disabled" : ""}>‹ Prev</button><span class="muted small">Page ${devState.page} / ${pages}</span>
        <button class="btn sm" id="next" ${devState.page >= pages ? "disabled" : ""}>Next ›</button></div></div>`
        : '<div class="empty">No devices match these filters.</div>';
      $$("tr[data-id]").forEach((tr) => (tr.onclick = () => (location.hash = "#/devices/" + tr.dataset.id)));
      if ($("#prev")) { $("#prev").onclick = () => { devState.page--; load(); }; $("#next").onclick = () => { devState.page++; load(); }; }
    }
    $("#flt").onsubmit = (e) => { e.preventDefault(); Object.assign(devState, formData(e.target), { page: 1 }); load(); };
    $("#export").onclick = async () => {
      const res = await api("/api/devices/export.csv?" + params(), { raw: true });
      const a = document.createElement("a"); a.href = URL.createObjectURL(await res.blob()); a.download = "tss-devices.csv"; a.click();
    };
    await load();
  }

  async function deviceDetailPage(id) {
    const d = await api("/api/devices/" + encodeURIComponent(id));
    const actions = [
      can("technician") && d.status === "active" ? '<button class="btn" id="sync">⟳ Refresh inventory</button><button class="btn" id="msg">✉ Send message</button><button class="btn" id="lock">🔒 Lock</button><button class="btn" id="edit">✎ Edit</button>' : "",
      can("admin") && d.status === "active" ? '<button class="btn" id="reboot">↻ Reboot</button><button class="btn" id="shutdown">⏻ Shut down</button><button class="btn danger" id="retire">Retire</button>' : "",
      can("admin") ? '<button class="btn danger" id="del">Delete</button>' : "",
    ].join("");
    const page = shell("devices", d.name, actions);
    const used = d.storage_total_gb && d.storage_free_gb != null ? ((d.storage_total_gb - d.storage_free_gb) / d.storage_total_gb) * 100 : null;
    page.innerHTML = `<p><a href="#/devices">‹ All devices</a> &nbsp; ${onlineBadge(d)} <span class="muted">Last seen ${ago(d.last_seen_at)}</span></p>
      <div class="tabs"><button class="active" data-tab="overview">Overview</button><button data-tab="apps">Apps (${d.installed_apps.length})</button><button data-tab="commands">Commands</button><button data-tab="activity">Activity</button></div>
      <div data-pane="overview"><div class="grid g2">
        <div class="card"><h2>Device</h2><dl class="kv"><dt>Platform</dt><dd>${ICON[d.platform] || ""} ${PLATFORMS[d.platform] || esc(d.platform)} ${esc(d.os_version)}</dd>
          <dt>Manufacturer</dt><dd>${esc(d.manufacturer || "—")}</dd><dt>Model</dt><dd>${esc(d.model || "—")}</dd>
          <dt>Serial number</dt><dd class="mono">${esc(d.serial_number || "—")}</dd><dt>Hardware ID</dt><dd class="mono">${esc(d.hardware_id || "—")}</dd>
          ${d.imei ? `<dt>IMEI</dt><dd class="mono">${esc(d.imei)}</dd>` : ""}<dt>Device ID</dt><dd class="mono">${esc(d.id)}</dd><dt>Agent</dt><dd>${esc(d.agent_version || "—")}</dd></dl></div>
        <div class="card"><h2>Assignment</h2><dl class="kv"><dt>Assigned user</dt><dd>${esc(d.assigned_user || "—")}</dd><dt>Group</dt><dd>${esc(d.group_name || "—")}</dd>
          <dt>Ownership</dt><dd>${d.ownership === "byod" ? "BYOD (personal)" : "Corporate"}</dd><dt>Status</dt><dd>${esc(d.status)}</dd>
          <dt>Enrolled</dt><dd>${dt(d.enrolled_at)}</dd><dt>Notes</dt><dd>${esc(d.notes || "—")}</dd></dl></div>
        <div class="card"><h2>Hardware</h2><dl class="kv"><dt>CPU</dt><dd>${esc(d.cpu || "—")}</dd><dt>Memory</dt><dd>${gb(d.ram_total_gb)}</dd>
          <dt>Storage</dt><dd>${gb(d.storage_free_gb)} free of ${gb(d.storage_total_gb)}${used != null ? `<div class="bars"><div class="track" style="margin-top:6px"><div class="fill" style="width:${used}%"></div></div></div>` : ""}</dd>
          <dt>Battery</dt><dd>${battery(d.battery_level)} ${d.is_charging ? "⚡ charging" : ""}</dd></dl></div>
        <div class="card"><h2>Network & location</h2><dl class="kv"><dt>IP address</dt><dd class="mono">${esc(d.ip_address || "—")}</dd><dt>Public IP</dt><dd class="mono">${esc(d.extra.public_ip || "—")}</dd>
          <dt>MAC address</dt><dd class="mono">${esc(d.mac_address || "—")}</dd><dt>Wi-Fi</dt><dd>${esc(d.wifi_ssid || "—")}</dd>
          <dt>Location</dt><dd>${d.latitude != null ? `${d.latitude.toFixed(5)}, ${d.longitude.toFixed(5)} · <a target="_blank" rel="noopener" href="https://www.openstreetmap.org/?mlat=${d.latitude}&mlon=${d.longitude}#map=16/${d.latitude}/${d.longitude}">Open map</a>` : "—"}</dd></dl></div>
        <div class="card"><h2>Security</h2><dl class="kv"><dt>Encrypted</dt><dd>${yesno(d.is_encrypted)}</dd><dt>Passcode set</dt><dd>${yesno(d.passcode_set)}</dd><dt>Rooted / jailbroken</dt><dd>${yesno(d.is_rooted, false)}</dd></dl></div>
        ${Object.keys(d.extra).filter((k) => k !== "public_ip").length ? `<div class="card"><h2>Other details</h2><dl class="kv">${Object.entries(d.extra).filter(([k]) => k !== "public_ip").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("")}</dl></div>` : ""}
      </div></div>
      <div data-pane="apps" class="hidden card">${d.installed_apps.length ? `<input id="appq" placeholder="Search apps" style="margin-bottom:10px"><div class="table-wrap"><table><thead><tr><th>Name</th><th>Version</th><th>Identifier / publisher</th></tr></thead><tbody id="apps">${d.installed_apps.map((a) => `<tr><td>${esc(a.name)}</td><td>${esc(a.version || "")}</td><td class="muted">${esc(a.id || a.publisher || "")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">The agent hasn\'t reported installed apps.</div>'}</div>
      <div data-pane="commands" class="hidden card">${d.commands.length ? `<div class="table-wrap"><table><thead><tr><th>Command</th><th>Status</th><th>By</th><th>Queued</th><th>Result</th></tr></thead><tbody>${d.commands.map((c) => `<tr><td>${esc(c.command)}</td><td><span class="badge ${c.status === "succeeded" ? "ok" : c.status === "failed" ? "bad" : "warn"}">${esc(c.status)}</span></td><td>${esc(c.created_by)}</td><td>${dt(c.created_at)}</td><td class="muted">${esc(c.result)}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">No commands sent to this device yet.</div>'}</div>
      <div data-pane="activity" class="hidden card">${d.activity.length ? `<table><tbody>${d.activity.map((a) => `<tr><td>${dt(a.at)}</td><td><b>${esc(a.action)}</b></td><td>${esc(a.actor)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">No activity.</div>'}</div>`;
    $$(".tabs button").forEach((b) => (b.onclick = () => { $$(".tabs button").forEach((x) => x.classList.toggle("active", x === b)); $$("[data-pane]").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== b.dataset.tab)); }));
    if ($("#appq")) $("#appq").oninput = (e) => { const q = e.target.value.toLowerCase(); $$("#apps tr").forEach((tr) => (tr.style.display = tr.textContent.toLowerCase().includes(q) ? "" : "none")); };
    const send = async (command, payload = {}) => { await api(`/api/devices/${d.id}/commands`, { method: "POST", body: { command, payload } }); toast("Queued — the device picks it up at next check-in"); deviceDetailPage(id); };
    if ($("#sync")) $("#sync").onclick = () => send("refresh_inventory");
    if ($("#lock")) $("#lock").onclick = () => confirmBox(`Lock ${d.name}?`, "The device's screen locks at its next check-in; the user must sign in again.", "Lock", () => send("lock"));
    if ($("#reboot")) $("#reboot").onclick = () => confirmBox(`Reboot ${d.name}?`, "The device restarts (with a 2-minute warning to the user) at its next check-in.", "Reboot", () => send("reboot"));
    if ($("#shutdown")) $("#shutdown").onclick = () => confirmBox(`Shut down ${d.name}?`, "The device powers off (with a 2-minute warning to the user) at its next check-in. You'll need physical access to turn it back on.", "Shut down", () => send("shutdown"));
    if ($("#msg")) $("#msg").onclick = () => {
      const m = modal(`<h2>Send message to ${esc(d.name)}</h2><form id="mf"><div class="field"><label>Title</label><input name="title" value="Message from IT" required></div><div class="field"><label>Message</label><textarea name="body" rows="3" required></textarea></div><div class="row"><button class="btn primary">Send</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
      $("#mf", m).onsubmit = async (e) => { e.preventDefault(); closeModal(); await send("send_message", formData(e.target)); };
    };
    if ($("#edit")) $("#edit").onclick = async () => {
      const groups = await api("/api/groups");
      const m = modal(`<h2>Edit device</h2><form id="ef"><div class="field"><label>Name</label><input name="name" value="${esc(d.name)}" required></div>
        <div class="field"><label>Assigned user (email)</label><input name="assigned_user" value="${esc(d.assigned_user || "")}"></div>
        <div class="field"><label>Group</label><select name="group_id"><option value="">No group</option>${groups.map((g) => `<option value="${g.id}" ${g.id === d.group_id ? "selected" : ""}>${esc(g.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Ownership</label><select name="ownership"><option value="corporate">Corporate</option><option value="byod" ${d.ownership === "byod" ? "selected" : ""}>BYOD</option></select></div>
        <div class="field"><label>Notes</label><textarea name="notes" rows="3">${esc(d.notes)}</textarea></div><div class="error" id="eerr"></div>
        <div class="row"><button class="btn primary">Save</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
      $("#ef", m).onsubmit = async (e) => {
        e.preventDefault(); const f = formData(e.target);
        try { await api("/api/devices/" + d.id, { method: "PATCH", body: { ...f, group_id: f.group_id ? +f.group_id : null, assigned_user: f.assigned_user || null } }); closeModal(); toast("Saved"); deviceDetailPage(id); }
        catch (err) { $("#eerr").textContent = err.message; }
      };
    };
    if ($("#retire")) $("#retire").onclick = () => confirmBox(`Retire ${d.name}?`, "The device's agent credentials stop working and it leaves the active inventory. It must re-enroll to be managed again.", "Retire", async () => { await api(`/api/devices/${d.id}/retire`, { method: "POST" }); toast("Device retired"); deviceDetailPage(id); });
    if ($("#del")) $("#del").onclick = () => confirmBox(`Delete ${d.name}?`, "This permanently removes the device record and its command history. The audit log keeps a record of the deletion.", "Delete", async () => { await api("/api/devices/" + d.id, { method: "DELETE" }); toast("Device deleted"); location.hash = "#/devices"; });
  }

  function confirmBox(title, text, label, fn) {
    const m = modal(`<h2>${esc(title)}</h2><p class="muted">${esc(text)}</p><div class="row"><button class="btn primary" id="ok">${esc(label)}</button><button class="btn" data-close>Cancel</button></div>`);
    $("#ok", m).onclick = async () => { closeModal(); try { await fn(); } catch (e) { toast(e.message, true); } };
  }

  // ---------- enrollment ----------
  async function enrollmentPage() {
    const page = shell("enrollment", "Enrollment", '<button class="btn primary" id="new">+ New enrollment token</button>');
    const [tokens, groups] = await Promise.all([api("/api/enrollment/tokens"), api("/api/groups")]);
    page.innerHTML = `<div class="card" style="margin-bottom:16px"><h2>How enrollment works</h2><p class="muted" style="margin:0">Create a token, then open its QR code or link on the device. Windows, macOS and Linux devices install the TSS agent with one command. Each device gets its own credential and checks in every few minutes with its inventory. Android and Apple native enrollment come in the next phase (see the plan).</p></div>
      <div class="card">${tokens.length ? `<div class="table-wrap"><table><thead><tr><th>Name</th><th>Platform</th><th>Ownership</th><th>Group</th><th>Uses</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody>
      ${tokens.map((t) => `<tr><td><b>${esc(t.name)}</b><div class="muted small">by ${esc(t.created_by)}</div></td><td>${t.platform ? PLATFORMS[t.platform] : "Any"}</td><td>${t.ownership === "byod" ? "BYOD" : "Corporate"}</td><td>${esc(t.group_name || "—")}</td>
        <td>${t.uses}${t.max_uses ? " / " + t.max_uses : ""}</td><td>${t.expires_at ? new Date(t.expires_at).toLocaleDateString() : "Never"}</td>
        <td>${t.usable ? '<span class="badge ok">Active</span>' : t.revoked ? '<span class="badge bad">Revoked</span>' : '<span class="badge">Expired / used</span>'}</td>
        <td class="row"><button class="btn sm" data-show="${t.id}">QR & install</button>${can("admin") && !t.revoked ? `<button class="btn sm danger" data-revoke="${t.id}">Revoke</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">No enrollment tokens yet.</div>'}</div>`;
    $("#new").onclick = () => {
      const m = modal(`<h2>New enrollment token</h2><form id="tf"><div class="field"><label>Name</label><input name="name" placeholder="e.g. Mumbai warehouse laptops" required></div>
        <div class="grid g2"><div class="field"><label>Platform</label><select name="platform"><option value="">Any platform</option>${Object.entries(PLATFORMS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
        <div class="field"><label>Ownership</label><select name="ownership"><option value="corporate">Corporate</option><option value="byod">BYOD</option></select></div></div>
        <div class="field"><label>Put devices in group</label><select name="group_id"><option value="">No group</option>${groups.map((g) => `<option value="${g.id}">${esc(g.name)}</option>`).join("")}</select></div>
        <div class="field"><label>Assign to user (optional email)</label><input name="assigned_user" type="email"></div>
        <div class="grid g2"><div class="field"><label>Max devices (blank = unlimited)</label><input name="max_uses" type="number" min="1"></div>
        <div class="field"><label>Expires in (days)</label><input name="expires_in_days" type="number" min="1" max="365" value="30"></div></div>
        <div class="error" id="terr"></div><div class="row"><button class="btn primary">Create</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
      $("#tf", m).onsubmit = async (e) => {
        e.preventDefault(); const f = formData(e.target);
        const body = { name: f.name, ownership: f.ownership, platform: f.platform || null, group_id: f.group_id ? +f.group_id : null, assigned_user: f.assigned_user || null, max_uses: f.max_uses ? +f.max_uses : null, expires_in_days: f.expires_in_days ? +f.expires_in_days : null };
        try { const t = await api("/api/enrollment/tokens", { method: "POST", body }); await enrollmentPage(); showToken(t); }
        catch (err) { $("#terr").textContent = err.message; }
      };
    };
    $$("[data-show]").forEach((b) => (b.onclick = () => showToken(tokens.find((t) => t.id === +b.dataset.show))));
    $$("[data-revoke]").forEach((b) => (b.onclick = () => confirmBox("Revoke this token?", "Devices already enrolled stay managed. New devices can't use this token.", "Revoke", async () => { await api(`/api/enrollment/tokens/${b.dataset.revoke}/revoke`, { method: "POST" }); toast("Token revoked"); enrollmentPage(); })));
  }

  async function showToken(t) {
    const origin = location.origin;
    const ps = `powershell -ExecutionPolicy Bypass -Command "iwr ${origin}/downloads/tss-agent.ps1 -OutFile $env:TEMP\\tss-agent.ps1; & $env:TEMP\\tss-agent.ps1 -Server ${origin} -Token ${t.token} -Install"`;
    const py = `curl -fsSLO ${origin}/downloads/tss_agent.py && sudo python3 tss_agent.py --server ${origin} --token ${t.token} --install`;
    const m = modal(`<h2>${esc(t.name)}</h2>
      <div class="callout" style="margin-bottom:14px"><b>Easiest:</b> download the installer and run it on the device (IT, on a company-owned PC). It enrols and installs the background agent in one double-click.</div>
      <div class="row" style="margin-bottom:6px"><button class="btn primary" data-dl="windows">⇩ Windows installer</button><button class="btn" data-dl="macos">⇩ macOS installer</button></div>
      <p class="muted small">Windows: double-click the .bat and approve the admin prompt. macOS: right-click the .command → Open.</p>
      <details style="margin-top:12px"><summary class="btn sm" style="display:inline-block">Other ways: QR code & manual commands</summary>
      <div style="text-align:center;margin-top:12px"><img id="qr" alt="Enrollment QR code" width="200" height="200" style="background:#fff;border-radius:8px"></div>
      <p class="muted small" style="text-align:center">Scan on the device to open the enrollment page.</p>
      <label>Enrollment link</label><div class="row" style="flex-wrap:nowrap"><input readonly value="${esc(t.enroll_url)}"><button class="btn sm" data-copy="${esc(t.enroll_url)}">Copy</button></div>
      <h3 style="margin-top:14px">Windows (PowerShell as Administrator)</h3><code class="block mono">${esc(ps)}</code><button class="btn sm" data-copy="${esc(ps)}" style="margin-top:6px">Copy</button>
      <h3 style="margin-top:14px">macOS / Linux</h3><code class="block mono">${esc(py)}</code><button class="btn sm" data-copy="${esc(py)}" style="margin-top:6px">Copy</button></details>
      <div class="row" style="margin-top:16px"><button class="btn" data-close>Close</button></div>`);
    $$("[data-copy]", m).forEach((b) => (b.onclick = () => copy(b.dataset.copy)));
    $$("[data-dl]", m).forEach((b) => (b.onclick = async () => {
      try {
        const res = await api(`/api/enrollment/tokens/${t.id}/installer?os=${b.dataset.dl}`, { raw: true });
        const cd = res.headers.get("Content-Disposition") || "";
        const fn = (cd.match(/filename="?([^"]+)"?/) || [])[1] || `TSS-MDM-Setup.${b.dataset.dl === "windows" ? "bat" : "command"}`;
        const a = document.createElement("a"); a.href = URL.createObjectURL(await res.blob()); a.download = fn; a.click();
        toast("Installer downloaded — run it on the device");
      } catch (e) { toast(e.message, true); }
    }));
    const res = await api(`/api/enrollment/tokens/${t.id}/qr`, { raw: true });
    const img = $("#qr", m); if (img) img.src = URL.createObjectURL(await res.blob());
  }

  // ---------- groups ----------
  async function groupsPage() {
    const page = shell("groups", "Device groups", can("admin") ? '<button class="btn primary" id="new">+ New group</button>' : "");
    const groups = await api("/api/groups");
    page.innerHTML = `<div class="card">${groups.length ? `<table><thead><tr><th>Name</th><th>Description</th><th>Devices</th><th></th></tr></thead><tbody>${groups.map((g) => `<tr><td><b>${esc(g.name)}</b></td><td class="muted">${esc(g.description || "—")}</td><td><a href="#/devices" data-group="${g.id}">${g.device_count}</a></td><td>${can("admin") ? `<button class="btn sm danger" data-del="${g.id}">Delete</button>` : ""}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">No groups yet. Groups let you organise devices, e.g. by store, warehouse or department.</div>'}</div>`;
    $$("[data-group]").forEach((a) => (a.onclick = () => Object.assign(devState, { group_id: a.dataset.group, page: 1 })));
    if ($("#new")) $("#new").onclick = () => {
      const m = modal(`<h2>New group</h2><form id="gf"><div class="field"><label>Name</label><input name="name" placeholder="e.g. Store – Bandra" required></div><div class="field"><label>Description</label><input name="description"></div><div class="error" id="gerr"></div><div class="row"><button class="btn primary">Create</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
      $("#gf", m).onsubmit = async (e) => { e.preventDefault(); try { await api("/api/groups", { method: "POST", body: formData(e.target) }); closeModal(); toast("Group created"); groupsPage(); } catch (err) { $("#gerr").textContent = err.message; } };
    };
    $$("[data-del]").forEach((b) => (b.onclick = () => confirmBox("Delete this group?", "Devices in it stay enrolled and become ungrouped.", "Delete", async () => { await api("/api/groups/" + b.dataset.del, { method: "DELETE" }); groupsPage(); })));
  }

  // ---------- admin users ----------
  async function usersPage() {
    const page = shell("users", "Admin users", '<button class="btn primary" id="new">+ Invite admin</button>');
    const users = await api("/api/users");
    page.innerHTML = `<div class="card" style="margin-bottom:16px"><h2>Roles</h2><div class="grid g4 small">
        <div><b>Viewer</b><div class="muted">Read-only: dashboard, devices, groups.</div></div><div><b>Technician</b><div class="muted">+ enroll devices, edit devices, send commands.</div></div>
        <div><b>Admin</b><div class="muted">+ groups, retire/delete devices, revoke tokens, audit log.</div></div><div><b>Super Admin</b><div class="muted">+ manage admin users and roles.</div></div></div></div>
      <div class="card"><div class="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last login</th><th></th></tr></thead><tbody>
      ${users.map((u) => `<tr><td><b>${esc(u.name)}</b>${u.id === state.user.id ? ' <span class="muted small">(you)</span>' : ""}</td><td>${esc(u.email)}</td>
        <td><select data-role="${u.id}" ${u.id === state.user.id ? "disabled" : ""}>${ROLES.map((r) => `<option value="${r}" ${u.role === r ? "selected" : ""}>${ROLE_LABEL[r]}</option>`).join("")}</select></td>
        <td>${!u.is_active ? '<span class="badge bad">Disabled</span>' : u.locked ? '<span class="badge warn">Locked</span>' : u.must_change_password ? '<span class="badge warn">Pending first login</span>' : '<span class="badge ok">Active</span>'}</td>
        <td>${ago(u.last_login_at)}</td><td class="row">${u.id === state.user.id ? "" : `<button class="btn sm" data-reset="${u.id}">Reset password</button><button class="btn sm" data-toggle="${u.id}" data-active="${u.is_active}">${u.is_active ? "Disable" : "Enable"}</button><button class="btn sm danger" data-del="${u.id}">Delete</button>`}</td></tr>`).join("")}</tbody></table></div></div>`;
    const showPw = (email, pw) => { const m = modal(`<h2>Temporary password</h2><p>Share this securely with <b>${esc(email)}</b>. They'll be asked to change it at first sign-in. It won't be shown again.</p><code class="block mono">${esc(pw)}</code><div class="row" style="margin-top:12px"><button class="btn" id="cp">Copy</button><button class="btn primary" data-close>Done</button></div>`); $("#cp", m).onclick = () => copy(pw); };
    $("#new").onclick = () => {
      const m = modal(`<h2>Invite admin</h2><form id="uf"><div class="field"><label>Name</label><input name="name" required></div><div class="field"><label>Email</label><input name="email" type="email" required></div>
        <div class="field"><label>Role</label><select name="role">${ROLES.map((r) => `<option value="${r}" ${r === "technician" ? "selected" : ""}>${ROLE_LABEL[r]}</option>`).join("")}</select></div>
        <div class="error" id="uerr"></div><div class="row"><button class="btn primary">Create</button><button type="button" class="btn" data-close>Cancel</button></div></form>`);
      $("#uf", m).onsubmit = async (e) => { e.preventDefault(); try { const r = await api("/api/users", { method: "POST", body: formData(e.target) }); await usersPage(); showPw(r.user.email, r.temporary_password); } catch (err) { $("#uerr").textContent = err.message; } };
    };
    $$("[data-role]").forEach((s) => (s.onchange = async () => { try { await api("/api/users/" + s.dataset.role, { method: "PATCH", body: { role: s.value } }); toast("Role updated"); } catch (e) { toast(e.message, true); usersPage(); } }));
    $$("[data-reset]").forEach((b) => (b.onclick = async () => { const u = users.find((x) => x.id === +b.dataset.reset); const r = await api(`/api/users/${u.id}/reset-password`, { method: "POST" }); showPw(u.email, r.temporary_password); usersPage(); }));
    $$("[data-toggle]").forEach((b) => (b.onclick = async () => { try { await api("/api/users/" + b.dataset.toggle, { method: "PATCH", body: { is_active: b.dataset.active !== "true" } }); usersPage(); } catch (e) { toast(e.message, true); } }));
    $$("[data-del]").forEach((b) => (b.onclick = () => confirmBox("Delete this admin?", "They lose access immediately.", "Delete", async () => { await api("/api/users/" + b.dataset.del, { method: "DELETE" }); usersPage(); })));
  }

  // ---------- audit ----------
  async function auditPage() {
    const page = shell("audit", "Audit log");
    let p = 1;
    page.innerHTML = `<div class="card"><form class="row" id="af" style="margin-bottom:12px"><input name="actor" placeholder="Actor (email or device)" style="max-width:260px"><input name="action" placeholder="Action prefix, e.g. device." style="max-width:220px"><button class="btn">Filter</button></form><div id="al"></div></div>`;
    let f = {};
    async function load() {
      const q = new URLSearchParams({ page: p, page_size: 50, ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)) });
      const r = await api("/api/audit?" + q);
      $("#al").innerHTML = `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Details</th><th>IP</th></tr></thead><tbody>${r.items.map((a) => `<tr><td>${dt(a.at)}</td><td>${esc(a.actor)}</td><td><b>${esc(a.action)}</b></td><td class="muted small">${esc(a.target_type)} ${esc(a.target_id)}</td><td class="muted small mono" style="white-space:normal;max-width:360px">${esc(JSON.stringify(a.details))}</td><td class="mono">${esc(a.ip)}</td></tr>`).join("")}</tbody></table></div>
        <div class="pager"><span class="muted">${r.total} events</span><div class="row"><button class="btn sm" id="ap" ${p <= 1 ? "disabled" : ""}>‹ Prev</button><button class="btn sm" id="an" ${p * 50 >= r.total ? "disabled" : ""}>Next ›</button></div></div>`;
      $("#ap").onclick = () => { p--; load(); }; $("#an").onclick = () => { p++; load(); };
    }
    $("#af").onsubmit = (e) => { e.preventDefault(); f = formData(e.target); p = 1; load(); };
    await load();
  }

  // ---------- account ----------
  function accountPage() {
    const page = shell("account", "My account");
    page.innerHTML = `<div class="grid g2"><div class="card"><h2>Profile</h2><dl class="kv"><dt>Name</dt><dd>${esc(state.user.name)}</dd><dt>Email</dt><dd>${esc(state.user.email)}</dd><dt>Role</dt><dd>${ROLE_LABEL[state.user.role]}</dd><dt>Last login</dt><dd>${dt(state.user.last_login_at)}</dd></dl>
      <p><button class="btn" id="la">Sign out of all sessions</button></p></div>${changePasswordForm(false)}</div>`;
    bindPasswordForm(() => accountPage());
    $("#la").onclick = async () => { await api("/api/auth/logout-all", { method: "POST" }); logout(); };
  }

  // ---------- router ----------
  async function render() {
    closeModal();
    const hash = location.hash.slice(2) || "dashboard";
    if (!state.token) return loginPage();
    if (!state.user) { try { state.user = await api("/api/auth/me"); } catch (_) { return; } }
    if (state.user.must_change_password) return forcePasswordPage();
    const [route, arg] = hash.split("/");
    const pages = { dashboard: dashboardPage, devices: arg ? () => deviceDetailPage(arg) : devicesPage, enrollment: enrollmentPage, groups: groupsPage, users: usersPage, audit: auditPage, account: accountPage, login: dashboardPage };
    try { await (pages[route] || dashboardPage)(); }
    catch (e) { const p = $("#page"); if (p) p.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; else toast(e.message, true); }
  }
  window.addEventListener("hashchange", render);
  render();
})();
