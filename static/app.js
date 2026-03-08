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

function autoResizeTextarea(el) {
  if (!el) return;
  const maxHeight = 300;
  el.style.height = "auto";
  const next = Math.min(el.scrollHeight, maxHeight);
  el.style.height = `${next}px`;
}

async function doRemoteUpload() {
  const pathEl = document.getElementById("upload_path");
  const contentEl = document.getElementById("upload_content");
  const msgEl = document.getElementById("upload_msg");
  const path = (pathEl.value || "").trim();
  const content = contentEl.value || "";
  const bytes = new TextEncoder().encode(content).length;

  if (!path) {
    msgEl.textContent = "path required";
    return;
  }
  if (bytes > 20 * 1024) {
    msgEl.textContent = "content too large (max 20KB)";
    return;
  }

  msgEl.textContent = "sending...";
  const data = await req("/api/remote/upload", "POST", { path, content });
  msgEl.textContent = data.message || "done";
}

function setUpdateState(updateAvailable) {
  const el = document.getElementById("upd_state");
  el.classList.remove("ok", "bad", "warn");
  if (updateAvailable) {
    el.classList.add("warn");
    el.textContent = "UPDATE AVAILABLE";
  } else {
    el.classList.add("ok");
    el.textContent = "UP TO DATE";
  }
}

function renderUpdateStatus(data) {
  const d = data || {};
  document.getElementById("upd_repo").textContent = d.repo || "-";
  document.getElementById("upd_branch").textContent = d.branch || "-";
  document.getElementById("upd_local").textContent = d.local_commit || "-";
  document.getElementById("upd_remote").textContent = d.remote_commit || "-";
  document.getElementById("upd_last_check").textContent = d.last_check || "-";
  setUpdateState(!!d.update_available);
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

async function pollUpdateStatus() {
  try {
    const data = await req("/api/update/status");
    if (!data.ok) {
      document.getElementById("upd_msg").textContent = data.message || "update status error";
      return;
    }
    renderUpdateStatus(data.data);
  } catch (e) {
    document.getElementById("upd_msg").textContent = "update poll error: " + String(e);
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

async function doUpdateAction(path) {
  const data = await req(path, "POST");
  document.getElementById("upd_msg").textContent = data.message || "done";
  if (data.ok && data.data) {
    if (data.data.local_commit || data.data.remote_commit) {
      renderUpdateStatus({
        ...data.data,
        branch: document.getElementById("upd_branch").textContent,
        repo: document.getElementById("upd_repo").textContent,
        remote_commit: data.data.new_commit || document.getElementById("upd_remote").textContent,
        local_commit: data.data.new_commit || document.getElementById("upd_local").textContent,
        update_available: false,
        last_check: new Date().toISOString().slice(0, 19),
      });
    }
  }
  await pollUpdateStatus();
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

document.getElementById("btn_update_check").addEventListener("click", async () => {
  await doUpdateAction("/api/update/check");
});

document.getElementById("btn_update_pull").addEventListener("click", async () => {
  await doUpdateAction("/api/update/pull");
});

document.getElementById("btn_update_pull_restart").addEventListener("click", async () => {
  await doUpdateAction("/api/update/pull_restart");
});

const uploadContentEl = document.getElementById("upload_content");
if (uploadContentEl) {
  uploadContentEl.addEventListener("input", () => autoResizeTextarea(uploadContentEl));
  autoResizeTextarea(uploadContentEl);
}

document.getElementById("btn_upload_send").addEventListener("click", async () => {
  try {
    await doRemoteUpload();
  } catch (e) {
    document.getElementById("upload_msg").textContent = "upload error: " + String(e);
  }
});

(async () => {
  await loadConfig();
  await pollStatus();
  await pollUpdateStatus();
  setInterval(pollStatus, 4000);
  setInterval(pollUpdateStatus, 30000);
})();
