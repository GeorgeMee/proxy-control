#!/usr/bin/env python3
import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Tuple

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
LOGS_DIR = BASE_DIR / "logs"
CONFIG_PATH = BASE_DIR / "config.json"
STATUS_PATH = RUNTIME_DIR / "status.json"
CONTROL_PATH = RUNTIME_DIR / "control.json"
WEB_LOG_PATH = LOGS_DIR / "web.log"
UPDATE_LOG_PATH = LOGS_DIR / "update.log"

DEFAULT_CONFIG: Dict[str, Any] = {
    "web_host": "0.0.0.0",
    "web_port": 5000,
    "local_proxy_host": "127.0.0.1",
    "local_proxy_port": 8080,
    "remote_host": "120.26.216.203",
    "remote_user": "root",
    "remote_port": 12334,
    "server_alive_interval": 30,
    "server_alive_count_max": 3,
    "check_interval": 5,
    "restart_backoff": 5,
    "max_backoff": 60,
    "auto_start_proxy": True,
    "auto_recover": True,
    "log_tail_lines": 50,
}

ALLOWED_CONFIG_KEYS = set(DEFAULT_CONFIG.keys())

DEFAULT_STATUS: Dict[str, Any] = {
    "timestamp": "",
    "pproxy": {
        "running": False,
        "pid": None,
        "port_ok": False,
        "last_start": None,
        "last_error": None,
    },
    "autossh": {
        "running": False,
        "pid": None,
        "remote_port_ok": False,
        "last_start": None,
        "last_restart": None,
        "restart_count": 0,
        "last_error": None,
    },
    "network": {"ssh_ok": False},
    "config": {
        "web_port": 5000,
        "local_proxy_port": 8080,
        "remote_host": "120.26.216.203",
        "remote_port": 12334,
    },
}

app = Flask(__name__)
update_logger = logging.getLogger("update")
MAX_UPLOAD_CONTENT_BYTES = 20 * 1024
SSH_UPLOAD_TIMEOUT_SECONDS = 10


def ensure_layout() -> None:
    for d in (RUNTIME_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        atomic_write_json(CONFIG_PATH, DEFAULT_CONFIG)
    if not STATUS_PATH.exists():
        atomic_write_json(STATUS_PATH, DEFAULT_STATUS)
    for name in ("supervisor.pid", "pproxy.pid", "autossh.pid"):
        p = RUNTIME_DIR / name
        if not p.exists():
            p.touch()
    for name in ("web.log", "supervisor.log", "pproxy.log", "autossh.log", "update.log"):
        p = LOGS_DIR / name
        if not p.exists():
            p.touch()


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent) as tf:
        json.dump(payload, tf, ensure_ascii=True, indent=2)
        tf.write("\n")
        temp_name = tf.name
    os.replace(temp_name, path)


def load_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        app.logger.exception("Failed to load JSON from %s", path)
    return dict(fallback)


def load_config() -> Dict[str, Any]:
    raw = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    config = dict(DEFAULT_CONFIG)
    for key in ALLOWED_CONFIG_KEYS:
        if key in raw:
            config[key] = raw[key]
    return config


def save_config(new_config: Dict[str, Any]) -> Dict[str, Any]:
    current = load_config()
    for key in ALLOWED_CONFIG_KEYS:
        if key in new_config:
            current[key] = new_config[key]
    atomic_write_json(CONFIG_PATH, current)
    return current


def tail_lines(path: Path, n: int) -> str:
    if n <= 0:
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except Exception:
        app.logger.exception("Failed reading log file %s", path)
        return ""


def send_control_command(action: str, payload: Dict[str, Any] | None = None) -> str:
    command = {
        "id": str(uuid.uuid4()),
        "action": action,
        "payload": payload or {},
        "timestamp": int(time.time()),
    }
    atomic_write_json(CONTROL_PATH, command)
    app.logger.info("Queued control action=%s id=%s", action, command["id"])
    return command["id"]


def json_ok(message: str, data: Dict[str, Any] | None = None):
    return jsonify({"ok": True, "message": message, "data": data or {}})


