"""FSR AI Eval Stub operations -- hosts scenario-driven stub MCP servers on the appliance.

Same pattern as the Microsoft Teams connector's bot listener: the connector
launches ``listener.py`` as a detached subprocess (with the integrations
virtualenv's python) when a configuration is added or activated. The listener
binds 127.0.0.1 only; fsr-ai runs on the same box and reaches each backend at
``http://127.0.0.1:<port>/mcp/<server_key>``.

The operations load fixtures, read/clear the call log and start/stop the
listener, so pyfsr's evaluation runner drives everything over the REST API.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from connectors.core.connector import ConnectorError, get_logger

logger = get_logger("fsr-ai-eval-stub")

HERE = os.path.dirname(os.path.abspath(__file__))
LISTENER = os.path.join(HERE, "listener.py")
DEFAULT_PORT = 18900

try:
    import uwsgi  # noqa: F401 - only present inside the integrations service

    PYTHON = uwsgi.opt["virtualenv"].decode("utf-8") + "/bin/python"
except Exception:
    PYTHON = sys.executable


def _state_dir():
    for base in (os.path.join(HERE, "state"), os.path.join(tempfile.gettempdir(), "fsr-ai-eval-stub")):
        try:
            os.makedirs(base, exist_ok=True)
            probe = os.path.join(base, ".w")
            with open(probe, "w") as f:
                f.write("")
            os.remove(probe)
            return base
        except OSError:
            continue
    raise ConnectorError("no writable state directory for the eval stub")


def _paths():
    d = _state_dir()
    return {
        "fixtures": os.path.join(d, "fixtures.json"),
        "log": os.path.join(d, "calls.jsonl"),
        "pid": os.path.join(d, "listener.pid"),
        "out": os.path.join(d, "listener.out"),
    }


def _port(config):
    try:
        return int((config or {}).get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def _listening(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"status": "down", "error": str(e)}


def start_listener(config):
    port, p = _port(config), _paths()
    if _listening(port):
        return {"started": False, "running": True, "port": port, **_health(port)}
    if not os.path.exists(p["fixtures"]):
        with open(p["fixtures"], "w") as f:
            json.dump({"servers": {}}, f)
    cmd = [
        PYTHON,
        LISTENER,
        "--port",
        str(port),
        "--fixtures",
        p["fixtures"],
        "--call-log",
        p["log"],
        "--pid-file",
        p["pid"],
    ]
    env = dict(os.environ)
    if (config or {}).get("token"):
        env["EVAL_STUB_TOKEN"] = config["token"]
    with open(p["out"], "a") as out:
        subprocess.Popen(cmd, stdout=out, stderr=out, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
    for _ in range(20):
        if _listening(port):
            return {"started": True, "running": True, "port": port, **_health(port)}
        time.sleep(0.25)
    raise ConnectorError(f"listener did not come up on 127.0.0.1:{port}; see {p['out']}")


def _listener_pids(port):
    """Listener processes for ``port``, found by command line in /proc.

    The pid file lives in the connector directory, which a reinstall replaces,
    so a listener started by the previous install can only be found this way.
    """
    pids = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids
    for entry in entries:
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv = f.read().decode(errors="replace").split("\0")
        except OSError:
            continue
        if any(a.endswith("listener.py") for a in argv) and "--port" in argv and str(port) in argv:
            pids.append(int(entry))
    return pids


def stop_listener(config=None):
    p, port = _paths(), _port(config)
    pids = set(_listener_pids(port))
    try:
        with open(p["pid"]) as f:
            pids.add(int(f.read().strip()))
        os.remove(p["pid"])
    except (OSError, ValueError):
        pass
    stopped = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except OSError:
            pass
    for _ in range(20):
        if not _listening(port):
            break
        time.sleep(0.25)
    return {"stopped": bool(stopped), "pids": stopped}


def load_fixtures(config, params):
    fixtures = params.get("fixtures")
    if isinstance(fixtures, str):
        fixtures = json.loads(fixtures)
    if not isinstance(fixtures, dict) or not isinstance(fixtures.get("servers"), dict):
        raise ConnectorError('fixtures must be a JSON object {"servers": {...}}')
    p = _paths()
    tmp = p["fixtures"] + ".tmp"
    with open(tmp, "w") as f:
        json.dump(fixtures, f)
    os.replace(tmp, p["fixtures"])  # the listener reloads on mtime change
    status = start_listener(config)
    return {
        "servers": sorted(fixtures["servers"]),
        "tools": sum(len(s.get("tools") or []) for s in fixtures["servers"].values()),
        **status,
    }


def get_call_log(config, params):
    p, rows = _paths(), []
    since = float(params.get("since") or 0)
    if os.path.exists(p["log"]):
        with open(p["log"]) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("ts", 0) >= since:
                    rows.append(row)
    if params.get("clear"):
        open(p["log"], "w").close()
    return {"calls": rows, "count": len(rows)}


def server_status(config, params=None):
    port = _port(config)
    return {"port": port, "running": _listening(port), **_health(port)}


OPERATIONS = {
    "load_fixtures": load_fixtures,
    "get_call_log": get_call_log,
    "server_status": server_status,
    "start_server": lambda config, params: start_listener(config),
    "stop_server": lambda config, params: stop_listener(config),
}
