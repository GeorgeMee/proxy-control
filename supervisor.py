#!/usr/bin/env python3
import json
import logging
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = BASE_DIR / "runtime"
LOGS_DIR = BASE_DIR / "logs"
CONFIG_PATH = BASE_DIR / "config.json"
STATUS_PATH = RUNTIME_DIR / "status.json"
CONTROL_PATH = RUNTIME_DIR / "control.json"
SUPERVISOR_PID_PATH = RUNTIME_DIR / "supervisor.pid"
PPROXY_PID_PATH = RUNTIME_DIR / "pproxy.pid"
AUTOSSH_PID_PATH = RUNTIME_DIR / "autossh.pid"
SUPERVISOR_LOG_PATH = LOGS_DIR / "supervisor.log"
PPROXY_LOG_PATH = LOGS_DIR / "pproxy.log"
AUTOSSH_LOG_PATH = LOGS_DIR / "autossh.log"

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

SHUTDOWN = False


def ensure_layout() -> None:
    for d in (RUNTIME_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        atomic_write_json(CONFIG_PATH, DEFAULT_CONFIG)
    if not STATUS_PATH.exists():
        atomic_write_json(STATUS_PATH, DEFAULT_STATUS)
    for p in (SUPERVISOR_PID_PATH, PPROXY_PID_PATH, AUTOSSH_PID_PATH):
        if not p.exists():
            p.touch()
    for p in (SUPERVISOR_LOG_PATH, PPROXY_LOG_PATH, AUTOSSH_LOG_PATH):
        if not p.exists():
            p.touch()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(SUPERVISOR_LOG_PATH, encoding="utf-8"), logging.StreamHandler()],
    )


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
        logging.exception("Failed to load JSON from %s", path)
    return dict(fallback)


def load_config() -> Dict[str, Any]:
    raw = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    config = dict(DEFAULT_CONFIG)
    config.update({k: raw[k] for k in raw if k in DEFAULT_CONFIG})
    return config


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def write_pid(path: Path, pid: int) -> None:
    path.write_text(str(pid), encoding="utf-8")


def clear_pid(path: Path) -> None:
    path.write_text("", encoding="utf-8")


def read_pid(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return int(raw)
    except Exception:
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_from_pid(path: Path) -> Optional[int]:
    pid = read_pid(path)
    if pid is None:
        return None
    if pid_alive(pid):
        return pid
    clear_pid(path)
    return None


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def ssh_run(config: Dict[str, Any], remote_cmd: str) -> Tuple[bool, str]:
    target = f"{config['remote_user']}@{config['remote_host']}"
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        target,
        remote_cmd,
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10)
        return proc.returncode == 0, proc.stdout
    except Exception as exc:
        return False, str(exc)


def test_remote(config: Dict[str, Any]) -> Tuple[bool, bool, str]:
    ssh_ok, out = ssh_run(config, "echo ok")
    if not ssh_ok:
        return False, False, out
    ok, port_out = ssh_run(config, "sh -lc 'ss -lnt 2>/dev/null || netstat -lnt 2>/dev/null'")
    if not ok:
        return True, False, port_out
    needle = f":{config['remote_port']}"
    return True, (needle in port_out), port_out


def terminate_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