def json_error(message: str, code: int = 400):
    return jsonify({"ok": False, "message": message, "data": {}}), code


def parse_config_payload(payload: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    cleaned: Dict[str, Any] = {}
    for key in ALLOWED_CONFIG_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if key in {
            "web_port",
            "local_proxy_port",
            "remote_port",
            "server_alive_interval",
            "server_alive_count_max",
            "check_interval",
            "restart_backoff",
            "max_backoff",
            "log_tail_lines",
        }:
            try:
                value = int(value)
            except Exception:
                return False, f"Invalid integer for {key}", {}
        if key in {"auto_start_proxy", "auto_recover"}:
            value = bool(value)
        cleaned[key] = value
    return True, "ok", cleaned


def run_cmd(args: list[str], timeout: int = 20) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            args,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "").strip()
        return True, (result.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return False, "command timeout"
    except Exception as exc:
        return False, str(exc)


def git_required_state() -> Tuple[bool, str]:
    ok, output = run_cmd(["git", "status", "--porcelain"])
    if not ok:
        return False, f"git status failed: {output}"
    if output.strip():
        return False, "working tree not clean"

    ok, branch = run_cmd(["git", "branch", "--show-current"])
    if not ok:
        return False, f"git branch check failed: {branch}"
    if branch.strip() != "main":
        return False, "updates allowed only on main branch"

    return True, ""


def resolve_remote_ref(branch: str) -> Tuple[bool, str]:
    candidates: list[str] = []
    clean_branch = branch.strip()
    if clean_branch and clean_branch != "HEAD":
        candidates.append(f"origin/{clean_branch}")

    ok, origin_head = run_cmd(["git", "rev-parse", "--abbrev-ref", "origin/HEAD"])
    if ok:
        ref = origin_head.strip()
        if ref and ref != "origin/HEAD" and ref not in candidates:
            candidates.append(ref)

    # Backward compatibility for repos that still use master as default branch.
    if "origin/master" not in candidates:
        candidates.append("origin/master")

    for ref in candidates:
        ok, _ = run_cmd(["git", "rev-parse", "--verify", ref])
        if ok:
            return True, ref

    return False, "no usable remote branch ref found (tried: " + ", ".join(candidates) + ")"


def get_update_status(fetch_remote: bool = True) -> Tuple[bool, str, Dict[str, Any]]:
    repo_url = ""

    ok, repo = run_cmd(["git", "remote", "get-url", "origin"])
    if ok:
        repo_url = repo.strip()

    ok, branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not ok:
        return False, f"failed to read branch: {branch}", {}

    ok, local_commit = run_cmd(["git", "rev-parse", "--short", "HEAD"])
    if not ok:
        return False, f"failed to read local commit: {local_commit}", {}

    if fetch_remote:
        ok, fetch_msg = run_cmd(["git", "fetch", "origin"])
        if not ok:
            return False, f"git fetch failed: {fetch_msg}", {}
        update_logger.info("update check")

    branch_name = branch.strip()
    ok, remote_ref = resolve_remote_ref(branch_name)
    if not ok:
        return False, f"failed to resolve remote ref: {remote_ref}", {}

    ok, remote_commit = run_cmd(["git", "rev-parse", "--short", remote_ref])
    if not ok:
        return False, f"failed to read remote commit: {remote_commit}", {}

    data = {
        "repo": repo_url,
        "branch": branch_name,
        "local_commit": local_commit.strip(),
        "remote_commit": remote_commit.strip(),
        "update_available": local_commit.strip() != remote_commit.strip(),
        "last_check": datetime.now().isoformat(timespec="seconds"),
    }
    return True, "ok", data


def do_pull() -> Tuple[bool, str, Dict[str, Any]]:
    ok, old_commit = run_cmd(["git", "rev-parse", "--short", "HEAD"])
    if not ok:
        return False, f"failed to read old commit: {old_commit}", {}

    ok, pull_msg = run_cmd(["git", "pull", "--ff-only", "origin", "main"])
    if not ok:
        return False, f"git pull failed: {pull_msg}", {}

    ok, new_commit = run_cmd(["git", "rev-parse", "--short", "HEAD"])
    if not ok:
        return False, f"failed to read new commit: {new_commit}", {}

    old_commit = old_commit.strip()
    new_commit = new_commit.strip()
    message = "already up to date" if old_commit == new_commit else "updated"
    update_logger.info("update pull: %s -> %s", old_commit, new_commit)
    return True, message, {"old_commit": old_commit, "new_commit": new_commit}


def restart_services() -> Tuple[bool, str]:
    stop_script = BASE_DIR / "stop_full.sh"
    start_script = BASE_DIR / "start_full.sh"
    if not stop_script.exists() or not start_script.exists():
        return False, "restart scripts missing"

    ok, stop_msg = run_cmd(["sh", str(stop_script)], timeout=20)
    if not ok:
        return False, f"stop failed: {stop_msg}"

    ok, start_msg = run_cmd(["sh", str(start_script)], timeout=20)
    if not ok:
        return False, f"start failed: {start_msg}"

    update_logger.info("update restart")
    return True, ""


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/games", methods=["GET"])
def api_games():
    games_path = BASE_DIR / "static" / "games.json"
    try:
        with games_path.open("r", encoding="utf-8") as f:
            games = json.load(f)
        return json_ok("games list", {"games": games})
    except Exception as e:
        app.logger.exception("Failed to load games.json")
        return json_error(f"Failed to load games: {e}", 500)


@app.route("/games/<game_id>")
def game_page(game_id):
    games_path = BASE_DIR / "static" / "games.json"
    try:
        with games_path.open("r", encoding="utf-8") as f:
            games = json.load(f)
        
        game_info = None
        for game in games:
            if game.get("id") == game_id:
                game_info = game
                break
        
        if not game_info:
            return render_template("game.html", game=None, error="Game not found")
        
        return render_template("game.html", game=game_info, error=None)
    except Exception as e:
        app.logger.exception("Failed to load game page")
        return render_template("game.html", game=None, error=f"Failed to load game: {e}")


@app.route("/api/status", methods=["GET"])
def api_status():
    config = load_config()
    status = load_json(STATUS_PATH, DEFAULT_STATUS)
    n = int(config.get("log_tail_lines", 50))
    logs = {
        "web": tail_lines(LOGS_DIR / "web.log", n),
        "supervisor": tail_lines(LOGS_DIR / "supervisor.log", n),
        "pproxy": tail_lines(LOGS_DIR / "pproxy.log", n),
        "autossh": tail_lines(LOGS_DIR / "autossh.log", n),
    }
    return json_ok("status", {"status": status, "logs": logs})


@app.route("/api/start", methods=["POST"])
def api_start():
    command_id = send_control_command("start")
    return json_ok("start requested", {"command_id": command_id})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    command_id = send_control_command("stop")
    return json_ok("stop requested", {"command_id": command_id})


@app.route("/api/restart", methods=["POST"])
def api_restart():
    command_id = send_control_command("restart")
    return json_ok("restart requested", {"command_id": command_id})


@app.route("/api/restart/pproxy", methods=["POST"])
def api_restart_pproxy():
    command_id = send_control_command("restart_pproxy")
    return json_ok("restart pproxy requested", {"command_id": command_id})


@app.route("/api/restart/autossh", methods=["POST"])
def api_restart_autossh():
    command_id = send_control_command("restart_autossh")
    return json_ok("restart autossh requested", {"command_id": command_id})


@app.route("/api/test", methods=["POST"])
def api_test():
    command_id = send_control_command("test_remote")
    return json_ok("remote test requested", {"command_id": command_id})


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return json_ok("config", {"config": load_config()})


@app.route("/api/config", methods=["POST"])
def api_set_config():
    payload = request.get_json(silent=True) or {}
    apply_now = bool(payload.get("apply_now", False))
    ok, msg, cleaned = parse_config_payload(payload)
    if not ok:
        return json_error(msg)
    config = save_config(cleaned)
    if apply_now:
        command_id = send_control_command("apply_config")
        return json_ok("config updated and apply requested", {"config": config, "command_id": command_id})
    return json_ok("config updated", {"config": config})


@app.route("/api/remote/upload", methods=["POST"])
def api_remote_upload():
    payload = request.get_json(silent=True) or {}
    remote_path = str(payload.get("path", "")).strip()
    content = payload.get("content", "")

    if not remote_path:
        return json_error("path required")
    if not isinstance(content, str):
        return json_error("content must be string")

    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_UPLOAD_CONTENT_BYTES:
        return json_error("content too large (max 20KB)")

    path_obj = PurePosixPath(remote_path)
    directory = path_obj.parent.as_posix()
    filename = path_obj.name
    if not filename:
        return json_error("invalid path")

    config = load_config()
    remote_host = str(config.get("remote_host", "")).strip()
    remote_user = str(config.get("remote_user", "")).strip()
    if not remote_host or not remote_user:
        return json_error("remote_host/remote_user missing in config")

    remote_target = f"{remote_user}@{remote_host}"
    remote_dir_cmd = f"test -d {shlex.quote(directory)}"
    ok, _ = run_cmd(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={SSH_UPLOAD_TIMEOUT_SECONDS}",
            remote_target,
            remote_dir_cmd,
        ],
        timeout=SSH_UPLOAD_TIMEOUT_SECONDS,
    )
    if not ok:
        return json_error("directory not found")

    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(content)
            temp_path = tf.name

        ok, scp_msg = run_cmd(
            [
                "scp",
                "-o",
                f"ConnectTimeout={SSH_UPLOAD_TIMEOUT_SECONDS}",
                temp_path,
                f"{remote_target}:{remote_path}",
            ],
            timeout=SSH_UPLOAD_TIMEOUT_SECONDS,
        )
        if not ok:
            return json_error(f"upload failed: {scp_msg}", 500)
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception:
                app.logger.exception("Failed deleting temp upload file: %s", temp_path)

    return json_ok("uploaded")


