async function req(path, method = "GET", payload = null) {
  const options = { method, headers: { "Content-Type": "application/json" } };
  if (payload) options.body = JSON.stringify(payload);
  const res = await fetch(path, options);
  return res.json();
}

function setState(el, ok, warn = false) {
  el.classList.remove("ok", "bad", "warn");
  if (warn) {
    el.classList.add("warn");
    el.textContent = "RETRY";
  } else if (ok) {
    el.classList.add("ok");
    el.textContent = "OK";
  } else {
    el.classList.add("bad");
    el.textContent = "BAD";
  }
}

function fillConfig(cfg) {
  const form = document.getElementById("config_form");
  Object.keys(cfg).forEach((k) => {
    const el = form.elements[k];
    if (!el) return;
    if (el.type === "checkbox") {
      el.checked = !!cfg[k];
    } else {
      el.value = cfg[k];
    }
  });
}

function getConfigFromForm() {
  const form = document.getElementById("config_form");
  const payload = {};
  Array.from(form.elements).forEach((el) => {
    if (!el.name) return;
    if (el.type === "checkbox") {
      payload[el.name] = !!el.checked;
    } else {
      payload[el.name] = el.value;
    }
  });
  return payload;
}

async function pollStatus() {
  try {
    const data = await req("/api/status");
    if (!data.ok) return;
    const st = data.data.status;
    const logs = data.data.logs;

    document.getElementById("timestamp").textContent = st.timestamp || "-";

    setState(document.getElementById("pproxy_state"), !!st.pproxy.running);
    setState(document.getElementById("autossh_state"), !!st.autossh.running);
    setState(document.getElementById("port_state"), !!st.pproxy.port_ok);
    setState(document.getElementById("ssh_state"), !!st.network.ssh_ok);

    const retrying = !!st.autossh.running && !st.autossh.remote_port_ok;
    setState(document.getElementById("remote_state"), !!st.autossh.remote_port_ok, retrying);

    document.getElementById("log_supervisor").textContent = logs.supervisor || "";
    document.getElementById("log_pproxy").textContent = logs.pproxy || "";
    document.getElementById("log_autossh").textContent = logs.autossh || "";
  } catch (e) {
    document.getElementById("action_msg").textContent = "poll error: " + String(e);
  }
}

async function loadConfig() {
  const data = await req("/api/config");
  if (!data.ok) return;
  fillConfig(data.data.config || {});
}

async function doAction(action) {
  const map = {
    start: "/api/start",
    stop: "/api/stop",
    restart: "/api/restart",
    restart_pproxy: "/api/restart/pproxy",
    restart_autossh: "/api/restart/autossh",
    test: "/api/test",
  };
  const path = map[action];
  if (!path) return;
  const data = await req(path, "POST");
  document.getElementById("action_msg").textContent = data.message || "done";
}

document.querySelectorAll("button[data-action]").forEach((btn) => {
  btn.addEventListener("click", () => doAction(btn.dataset.action));
});

document.getElementById("save_cfg").addEventListener("click", async () => {
  const payload = getConfigFromForm();
  const data = await req("/api/config", "POST", payload);
  document.getElementById("cfg_msg").textContent = data.message || "saved";
});

document.getElementById("save_apply_cfg").addEventListener("click", async () => {
  const payload = getConfigFromForm();
  payload.apply_now = true;
  const data = await req("/api/config", "POST", payload);
  document.getElementById("cfg_msg").textContent = data.message || "saved and applied";
});

(async () => {
  await loadConfig();
  await pollStatus();
  setInterval(pollStatus, 4000);
})();