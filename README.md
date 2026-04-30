# LLMCAD (build123d2web bare-metal)

Web-based [Build123d](https://github.com/gumyr/build123d) editor with a three.js viewer and STL/STEP export. This fork of [cadquery2web](https://github.com/30hours/cadquery2web) runs on bare metal (no Docker), suitable for an LXC Proxmox / Debian / Ubuntu setup.

![build123d2web example](./example.png)

## Architecture

Two processes on the same host:

- **Node.js** (`node/server.js`) — listens on `0.0.0.0:49157`. Serves the static frontend from `web/` and exposes the API under `/api/{preview,stl,step,generate}`. Forwards API requests to the Python server.
- **Python / Build123d** (`cadquery/server.py`) — listens on `127.0.0.1:5002` (loopback only). Spawns a fresh `worker.py` subprocess per request to tessellate for preview and export STL/STEP. The historical `cadquery/` directory name is preserved for git history; the engine inside is build123d.

```
Browser  ──HTTP──▶  Node :49157 ──HTTP──▶  Python/Build123d :5002
                       │                     │
                       │                     └── per-request worker.py subprocess
                       └── serves web/ (HTML/JS/CSS)
```

## Prerequisites

- Linux (tested on Ubuntu 24.04 LXC). Debian 12+ should work as well.
- **Node.js LTS** (installed via [nvm](https://github.com/nvm-sh/nvm)).
- **Python 3.11+** with `venv` (Python 3.12 tested; build123d 0.10 supports 3.10–3.13).
- System packages: `python3-venv`, `python3-pip`, `libgl1`, `libglx-mesa0` (needed by VTK / OpenCascade).

Install system packages (Debian/Ubuntu):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip libgl1 libglx-mesa0 curl ca-certificates
```

Install nvm and Node LTS (user space, no sudo):

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm install --lts
```

## Install

```bash
git clone https://github.com/Emilien-Etadam/LLMCAD.git
cd LLMCAD

# Python venv + Build123d deps (cadquery-ocp pulled transitively)
cd cadquery
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
cd ..

# Node deps
cd node
npm install
cd ..

# Configuration (override only if you need different ports/hosts)
cp .env.example .env
```

## Configuration (`.env`)

```
CADQUERY_HOST=127.0.0.1
CADQUERY_PORT=5002
NODE_HOST=0.0.0.0
NODE_PORT=49157
```

The Python server intentionally binds to `127.0.0.1` so it cannot be reached from outside the host. Only the Node server (which fronts the API and serves the UI) is exposed publicly.

## Run

```bash
./start.sh
```

The script loads `.env`, starts both processes, and stops them cleanly on `Ctrl+C` / `SIGTERM`. Logs of API requests are written to `./logs/requests-YYYY-MM-DD.log`.

Open the editor at `http://<host-ip>:49157` (or `http://localhost:49157` if local).

### Run as a systemd service (optional)

Example unit (`/etc/systemd/system/llmcad.service`):

```ini
[Unit]
Description=LLMCAD (cadquery2web bare-metal)
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/LLMCAD
ExecStart=/path/to/LLMCAD/start.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl daemon-reload && sudo systemctl enable --now llmcad`.

## Operation

- The frontend (`web/`) is HTML/CSS/JavaScript that allows Build123d code to be input, shows the current 3D model (coarse) with [three.js](https://github.com/mrdoob/three.js/), and lets you download STL/STEP exports.
- The Node server handles requests from the user (preview / STL / STEP / generate) and queues them to avoid concurrent geometry executions.
- The Python server spawns a fresh subprocess (`cadquery/worker.py`) for each `/preview`, `/stl`, and `/step` request. The worker imports build123d, applies an `RLIMIT_AS` cap (4 GiB by default; override via `CADQUERY_WORKER_MEM_LIMIT_MB`), `exec()`s the user code, and writes the output. A 30 s wall-clock timeout (`CADQUERY_EXEC_TIMEOUT`) lives in the parent. Native crashes (segfaults in OpenCascade, out-of-memory) only kill the worker; the parent Flask server stays up.

  The user script must assign its 3D result to `result` (a `Part`, `Compound`, or `Solid`). The worker preloads `from build123d import *` so common constructors (`Box`, `Cylinder`, `fillet`, `extrude`, `Pos`, `Axis`, ...) are available without an explicit import.

## Security model

There is **no** AST-level validator (the previous `CadQueryValidator.py` was removed in phase 5 — see [`STATUS.md`](./STATUS.md)). The only sandbox is process isolation + `RLIMIT_AS` + the 30 s timeout, plus a watchdog (`start.sh`) that probes `GET /health` every 30 s and force-restarts the Flask process if it stops responding.

This is sufficient for **local-only** deployments (loopback bind, single-user). Do **not** expose this server to the public internet without re-introducing input validation.

## Notes

- See the [Build123d documentation](https://build123d.readthedocs.io/) for the full API. Algebraic style (`Box(50,30,10) - Cylinder(5,30)`, `fillet(part.edges(), 2)`) is preferred in this project.
- Loosely based on [replicad](https://github.com/sgenoud/replicad) but using Build123d / OpenCascade instead of [OpenCascade.js](https://ocjs.org/).
- Pull requests are very welcome.

## Future Work

- Persistent worker pool (warm OCC import) — phase 6.
- vLLM client + agentic loop — phase 7.
- Geometric tool calling — phase 8.

## License

[MIT](https://choosealicense.com/licenses/mit/)