class Supervisor:
    def __init__(self) -> None:
        self.status: Dict[str, Any] = load_json(STATUS_PATH, DEFAULT_STATUS)
        self.last_command_id: Optional[str] = None
        self.last_remote_check = 0.0
        self.backoff = {
            "pproxy_delay": 0,
            "autossh_delay": 0,
            "pproxy_next": 0.0,
            "autossh_next": 0.0,
        }

    def update_status_config(self, config: Dict[str, Any]) -> None:
        self.status["timestamp"] = now_iso()
        self.status["config"] = {
            "web_port": int(config["web_port"]),
            "local_proxy_port": int(config["local_proxy_port"]),
            "remote_host": str(config["remote_host"]),
            "remote_port": int(config["remote_port"]),
        }

    def sync_runtime_status(self, config: Dict[str, Any]) -> None:
        pproxy_pid = process_from_pid(PPROXY_PID_PATH)
        autossh_pid = process_from_pid(AUTOSSH_PID_PATH)

        self.status["pproxy"]["running"] = pproxy_pid is not None
        self.status["pproxy"]["pid"] = pproxy_pid
        self.status["pproxy"]["port_ok"] = port_open(str(config["local_proxy_host"]), int(config["local_proxy_port"]))

        self.status["autossh"]["running"] = autossh_pid is not None
        self.status["autossh"]["pid"] = autossh_pid

        now = time.time()
        if now - self.last_remote_check >= max(10, int(config["check_interval"])):
            ssh_ok, remote_ok, _ = test_remote(config)
            self.status["network"]["ssh_ok"] = ssh_ok
            self.status["autossh"]["remote_port_ok"] = remote_ok
            self.last_remote_check = now

        self.update_status_config(config)
        atomic_write_json(STATUS_PATH, self.status)

    def start_pproxy(self, config: Dict[str, Any]) -> bool:
        pid = process_from_pid(PPROXY_PID_PATH)
        if pid is not None:
            return True
        cmd = ["pproxy", "-l", f"http://{config['local_proxy_host']}:{config['local_proxy_port']}"]
        try:
            with PPROXY_LOG_PATH.open("a", encoding="utf-8") as logf:
                proc = subprocess.Popen(cmd, stdout=logf, stderr=logf)
            write_pid(PPROXY_PID_PATH, proc.pid)
            time.sleep(0.5)
            if proc.poll() is not None:
                self.status["pproxy"]["last_error"] = f"pproxy exited with code {proc.returncode}"
                clear_pid(PPROXY_PID_PATH)
                return False
            self.status["pproxy"]["last_start"] = now_iso()
            self.status["pproxy"]["last_error"] = None
            logging.info("Started pproxy pid=%s", proc.pid)
            return True
        except Exception as exc:
            self.status["pproxy"]["last_error"] = str(exc)
            logging.exception("Failed to start pproxy")
            return False

    def stop_pproxy(self) -> None:
        pid = process_from_pid(PPROXY_PID_PATH)
        if pid is not None:
            terminate_pid(pid)
            logging.info("Stopped pproxy pid=%s", pid)
        clear_pid(PPROXY_PID_PATH)

    def start_autossh(self, config: Dict[str, Any]) -> bool:
        pid = process_from_pid(AUTOSSH_PID_PATH)
        if pid is not None:
            return True
        if not port_open(str(config["local_proxy_host"]), int(config["local_proxy_port"])):
            self.status["autossh"]["last_error"] = "Local proxy port is not listening"
            return False
        target = f"{config['remote_user']}@{config['remote_host']}"
        cmd = [
            "autossh",
            "-M",
            "0",
            "-o",
            f"ServerAliveInterval={config['server_alive_interval']}",
            "-o",
            f"ServerAliveCountMax={config['server_alive_count_max']}",
            "-o",
            "ExitOnForwardFailure=yes",
            "-N",
            "-R",
            f"{config['remote_port']}:{config['local_proxy_host']}:{config['local_proxy_port']}",
            target,
        ]
        try:
            with AUTOSSH_LOG_PATH.open("a", encoding="utf-8") as logf:
                proc = subprocess.Popen(cmd, stdout=logf, stderr=logf)
            write_pid(AUTOSSH_PID_PATH, proc.pid)
            time.sleep(0.8)
            if proc.poll() is not None:
                self.status["autossh"]["last_error"] = f"autossh exited with code {proc.returncode}"
                clear_pid(AUTOSSH_PID_PATH)
                return False
            self.status["autossh"]["last_start"] = now_iso()
            self.status["autossh"]["last_error"] = None
            logging.info("Started autossh pid=%s", proc.pid)
            return True
        except Exception as exc:
            self.status["autossh"]["last_error"] = str(exc)
            logging.exception("Failed to start autossh")
            return False

    def stop_autossh(self) -> None:
        pid = process_from_pid(AUTOSSH_PID_PATH)
        if pid is not None:
            terminate_pid(pid)
            logging.info("Stopped autossh pid=%s", pid)
        clear_pid(AUTOSSH_PID_PATH)

    def start_chain(self, config: Dict[str, Any]) -> None:
        pproxy_ok = self.start_pproxy(config)
        if pproxy_ok:
            self.start_autossh(config)

    def stop_chain(self) -> None:
        self.stop_autossh()
        self.stop_pproxy()

    def restart_chain(self, config: Dict[str, Any]) -> None:
        self.stop_chain()
        time.sleep(0.2)
        self.start_chain(config)

    def restart_pproxy(self, config: Dict[str, Any]) -> None:
        self.stop_pproxy()
        time.sleep(0.2)
        self.start_pproxy(config)

    def restart_autossh(self, config: Dict[str, Any]) -> None:
        self.stop_autossh()
        time.sleep(0.2)
        self.start_autossh(config)

    def handle_command(self, config: Dict[str, Any]) -> bool:
        if not CONTROL_PATH.exists():
            return False
        try:
            cmd = load_json(CONTROL_PATH, {})
            command_id = str(cmd.get("id", "")).strip()
            action = str(cmd.get("action", "")).strip()
            if not command_id or not action or command_id == self.last_command_id:
                return False
            self.last_command_id = command_id
            logging.info("Handling command id=%s action=%s", command_id, action)

            if action == "start":
                self.start_chain(config)
            elif action == "stop":
                self.stop_chain()
            elif action == "restart":
                self.restart_chain(config)
            elif action == "restart_pproxy":
                self.restart_pproxy(config)
            elif action == "restart_autossh":
                self.restart_autossh(config)
            elif action == "test_remote":
                ssh_ok, remote_ok, detail = test_remote(config)
                self.status["network"]["ssh_ok"] = ssh_ok
                self.status["autossh"]["remote_port_ok"] = remote_ok
                self.status["autossh"]["last_error"] = None if remote_ok else detail[-200:]
            elif action == "apply_config":
                self.restart_chain(load_config())
            elif action == "stop_supervisor":
                self.stop_chain()
                global SHUTDOWN
                SHUTDOWN = True
            else:
                logging.warning("Unknown action: %s", action)
            return True
        except Exception:
            logging.exception("Failed handling command")
            return False

    def apply_auto_recover(self, config: Dict[str, Any]) -> None:
        if not bool(config.get("auto_recover", True)):
            return

        now = time.time()
        min_delay = int(config.get("restart_backoff", 5))
        max_delay = int(config.get("max_backoff", 60))

        pproxy_alive = process_from_pid(PPROXY_PID_PATH) is not None
        if not pproxy_alive and now >= self.backoff["pproxy_next"]:
            ok = self.start_pproxy(config)
            if ok:
                self.backoff["pproxy_delay"] = 0
            else:
                delay = self.backoff["pproxy_delay"] or min_delay
                self.backoff["pproxy_next"] = now + delay
                self.backoff["pproxy_delay"] = min(delay * 2, max_delay)

        autossh_alive = process_from_pid(AUTOSSH_PID_PATH) is not None
        if not autossh_alive and now >= self.backoff["autossh_next"]:
            ok = self.start_autossh(config)
            if ok:
                self.backoff["autossh_delay"] = 0
                self.status["autossh"]["last_restart"] = now_iso()
                self.status["autossh"]["restart_count"] = int(self.status["autossh"].get("restart_count", 0)) + 1
            else:
                delay = self.backoff["autossh_delay"] or min_delay
                self.backoff["autossh_next"] = now + delay
                self.backoff["autossh_delay"] = min(delay * 2, max_delay)


def handle_signal(signum, frame):
    del frame
    logging.info("Received signal=%s, shutting down", signum)
    global SHUTDOWN
    SHUTDOWN = True


def ensure_single_instance() -> None:
    old_pid = read_pid(SUPERVISOR_PID_PATH)
    if old_pid and pid_alive(old_pid):
        raise SystemExit(f"supervisor already running with pid {old_pid}")
    write_pid(SUPERVISOR_PID_PATH, os.getpid())


def main() -> None:
    ensure_layout()
    setup_logging()
    ensure_single_instance()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    supervisor = Supervisor()
    cfg = load_config()
    if bool(cfg.get("auto_start_proxy", True)):
        supervisor.start_chain(cfg)

    logging.info("Supervisor started pid=%s", os.getpid())

    try:
        while not SHUTDOWN:
            config = load_config()
            supervisor.handle_command(config)
            supervisor.apply_auto_recover(config)
            supervisor.sync_runtime_status(config)
            time.sleep(max(1, int(config.get("check_interval", 5))))
    finally:
        clear_pid(SUPERVISOR_PID_PATH)
        logging.info("Supervisor stopped")


if __name__ == "__main__":
    main()