@app.route("/api/update/status", methods=["GET"])
def api_update_status():
    ok, msg, data = get_update_status(fetch_remote=True)
    if not ok:
        update_logger.error("update status error: %s", msg)
        return json_error(msg, 500)
    return json_ok("status", data)


@app.route("/api/update/check", methods=["POST"])
def api_update_check():
    ok, msg, data = get_update_status(fetch_remote=True)
    if not ok:
        update_logger.error("update check error: %s", msg)
        return json_error(msg, 500)
    return json_ok("status", data)


@app.route("/api/update/pull", methods=["POST"])
def api_update_pull():
    ok, reason = git_required_state()
    if not ok:
        update_logger.error("update pull rejected: %s", reason)
        return json_error(reason)

    ok, msg, data = do_pull()
    if not ok:
        update_logger.error("update pull error: %s", msg)
        return json_error(msg, 500)
    return json_ok(msg, data)


@app.route("/api/update/pull_restart", methods=["POST"])
def api_update_pull_restart():
    ok, reason = git_required_state()
    if not ok:
        update_logger.error("update pull_restart rejected: %s", reason)
        return json_error(reason)

    ok, msg, data = do_pull()
    if not ok:
        update_logger.error("update pull_restart pull error: %s", msg)
        return json_error(msg, 500)

    ok, restart_msg = restart_services()
    if not ok:
        update_logger.error("update restart error: %s", restart_msg)
        return json_error(restart_msg, 500)

    return json_ok("updated and restarted", data)


def setup_logging() -> None:
    ensure_layout()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(WEB_LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )
    update_logger.setLevel(logging.INFO)
    update_logger.propagate = False
    if not update_logger.handlers:
        update_logger.addHandler(logging.FileHandler(UPDATE_LOG_PATH, encoding="utf-8"))


def main() -> None:
    setup_logging()
    config = load_config()
    app.logger.info("Starting web app on %s:%s", config["web_host"], config["web_port"])
    app.run(host=str(config["web_host"]), port=int(config["web_port"]), debug=False)


if __name__ == "__main__":
    main()

