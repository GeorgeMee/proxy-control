# proxy-control-mobile

Lightweight proxy chain control for mobile Reterminal Alpine environments.

## Features
- Flask web console
- Python supervisor daemon (single process guard)
- Control `pproxy` and `autossh`
- Config file: `config.json`
- Runtime status file: `runtime/status.json`
- Log files under `logs/`

## Runtime assumptions
- Alpine / BusyBox style environment
- No `systemd`
- No `OpenRC`
- No Docker / DB / Redis

## Install dependencies
```sh
python3 -m pip install -r requirements.txt
# autossh must be installed by OS package manager
```

## SSH key setup (passwordless)
1. Generate key pair if needed:
```sh
ssh-keygen -t ed25519
```
2. Copy public key to remote host:
```sh
ssh-copy-id root@120.26.216.203
```
3. Verify:
```sh
ssh -o BatchMode=yes root@120.26.216.203 'echo ok'
```

## Start services
```sh
sh scripts/start_all.sh
```

## Start web console
```sh
python3 app.py
```

## Access page
- `http://<device-ip>:5000/`

## Logs
- `logs/web.log`
- `logs/supervisor.log`
- `logs/pproxy.log`
- `logs/autossh.log`

Example:
```sh
tail -f logs/supervisor.log
```

## Troubleshooting
- `pproxy` not running: check `logs/pproxy.log`
- `autossh` not running: confirm local proxy port is listening first
- remote port not open: run `sh scripts/test_remote.sh`
- no status update: confirm supervisor pid is alive and writable `runtime/status.json`