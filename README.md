# Puget Sound OSINT

Ferry vessel tracking platform for Puget Sound. Combines WSDOT Vessels API position data with YOLOv8 object detection on terminal camera feeds. Generates TACREPs and streams them to ChatSurfer. A deconfliction engine suppresses duplicate reports and correlates detections across both sources.

## Architecture

```
run.py → src/app.py (orchestrator)
  ├── src/ingestion/feed_manager.py    Camera polling (async, threaded)
  ├── src/detection/vessel_detector.py YOLOv8 inference
  ├── src/tracking/wsf_api.py          WSDOT Vessels REST API client
  ├── src/reporting/tacrep.py          TACREP format generation
  ├── src/reporting/chatsurfer.py      Report queue + ChatSurfer HTTP POST
  ├── src/reporting/deconfliction.py   Cross-source dedup (120s window, 2nm correlation)
  └── src/api/server.py                FastAPI server + embedded HTML/JS dashboard
```

Three threads: main (uvicorn), feed manager (asyncio camera polling), ChatSurfer worker (report queue). All state in memory. No database.

## Requirements

- Python 3.10+
- ~2GB disk (PyTorch CPU + ultralytics)
- WSDOT API key (free, register at [wsdot.wa.gov](https://wsdot.wa.gov/traffic/api/))

## Bare Metal Setup

```bash
git clone https://github.com/jaw1999/Puget-Sound-OSINT.git
cd Puget-Sound-OSINT

python3 -m venv venv
source venv/bin/activate

# CPU-only PyTorch (skip CUDA, saves ~1.5GB)
pip install numpy"<2"
pip install torch==2.1.2+cpu torchvision==0.16.2+cpu \
  --index-url https://download.pytorch.org/whl/cpu

pip install -r requirements.txt
```

### Run

```bash
python3 run.py
```

Dashboard at `http://localhost:8080`.

### CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | `config/settings.yaml` | Config file path |
| `--port` | `8080` | Web server port |
| `--log-level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--log-file` | `logs/osint.log` | Log output path |
| `--no-web` | `false` | Run without the web dashboard |

### Device Selection

Edit `config/settings.yaml` under `detector.device`:

```yaml
detector:
  device: cpu      # default
  # device: mps    # Apple Silicon
  # device: cuda:0 # NVIDIA GPU (requires CUDA torch)
```

## Docker

```bash
docker build -t puget-sound-osint .
```

```bash
docker run -p 8080:8080 \
  -v $(pwd)/captures:/app/captures \
  -v $(pwd)/reports:/app/reports \
  -v $(pwd)/logs:/app/logs \
  puget-sound-osint
```

The image uses CPU-only PyTorch (~1.2GB vs ~4GB with CUDA). Builds on `python:3.10-slim`. Enforces `numpy<2` for torch compatibility.

### Override Config

Mount a custom settings file:

```bash
docker run -p 8080:8080 \
  -v $(pwd)/config:/app/config \
  -v $(pwd)/captures:/app/captures \
  puget-sound-osint
```

## Configuration

All config in `config/settings.yaml`. Cameras in `config/cameras.yaml`. TAI/platform mappings in `config/tai_mapping.yaml`.

WSDOT API key and ChatSurfer credentials are set through the web dashboard at runtime (ChatSurfer tab). Config changes persist in memory only — lost on restart.

### TACREP Format

```
CALLSIGN//SERIAL//NUM_TARGETS//CONFIDENCE//PLATFORM//TAI//HHMM//REM: remarks
```

Example:
```
PR01//I005//1//PROBABLE//ORCA//BALDER//1430//REM: VES TOKITAE EN ROUTE BAINBRIDGE 12KTS
```

### Platform Codes

| Code | Class | Vessels |
|------|-------|---------|
| `WHALE` | Jumbo Mark II | Tacoma, Wenatchee, Puyallup |
| `EAGLE` | Super | Hyak, Kaleetan, Yakima, Elwha, Walla Walla, Spokane |
| `SALMON` | Issaquah | Issaquah, Kittitas, Cathlamet, Kitsap, Sealth, Chelan |
| `ORCA` | Olympic | Tokitae, Samish, Chimacum, Suquamish |
| `SEAL` | Kwa-di Tabil | Chetzemoka, Kennewick, Salish |

### Confidence Levels

| Level | Source |
|-------|--------|
| `CONFIRMED` | API-identified or cross-source correlated |
| `PROBABLE` | Visual detection >= 0.5 confidence |
| `POSSIBLE` | Visual detection >= 0.3 confidence |
| `UNKNOWN` | Visual detection < 0.3 confidence |

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Web dashboard |
| `GET` | `/api/status` | Platform status |
| `GET` | `/api/vessels` | All vessel positions (triggers TACREP generation) |
| `GET` | `/api/feeds` | Camera feed status |
| `GET` | `/api/feeds/{id}/snapshot` | Latest frame as JPEG |
| `POST` | `/api/detection/scan-all` | Run YOLOv8 on all feeds |
| `GET` | `/api/detection/detect/{id}/annotated` | Annotated detection JPEG |
| `GET` | `/api/tacrep/recent` | Recent TACREPs |
| `POST` | `/api/tacrep/manual` | Submit manual TACREP |
| `POST` | `/api/checkin` | Send check-in message |
| `POST` | `/api/checkout` | Send check-out message |
| `GET` | `/api/tai-areas` | TAI polygon definitions |
| `GET` | `/api/deconfliction/status` | Deconfliction engine state |

## Project Structure

```
├── run.py                     Entry point
├── config/
│   ├── settings.yaml          Platform config
│   ├── cameras.yaml           Camera feed definitions
│   └── tai_mapping.yaml       TAI codes, platform codes
├── src/
│   ├── app.py                 Orchestrator
│   ├── api/server.py          FastAPI + HTML dashboard (~3500 lines)
│   ├── ingestion/
│   │   ├── feed_manager.py    Async camera polling
│   │   └── wsdot_cameras.py   WSDOT terminal camera metadata
│   ├── detection/
│   │   └── vessel_detector.py YOLOv8 wrapper
│   ├── tracking/
│   │   └── wsf_api.py         WSDOT Vessels API client
│   └── reporting/
│       ├── tacrep.py          TACREP generation/parsing
│       ├── chatsurfer.py      ChatSurfer client + queue
│       └── deconfliction.py   Cross-source dedup engine
├── yolov8n.pt                 YOLOv8 nano model (6.5MB)
├── captures/                  Saved frames (by date/feed)
├── reports/                   TACREP log output
└── docs/                      Technical reference
```
