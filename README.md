# LLMCAD (cadquery2web bare-metal)

Web-based [CadQuery](https://github.com/CadQuery/cadquery) editor with a three.js viewer and STL/STEP export. This fork of [cadquery2web](https://github.com/30hours/cadquery2web) runs on bare metal (no Docker), suitable for an LXC Proxmox / Debian / Ubuntu setup.

![cadquery2web example](./example.png)

## Architecture

Two processes on the same host:

- **Node.js** (`node/server.js`) — listens on `0.0.0.0:49157`. Serves the static frontend from `web/` and exposes the API under `/api/{preview,stl,step}`. Forwards API requests to the Python server.
- **Python / CadQuery** (`cadquery/server.py`) — listens on `127.0.0.1:5002` (loopback only). Validates user code (`CadQueryValidator.py`), tessellates for preview, and exports STL/STEP.

```
Browser  ──HTTP──▶  Node :49157 ──HTTP──▶  Python/CadQuery :5002
                       │
                       └── serves web/ (HTML/JS/CSS)
```

## Prerequisites

- Linux (tested on Ubuntu 24.04 LXC). Debian 12+ should work as well.
- **Node.js LTS** (installed via [nvm](https://github.com/nvm-sh/nvm)).
- **Python 3.11+** with `venv` (Python 3.12 is fine; CadQuery 2.7 supports 3.10–3.12).
- System packages: `python3-venv`, `python3-pip`, `libgl1`, `libglx-mesa0` (needed by VTK / CadQuery).

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

# Python venv + CadQuery deps
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

- The frontend (`web/`) is HTML/CSS/JavaScript that allows CadQuery code to be input, shows the current 3D model (coarse) with [three.js](https://github.com/mrdoob/three.js/), and lets you download STL/STEP exports.
- The Node server handles requests from the user (preview / STL / STEP) and queues them to avoid concurrent CadQuery executions.
- The CadQuery Python server has security controls so only CadQuery-like code can be executed (see [`CadQueryValidator.py`](./cadquery/CadQueryValidator.py)). Strict whitelisting is applied to imports, function calls, and AST node types.

  Once code passes the validator, the shape undergoes coarse tessellation into vertices and faces. This data is sent back to the frontend for display. STL/STEP files can then be generated for the high resolution model.

## Notes

- See [CadQuery examples](https://cadquery.readthedocs.io/en/latest/examples.html) for code that can be run directly.
- Loosely based on [replicad](https://github.com/sgenoud/replicad) but using CadQuery instead of [OpenCascade.js](https://ocjs.org/).
- Pull requests are very welcome.

## Future Work

- Support arbitrary function definitions (currently whitelisted, so they can be defined but not executed).
- Add line numbers to the Python editor.
- Better error reporting/handling for syntax issues.
- Add axis labels on the three.js display.

## License

[MIT](https://choosealicense.com/licenses/mit/)
