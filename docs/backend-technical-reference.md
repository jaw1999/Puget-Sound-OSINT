# Puget Sound OSINT Platform - Backend Technical Reference

## 1. System Overview

The Puget Sound OSINT Platform monitors Washington State Ferry vessels through two data paths: camera feed polling with YOLOv8 object detection, and the WSDOT Vessels REST API. When either path identifies a vessel, the system generates a TACREP (tactical report) and streams it to a ChatSurfer chat room. A deconfliction layer sits between both paths to suppress duplicate reports and correlate detections across sources.

The backend runs as a single Python process. It spawns two background threads (camera polling and report queue processing) alongside a FastAPI web server on the main thread. All state lives in memory. There is no database.

---

## 2. Project Structure

```
puget-sound-osint/
├── run.py                            # Entry point, calls src.app.main()
├── requirements.txt                  # Python dependencies
├── yolov8n.pt                        # Pre-trained YOLO nano model (6.5 MB)
├── config/
│   ├── settings.yaml                 # Platform-level configuration
│   ├── cameras.yaml                  # Camera feed URLs and coordinates
│   └── tai_mapping.yaml              # TAI codes, platform codes, vessel-to-class maps
├── src/
│   ├── app.py                        # Orchestrator: PugetSoundOSINT class
│   ├── api/
│   │   └── server.py                 # FastAPI app, HTML template, all API routes (~3500 lines)
│   ├── ingestion/
│   │   ├── feed_manager.py           # Async camera polling loop
│   │   └── wsdot_cameras.py          # WSDOT terminal camera metadata and URL generation
│   ├── detection/
│   │   └── vessel_detector.py        # YOLOv8 wrapper, bounding boxes, detection buffer
│   ├── tracking/
│   │   └── wsf_api.py                # WSDOT Vessels API client, vessel position/info models
│   └── reporting/
│       ├── tacrep.py                 # TACREP format generator and parser
│       ├── chatsurfer.py             # ChatSurfer HTTP client with queue and rate limiting
│       └── deconfliction.py          # Duplicate suppression and cross-source correlation
├── captures/                         # Saved camera frames, organized by date and feed ID
├── reports/
│   └── tacreps.log                   # Append-only TACREP output file
└── logs/
    └── osint.log                     # Application log
```

---

## 3. Entry Point and Startup

### run.py

Imports and calls `main()` from `src.app`.

### src/app.py — main()

Parses command-line arguments:

| Flag | Default | Purpose |
|------|---------|---------|
| `--config` / `-c` | `config/settings.yaml` | Path to settings file |
| `--log-level` | `INFO` | Logging verbosity |
| `--log-file` | `logs/osint.log` | Log output path |
| `--port` | `8080` | Web server port |
| `--no-web` | `false` | Disable the web dashboard |

After parsing, `main()` does the following in order:

1. Configures logging to both console and file.
2. Creates a `PugetSoundOSINT` instance with the settings YAML path.
3. Registers `SIGINT` and `SIGTERM` handlers that call `stop()`.
4. Calls `initialize()` — creates the FeedManager, ChatSurferClient, and VesselDetector.
5. Calls `start()` — starts the camera polling thread, starts the report queue thread, sends a check-in message.
6. Unless `--no-web` is set, creates the FastAPI app via `create_app()` and runs it with uvicorn on the main thread. This call blocks until the process receives a signal.
7. On signal, calls `stop()` — sends a check-out message, stops polling, drains the report queue, joins threads.

---

## 4. PugetSoundOSINT — The Orchestrator

**File:** `src/app.py`

This class holds references to every subsystem. It loads configuration from YAML, wires components together, and defines the frame callback that connects camera polling to detection and reporting.

### Configuration Loading

On construction, `PugetSoundOSINT` reads two YAML files:

- `config/settings.yaml` — detector settings, ChatSurfer settings, web server settings, WSDOT API settings, storage paths.
- `config/tai_mapping.yaml` — maps terminal names to TAI codes (e.g., "Point Defiance" → "BALDER") and vessel names to platform codes (e.g., "Tokitae" → "ORCA").

### initialize()

Creates each component:

- `FeedManager` — reads `config/cameras.yaml`, builds a `CameraFeed` object for each entry, sets the detection callback.
- `ChatSurferClient` — configured with callsign, mode, session cookie, room, server URL.
- `VesselDetector` — lazy-loaded YOLOv8. Created here if detection is enabled in config, but the model file itself loads on first inference.
- `TacrepDeconfliction` — initialized with a 120-second suppress window and 2-nautical-mile correlation radius.

### start()

- Calls `FeedManager.start()`, which spawns a thread running an asyncio event loop for camera polling.
- Calls `ChatSurferClient.start()`, which spawns a thread running the report queue worker.
- Calls `ChatSurferClient.check_in()`, which generates and sends a check-in message (`"PR01 ONSTA 1430 Z"`).

### _on_frame_captured(feed_id, frame, feed)

This is the callback registered with FeedManager. It fires every time a camera produces a frame. The sequence:

1. Look up the TAI code for this feed (from the feed's `tai_code` field, or by substring-matching the feed ID/name against `_tai_mapping`). If no TAI code is found, return without running detection.
2. If the detector is not loaded, return.
3. Run `VesselDetector.detect(frame, camera_id=feed_id)`. If no detections, return.
4. For each detection returned:
   - Call `TacrepDeconfliction.should_report()` with the TAI code, a vessel key (`"VISUAL_{feed_id}"`), source `"visual"`, and the camera coordinates.
   - If the deconfliction engine correlates this detection with a known API vessel, the vessel key is replaced with the vessel's name and confidence may be upgraded to `CONFIRMED`.
   - If `should_report()` returns true, save the annotated frame, generate a TACREP via `ChatSurferClient.report_detection()`, and record the report in the deconfliction engine.
   - If `should_report()` returns false (suppressed), skip sending but note any confidence upgrade.

---

## 5. Camera Feed Ingestion

### src/ingestion/feed_manager.py

#### CameraFeed

A dataclass representing one camera source:

```
name: str               # "Clinton Terminal"
id: str                 # "clinton"
url: str                # JPEG endpoint
coordinates: (lat, lon) # For map display and TAI correlation
refresh_sec: float      # Polling interval, default 30
tai_code: str | None    # Assigned TAI code
terminal_id: int | None # WSDOT terminal ID
enabled: bool           # Whether to poll this feed
```

Runtime state tracked per feed:
- `last_fetch` — timestamp of last poll attempt
- `last_frame` — numpy array of the last captured frame
- `last_frame_time` — datetime of last capture
- `consecutive_errors` — counter, resets on success
- `is_online` — set to false after 5 consecutive errors

#### FeedManagerConfig

```
cameras_config_path: "config/cameras.yaml"
storage_path: "./captures"
save_all_frames: true
max_concurrent_fetches: 10
request_timeout_sec: 15.0
max_consecutive_errors: 5
error_backoff_sec: 60.0
```

#### FeedManager

On construction, loads `config/cameras.yaml` and creates a `CameraFeed` for each entry. The YAML has two sections: `wsdot_terminals` (WSDOT-hosted JPEG cameras at ferry terminals) and `third_party_cameras` (other sources like Whidbey Telecom and HDOnTap).

`start()` spawns a background thread that runs an asyncio event loop. Inside that loop, `_polling_loop()` runs forever:

1. Iterate all feeds. For each, check `_should_poll()`:
   - Feed must be enabled.
   - Enough time must have elapsed since `last_fetch` (at least `refresh_sec`).
   - If the feed is in error state, apply exponential backoff: `error_backoff_sec * 2^(consecutive_errors - max_consecutive_errors)`.
2. For all feeds that pass the check, create async tasks bounded by a `Semaphore(max_concurrent_fetches)`.
3. Each task (`_fetch_feed`) does:
   - `GET feed.url` with aiohttp, timeout `request_timeout_sec`.
   - Decode the response bytes as a JPEG or PNG into a numpy array via OpenCV.
   - On success: update `last_frame`, `last_frame_time`, reset `consecutive_errors` to 0, set `is_online` to true. Optionally save the frame to disk. Call the frame callback (`_on_frame_captured`).
   - On failure: increment `consecutive_errors`. If it exceeds `max_consecutive_errors`, mark the feed offline.
4. Sleep 1 second, repeat.

Frame storage path: `./captures/{YYYY-MM-DD}/{feed_id}/{feed_id}_HHMMSS.jpg`

### src/ingestion/wsdot_cameras.py

Contains `WSDOTTerminal`, a dataclass with terminal metadata (name, terminal ID, URL slug, coordinates). A `camera_url` property generates the URL: `https://images.wsdot.wa.gov/wsf/{slug}/terminal/{slug}.jpg`.

`WSDOT_TERMINALS` is a dictionary of 22 terminals with their coordinates and IDs. `WSDOTCameraPoller` can discover which terminals are online by issuing HEAD requests.

---

## 6. Vessel Detection

**File:** `src/detection/vessel_detector.py`

### Data Types

**VesselType** (enum): `FERRY`, `BOAT`, `SHIP`, `SAILBOAT`, `UNKNOWN`

**DetectionStatus** (enum): `HIGH` (confidence >= 0.7), `MEDIUM` (>= 0.5), `LOW` (>= threshold)

**BoundingBox** (dataclass): `x1, y1, x2, y2` in pixel coordinates. Properties compute `width`, `height`, `center`, `area`.

**Detection** (dataclass):
```
detection_id: str           # "det_YYYYMMDDHHMMSS_XXXXXX"
vessel_type: VesselType
confidence: float           # 0.0 to 1.0
bbox: BoundingBox
class_name: str             # Raw YOLO class name
class_id: int               # YOLO class ID
timestamp: datetime
vessel_name: str | None     # Filled in if correlated with API vessel
vessel_id: int | None
heading_estimate: float | None
```

**DetectionResult** (dataclass):
```
camera_id: str
frame_timestamp: datetime
detections: list[Detection]
processing_time_ms: float
frame_shape: (height, width)
```

### VesselDetector

Constructor parameters:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `model_path` | `"yolov8n.pt"` | YOLO model file. Auto-downloads from ultralytics if missing. |
| `confidence_threshold` | `0.25` | Minimum confidence to keep a detection. |
| `iou_threshold` | `0.45` | Non-maximum suppression IOU threshold. |
| `device` | `"cpu"` | Inference device. Options: `"cpu"`, `"cuda:0"`, `"mps"`. |
| `vessel_classes_only` | `true` | Filter out non-vessel detections. |
| `img_size` | `640` | Input resolution for the model. |

**`detect(image, camera_id)`** — The model loads on first call. Runs `model.predict()` on the image. For each result box, extracts coordinates, class ID, class name, and confidence. Maps the YOLO class to a `VesselType` using two lookups:

- `VESSEL_CLASS_IDS`: maps COCO class 8 ("boat") to `VesselType.BOAT`.
- `MARITIME_CLASS_MAP`: keyword-based mapping for custom-trained models ("ferry" → `FERRY`, "ship" → `SHIP`, etc.).

Returns a `DetectionResult` with all detections and the inference time in milliseconds.

**`annotate(image, detections)`** — Draws bounding boxes on a copy of the image. Box color is based on confidence: green (>= 0.7), yellow (>= 0.5), orange (< 0.5). Labels show vessel name (if known) or type, plus confidence percentage.

**`detect_and_annotate(image, camera_id)`** — Runs both methods and returns a tuple of (DetectionResult, annotated image).

### DetectionBuffer

Buffers detection results across frames. Constructor takes `max_frames` (30) and `min_hits` (3). A detection must appear in at least `min_hits` frames within the buffer to be confirmed. This reduces false positives from transient detections.

---

## 7. WSDOT Vessels API Client

**File:** `src/tracking/wsf_api.py`

### Data Types

**VesselClass** (enum): Maps vessel classes to platform codes used in TACREPs.

| Enum | Platform Code | Vessels |
|------|--------------|---------|
| `JUMBO_MARK_II` | `WHALE` | Tacoma, Wenatchee, Puyallup |
| `SUPER` | `EAGLE` | Hyak, Kaleetan, Yakima, Elwha, Walla Walla, Spokane |
| `ISSAQUAH` | `SALMON` | Issaquah, Kittitas, Cathlamet, Kitsap, Sealth, Chelan |
| `OLYMPIC` | `ORCA` | Tokitae, Samish, Chimacum, Suquamish |
| `KWA_DI_TABIL` | `SEAL` | Chetzemoka, Kennewick, Salish |

`VESSEL_CLASSES` is a dictionary mapping all 23 WSF vessel names to their `VesselClass`.

**VesselPosition** (dataclass): Real-time vessel state.
```
vessel_id: int
vessel_name: str
mmsi: int | None
latitude: float
longitude: float
speed: float                    # knots
heading: float                  # degrees
in_service: bool
at_dock: bool
departing_terminal_id: int | None
departing_terminal_name: str | None
arriving_terminal_id: int | None
arriving_terminal_name: str | None
scheduled_departure: datetime | None
eta: datetime | None
eta_source: str | None          # "Schedule" or "Estimated"
left_dock: datetime | None
timestamp: datetime
vessel_class: VesselClass       # Resolved from vessel name on construction
platform_code: str              # Resolved from vessel class on construction
```

**VesselInfo** (dataclass): Static vessel specifications (length, beam, horsepower, max speed, capacities, year built).

### WSFVesselsClient

Async HTTP client for the WSDOT Vessels REST API.

Base URL: `https://www.wsdot.wa.gov/ferries/api/vessels/rest`

Constructor takes an API key (free, registered at wsdot.wa.gov) and a timeout (15 seconds). Uses aiohttp internally with lazy session creation.

Methods:

- `get_vessel_locations()` — `GET /vessellocations?apiaccesscode={key}`. Returns a list of `VesselPosition` objects. The API updates positions roughly every 5 seconds.
- `get_vessel_basics(use_cache=True)` — `GET /vesselbasics`. Returns a dict of `VesselInfo` objects. Cached for 24 hours since vessel specs rarely change.
- `get_vessel_verbose()` — `GET /vesselverbose`. Returns combined location and info in one call.
- `get_active_vessels()` — Filters `get_vessel_locations()` to `in_service=True`.
- `get_vessels_near_terminal(terminal_id, radius_nm)` — Returns vessels departing from, arriving at, or docked at a terminal.

WSDOT returns dates in a format like `"/Date(1234567890000-0800)/"`. The `_parse_datetime()` method handles this with regex extraction and timezone conversion to UTC.

### VesselTracker

A wrapper around `WSFVesselsClient` that polls on a loop. Constructor takes the API key, a poll interval (5 seconds), and a flag to filter for active-only vessels.

`start()` loads vessel info once, then creates an asyncio task that:
1. Calls `get_vessel_locations()` (or `get_active_vessels()`).
2. Updates an internal `positions` dictionary keyed by vessel ID.
3. Fires an `on_position_update` callback with the positions dict.
4. Sleeps for the poll interval.
5. On error, fires `on_error` and continues.

Query methods: `get_vessel_by_name(name)`, `get_vessels_at_dock()`, `get_vessels_underway()`.

---

## 8. TACREP Generation

**File:** `src/reporting/tacrep.py`

### Format

```
CALLSIGN//SERIAL//NUM_TARGETS//CONFIDENCE//PLATFORM//TAI//HHMM//REM: remarks
```

Example:
```
PR01//I005//1//PROBABLE//ORCA//BALDER//1430//REM: VES TOKITAE EN ROUTE BAINBRIDGE 12KTS
```

### ConfidenceLevel (enum)

`CONFIRMED`, `PROBABLE`, `POSSIBLE`, `UNKNOWN`

### TacrepReport (dataclass)

```
callsign: str                   # "PR01"
serial_number: int              # Auto-incremented
num_targets: int
confidence: ConfidenceLevel
platform: str                   # Platform code (ORCA, WHALE, etc.)
tai: str                        # Target Area of Interest code
timestamp: datetime             # UTC
remarks: str
vessel_name: str | None
direction: str | None           # INBOUND, OUTBOUND, DOCKED
loading_state: str | None       # LOADING, OFFLOADING, IDLE
vehicle_count: int | None
```

`format_serial()` returns `"I001"`, `"I002"`, etc. `format_timestamp()` returns `"HHMM"` in UTC. `to_tacrep_string()` assembles the full formatted string.

### TacrepGenerator

Holds a callsign and a serial counter. Methods:

- `create_report(...)` — Takes target count, confidence, platform, TAI, remarks, and optional fields. Increments the serial counter, creates a `TacrepReport`, returns it.
- `from_detection(detection, tai, platform_mapping)` — Converts a detection dict to a `TacrepReport`. Maps confidence float to `ConfidenceLevel` (>= 0.7 → CONFIRMED, >= 0.5 → PROBABLE, >= 0.3 → POSSIBLE, else UNKNOWN). Maps vessel class through platform_mapping dict.
- `generate_checkin()` — Returns `"PR01 ONSTA HHMM Z"`.
- `generate_checkout()` — Returns `"PR01 OFF-STA HHMM Z"`.
- `reset_serial(value=0)` — Resets counter for a new shift.

### parse_tacrep(tacrep_string)

Utility function. Parses a formatted TACREP string back into a dict with fields: callsign, serial, num_targets, confidence, platform, tai, timestamp_z, remarks.

---

## 9. ChatSurfer Integration

**File:** `src/reporting/chatsurfer.py`

### ChatSurferConfig (dataclass)

```
enabled: bool                       # true
callsign: str                       # "PR01"
mode: str                           # "stdout", "file", "chatsurfer"
server_url: str                     # "https://chatsurfer.nro.mil"
session: str                        # SESSION cookie from browser
room: str                           # Chat room name
nickname: str                       # "OSINT_Bot"
domain: str                         # "chatsurferxmppunclass"
classification: str                 # "UNCLASSIFIED//FOUO"
output_file: str                    # "reports/tacreps.log"
image_base_url: str                 # "http://localhost:8080/images/"
image_storage_path: str             # "./captures/"
min_report_interval_sec: float      # 30.0
max_retries: int                    # 3
retry_delay_sec: float              # 1.0
```

### send_chatsurfer_message(message, config, image_url)

Standalone function. POSTs to `{server_url}/api/chatserver/message` with:

- Headers: `cookie: SESSION={session}`, `Content-Type: application/json`
- Body: `{"classification": ..., "message": ..., "domainId": ..., "nickName": ..., "roomName": ...}`
- If an image URL is provided, appends `"\n[IMG] {url}"` to the message body.
- Returns true on HTTP 200 or 204, false on any error.
- SSL verification is disabled (`verify=False`) to handle self-signed certificates.

### ChatSurferClient

The main reporting interface. Holds a `TacrepGenerator`, a `Queue`, and a worker thread.

**`start()`** — Spawns a background thread running `_worker_loop()`.

**`_worker_loop()`** — Pulls `(report, image_url)` tuples from the queue. For each:
1. Writes to the output file (always, as a backup).
2. If `session` and `room` are configured, POSTs to ChatSurfer via `send_chatsurfer_message()`.
3. If mode is `"stdout"`, prints the formatted report to console.
4. Continues until a `None` sentinel is received.

**`stop()`** — Pushes `None` into the queue, joins the thread with a 5-second timeout.

**`report_detection(detection, tai, image_path, force)`** — The main entry point for detection-triggered reports:
1. Checks rate limiting: if the last report for this TAI was less than `min_report_interval_sec` ago, returns `None` (unless `force=True`).
2. Converts the local image path to a URL using `image_base_url`.
3. Creates a `TacrepReport` from the detection dict via `TacrepGenerator.from_detection()`.
4. Pushes `(report, image_url)` onto the queue.
5. Updates `_last_report_time[tai]`.
6. Returns the report.

**`save_detection_image(frame, tai, detection)`** — Saves the frame (with bounding box drawn if a detection dict is provided) to `{image_storage_path}/{TAI}_{YYYYMMDD}_{HHMMSS}.jpg`. Returns the path.

**`check_in()` / `check_out()`** — Generate check-in/check-out messages from the TACREP generator and send them via the queue.

---

## 10. Deconfliction Engine

**File:** `src/reporting/deconfliction.py`

### Purpose

The system has two independent paths that can both identify the same vessel: YOLOv8 detection from camera feeds ("visual" source) and the WSDOT Vessels API ("api" source). Without deconfliction, the same ferry could generate two TACREPs — one from each path — within seconds of each other. The deconfliction engine prevents this and also correlates detections across sources to upgrade confidence.

### ReportRecord (dataclass)

```
tai: str                    # Target Area code
vessel_key: str             # Vessel name or "VISUAL_{camera_id}"
source: str                 # "api" or "visual"
platform: str
confidence: str
timestamp: float            # time.time()
serial: str                 # TACREP serial number
vessel_name: str | None
camera_id: str | None
lat: float | None
lon: float | None
correlated: bool            # True if matched across both sources
```

### TacrepDeconfliction

Constructor parameters:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `suppress_window_sec` | 120.0 | Time window in which duplicate reports are suppressed |
| `correlation_radius_nm` | 2.0 | Maximum distance in nautical miles for cross-source matching |
| `max_records` | 500 | Memory cap for stored report records |

Internal state:
- `_reports`: dict keyed by `(tai, vessel_key)` → `ReportRecord`
- `_api_vessel_cache`: dict keyed by vessel name → `{lat, lon, at_dock, terminals, vessel_class, speed, timestamp}`

**`update_api_vessels(vessels)`** — Called by the API endpoint handler when vessel positions are fetched. Stores lat/lon and metadata for each vessel in the cache.

**`correlate_visual_with_api(camera_lat, camera_lon)`** — Given a camera's coordinates, iterates the API vessel cache and finds the closest vessel within `correlation_radius_nm`. Uses the Haversine formula (radius = 3440.065 nautical miles). Skips entries older than 60 seconds. Returns the vessel name, or `None` if no match is within range.

**`should_report(tai, vessel_key, source, camera_lat, camera_lon)`** — The decision function. Returns a tuple: `(should_send: bool, correlated_name: str, upgraded_confidence: str | None)`.

Logic:

1. If the source is `"visual"`, attempt correlation: call `correlate_visual_with_api()` with the camera coordinates. If a match is found, replace the vessel key with the vessel's name.
2. Look up `(tai, vessel_key)` in `_reports`.
3. If a record exists and is within the suppress window:
   - If the record's source differs from the current source and it has not already been marked as correlated: mark it `correlated=True`, set confidence to `"CONFIRMED"`. Return `(False, vessel_name, "CONFIRMED")`. The report is suppressed, but the upgrade is noted.
   - Otherwise (same source, or already correlated): return `(False, vessel_name, None)`. Suppressed.
4. If no record exists, or the record has expired: return `(True, vessel_name, None)`. Send a new report.

**`record_report(tai, vessel_key, source, platform, confidence, serial, ...)`** — Called after a TACREP is sent. Stores a `ReportRecord` in `_reports`. Calls `_prune()` to clean up.

**`_prune()`** — Removes records older than 3x the suppress window. If total records exceed `max_records`, removes the oldest entries.

**`get_active_reports()`** — Returns non-expired records as a list of dicts with `tai`, `vessel_key`, `source`, `platform`, `confidence`, `age_sec`, `correlated`, `vessel_name`.

**`_distance_nm(lat1, lon1, lat2, lon2)`** — Haversine distance in nautical miles.

### Cross-Source Correlation Example

```
T=0s:   API reports "Tokitae" at (47.976, -122.352) in TAI "CLINTON".
        → TACREP sent: PR01//I001//1//CONFIRMED//ORCA//CLINTON//1430//REM: VES TOKITAE...
        → Recorded: ("CLINTON", "Tokitae") source="api"

T=20s:  Clinton camera captures frame. YOLOv8 detects boat, confidence 0.85.
        → should_report("CLINTON", "VISUAL_clinton", "visual", 47.975, -122.351)
        → correlate_visual_with_api(47.975, -122.351) finds Tokitae at 0.06nm → match
        → vessel_key becomes "Tokitae"
        → Lookup ("CLINTON", "Tokitae"): record exists, 20s old (< 120s window)
        → Different source (api vs visual), not yet correlated
        → Mark correlated=True, confidence="CONFIRMED"
        → Return (False, "Tokitae", "CONFIRMED")
        → No new TACREP sent. Both sources agree.
```

---

## 11. Web Server and API

**File:** `src/api/server.py`

### create_app(osint_app)

Takes the `PugetSoundOSINT` instance and returns a `FastAPI` app. The app stores the orchestrator reference in `app.state.osint`. Several module-scoped variables inside `create_app` hold detection and reporting state:

```python
_detector = None                     # VesselDetector instance
_detection_enabled = False
_detection_results = {}              # feed_id → DetectionResult
_tacrep_log = []                     # Recent TACREPs, capped at 200
_tacrep_max_log = 200
_deconfliction = osint_app._deconfliction   # Shared with orchestrator (single instance)
```

Additional state on `app.state`:
```python
app.state.vessel_client = None       # WSFVesselsClient, created on first /api/vessels call
app.state.vessel_cache = {}          # Cached vessel positions
app.state.vessel_cache_time = 0      # Cache timestamp
app.state.tai_areas = []             # TAI polygon definitions (in-memory)
```

A terminal-to-TAI mapping dict (`_terminal_tai_map`) maps terminal names to TAI codes as a fallback when no user-defined TAI code is assigned.

### Shutdown Hook

```python
@app.on_event("shutdown")
async def cleanup():
    if app.state.vessel_client:
        await app.state.vessel_client.close()
```

### API Endpoints

#### Status and Configuration

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Serves the HTML dashboard (CONFIG_PAGE template) |
| `GET` | `/api/status` | Returns running state, callsign, camera counts, report count |
| `GET` | `/api/config` | Returns the full config dict |
| `POST` | `/api/config` | Merges submitted JSON into config. Handles ChatSurfer fields, resets vessel client on API key change |

#### Camera Feeds

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/cameras` | List of all cameras with ID, name, online status, enabled flag, TAI code |
| `GET` | `/api/feeds` | Per-feed status: name, enabled, online, TAI code, last update time, error count, coordinates |
| `GET` | `/api/feeds/{feed_id}/snapshot` | Latest frame as JPEG (quality 85%). Headers disable caching. Returns 404 if no frame available |

#### Vessel Tracking

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/vessels` | All vessel positions from WSDOT API. Creates client on first call. Caches for 3 seconds. Triggers `_generate_api_tacreps()` and `_deconfliction.update_api_vessels()` |
| `GET` | `/api/vessels/{vessel_id}` | Single vessel from cache |

#### Check-in/Check-out

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/checkin` | Generates and sends check-in message |
| `POST` | `/api/checkout` | Generates and sends check-out message |
| `POST` | `/api/test-report` | Sends a test TACREP (TAI=TEST, platform=ORCA, confidence=PROBABLE) |

#### Detection

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/detection/status` | Whether detection is enabled, model state, confidence threshold, count of results |
| `POST` | `/api/detection/enable` | Toggle detection. Accepts `enable`, `confidence_threshold`, `device`. Creates or destroys the VesselDetector |
| `GET` | `/api/detection/detect/{feed_id}` | Runs detection on the latest frame from a feed. Returns DetectionResult as JSON |
| `GET` | `/api/detection/detect/{feed_id}/annotated` | Runs detection and returns annotated JPEG. Includes `X-Detection-Count` and `X-Processing-Time-Ms` headers |
| `GET` | `/api/detection/results` | All stored detection results |
| `POST` | `/api/detection/scan-all` | Scans all enabled, online feeds. For each detection, runs deconfliction, correlates with API vessels, generates TACREPs. Returns per-feed results summary |

#### TACREP Reporting

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/tacrep/recent` | Returns recent TACREPs from in-memory log. Optional `since` query parameter (ISO datetime) filters newer entries. Default: last 50 |
| `POST` | `/api/tacrep/manual` | Accepts `num_targets`, `confidence`, `platform`, `tai`, `remarks`. Creates and sends a TACREP |

#### TAI Area Management

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/tai-areas` | Returns all TAI area definitions (code, polygon, camera list) |
| `POST` | `/api/tai-areas` | Creates or replaces a TAI area. Requires `code` and `polygon`. Updates camera TAI assignments |
| `DELETE` | `/api/tai-areas/{code}` | Removes a TAI area and clears TAI assignments from its cameras |

#### Deconfliction

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/deconfliction/status` | Suppress window, correlation radius, list of active report records, cache counts |

#### ChatSurfer

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/chatsurfer/test` | Tests ChatSurfer connection. Accepts `session`, `room`, `nickname`, `domain`, `server_url`. Sends a test message and reports success or failure |

### Internal Functions

**`_generate_api_tacreps(vessels)`** — Called by the `/api/vessels` handler. For each in-service vessel that is not at dock:
1. Derive TAI code from `_get_vessel_tai()`.
2. Check `_deconfliction.should_report()`.
3. If should report: build remarks (vessel name, terminals, speed), create a `TacrepReport` with `CONFIRMED` confidence (API data is authoritative), send via ChatSurfer, record in deconfliction, log to `_tacrep_log`.

**`_get_vessel_tai(vessel)`** — Checks user-configured TAI codes first, falls back to `_terminal_tai_map`, defaults to `"PUGETSOUND"`.

**`_log_tacrep(message, feed_id, feed_name, source)`** — Appends an entry to `_tacrep_log` with timestamp, message, source, feed ID, and feed name. Caps at 200 entries (FIFO).

### HTML Template

The `CONFIG_PAGE` variable contains the entire frontend as a single HTML document (~2700 lines). It uses:

- CSS custom properties for theming (dark theme, background `#0a0e14`, cards `#12171f`).
- Leaflet.js with CartoDB Dark Matter tiles for the map.
- Leaflet Draw for TAI polygon creation.
- Seven tabs: Live Feeds, Reporting, TAI Map, Cameras, TAI Codes, ChatSurfer, Live Output.
- JavaScript polling: status every 5 seconds, feeds at a configurable rate (2-30 seconds), TACREPs every 3 seconds, vessels on map update.

---

## 12. Vessel Reporting Pipeline: API Fetch, TACREP Generation, and Visual Deconfliction

This section traces the full lifecycle of how vessel positions become TACREPs and how the two source paths (WSDOT API and YOLOv8 cameras) are multiplexed through the deconfliction engine.

### 12.1 TAI Assignment: How Data Gets Aligned to Target Areas

Every TACREP includes a TAI (Target Area of Interest) code — a short string like `"CLINTON"` or `"BALDER"` that identifies where the observation occurred. The deconfliction engine keys its suppress window on `(tai, vessel_key)` pairs, so TAI assignment determines which reports can be deduplicated against each other.

Drawing a TAI polygon on the map is the primary way to define a TAI. When an operator draws a polygon and labels it, the system automatically associates both data paths — cameras and API vessels — with that TAI. No separate configuration is needed.

#### 12.1.1 Drawing a TAI: What Happens

The web UI's TAI Map tab lets an operator draw a polygon on the Leaflet map to define a TAI area. The flow:

1. The operator types a TAI code (e.g., `"CLINTON"`) and clicks "Draw TAI."
2. Leaflet Draw enters polygon mode. The operator clicks map points to define vertices, double-clicks to close.
3. On polygon completion (`L.Draw.Event.CREATED`), the frontend runs `assignCamerasToTai()`. This iterates every camera in `CAMERA_LOCATIONS` and tests whether each camera's lat/lon coordinates fall inside the drawn polygon using `isPointInPolygon()` — a ray-casting point-in-polygon algorithm. Cameras inside the boundary are collected into a list.
4. The frontend calls `saveTaiArea()`, which POSTs to `/api/tai-areas` with three fields: the TAI code string, the polygon vertex coordinates as `[[lat, lon], ...]`, and the list of camera IDs that passed the containment check.
5. The backend handler ([server.py:2910](src/api/server.py#L2910)) stores the polygon and camera list in `app.state.tai_areas`. It iterates the camera list and writes `feed.tai_code = code` on each camera's `CameraFeed` object in the FeedManager.

This single action produces two effects:
- **Cameras inside the polygon** get `feed.tai_code` set. All visual detections from those cameras will carry this TAI code.
- **The polygon is stored server-side.** The backend uses it for server-side point-in-polygon testing against API vessel GPS positions (see 12.1.2).

On `DELETE /api/tai-areas/{code}`, the backend clears `feed.tai_code = None` for every camera that was in the deleted TAI, and removes the polygon from the in-memory list.

#### 12.1.2 How Each Data Path Gets Its TAI

**API path** — `_get_vessel_tai(vessel)` runs four lookups in order:

1. **User-configured TAI codes** from `config.tai_codes` (set via the TAI Codes tab or `tai_mapping.yaml`). Each entry maps a terminal name to a TAI code. If the vessel's departing or arriving terminal matches, that code is returned.
2. **Drawn TAI polygons** — the backend runs `_point_in_polygon()` (a server-side ray-casting implementation) against every polygon in `app.state.tai_areas` using the vessel's latitude and longitude. If the vessel's GPS position falls inside a polygon, that polygon's TAI code is returned.
3. **Built-in terminal map** (`_terminal_tai_map`) — a hardcoded dict of 19 terminal names to TAI codes (e.g., `"Seattle"` → `"SEATTLE"`, `"Clinton"` → `"CLINTON"`, `"Mukilteo"` → `"MUKILTEO"`).
4. **Default** — if nothing matches, the vessel gets `"PUGETSOUND"`.

The polygon check at step 2 means that drawing a TAI on the map is sufficient for API vessels to be assigned to it. A ferry whose GPS coordinates are inside the polygon gets that TAI code, regardless of its terminal names.

**Visual path (scan-all)** — the scan-all handler reads `feed.tai_code` from the `CameraFeed` object. If the feed has no TAI code, it falls back to server-side polygon containment: it runs `_point_in_polygon()` against the camera's coordinates and every stored polygon. If the camera is inside a polygon, it gets that TAI code. Otherwise, it defaults to `"UNASSIGNED"`.

**Visual path (frame callback)** — the `_on_frame_captured` callback in [app.py:163](src/app.py#L163) checks `feed.tai_code` first. If that is `None`, it tries to match the feed ID or feed name against the keys in `_tai_mapping` (loaded from `tai_mapping.yaml` on startup). The matching is substring-based: if `"tahlequah"` appears in `feed_id.lower()` or `feed.name.lower()`, and `_tai_mapping` has a `"Tahlequah"` → `"THOR"` entry, the camera gets TAI code `"THOR"`. If neither lookup produces a result, the callback returns without generating any report — frames from cameras with no TAI assignment are silently dropped. (The frame callback does not check polygons directly, but cameras inside a drawn polygon already have `feed.tai_code` set from the draw action.)

#### 12.1.3 Deconfliction Engine: Single Instance

The orchestrator (`app.py`) creates a single `TacrepDeconfliction` instance on startup. The web server (`server.py`) references the same instance rather than creating its own. This means all report paths — frame callback detections, scan-all detections, and API vessel TACREPs — share the same deconfliction state. When the API path records a report for a vessel, the frame callback path sees it during its suppress window check, and vice versa.

#### 12.1.4 End-to-End: One Polygon Covers Both Paths

Drawing a polygon labeled `"ALPHA"` around a terminal area:
1. Cameras inside the polygon get `feed.tai_code = "ALPHA"`. Their visual detections produce TACREPs with `TAI=ALPHA`.
2. API vessels whose GPS position is inside the polygon get `TAI=ALPHA` from `_get_vessel_tai()`. Their TACREPs carry `TAI=ALPHA`.
3. Both paths record in the same deconfliction engine under `("ALPHA", vessel_key)`. Cross-source suppression and confidence upgrades work because the TAI codes match and the deconfliction state is shared.

No additional configuration in the TAI Codes tab is needed. The TAI Codes tab (`config.tai_codes`) exists as an override for cases where terminal-name matching should take priority over polygon containment.

**Filtering consequence:**
- In `app.py`, a camera with no TAI code (not inside any polygon, no match in `_tai_mapping`) produces zero TACREPs. The frame is captured and saved, but detection is never run.
- In the scan-all handler, a camera with no TAI code and not inside any polygon gets `"UNASSIGNED"`. Detection runs, but the resulting TACREP will not deconflict against API-sourced reports.

**TAI areas are in-memory only.** Polygon definitions and camera assignments stored via `POST /api/tai-areas` live in `app.state.tai_areas` and on `CameraFeed` objects in memory. They are lost on process restart. The `tai_mapping.yaml` file persists across restarts but only affects the `app.py` frame callback path.

### 12.2 The Trigger: Frontend Polls `/api/vessels`

The Leaflet map in the browser calls `GET /api/vessels` on a recurring interval. This is the only call path that fetches vessel positions from the WSDOT API. The endpoint does three things in sequence: fetch positions, feed the deconfliction cache, and generate TACREPs. All three happen inside a single request handler.

### 12.3 Fetching Vessel Positions

The `get_vessels()` handler in [server.py:2972](src/api/server.py#L2972):

1. Looks for a WSDOT API key in the config dict (checks `wsdot_api_key`, `wsf_api_key`, and `wsdot_api.api_key`). If none is found, returns `{}`.
2. Checks a 3-second cache (`app.state.vessel_cache`). If the cache is fresh, returns it and skips the rest.
3. Creates a `WSFVesselsClient` on first call (stored in `app.state.vessel_client`).
4. Calls `await client.get_vessel_locations()`, which hits `GET https://www.wsdot.wa.gov/ferries/api/vessels/rest/vessellocations?apiaccesscode={key}`. The WSDOT API returns positions for every vessel in the fleet, updated roughly every 5 seconds.
5. Converts each `VesselPosition` object to a dict containing: `id`, `name`, `latitude`, `longitude`, `speed`, `heading`, `in_service`, `at_dock`, `departing_terminal`, `arriving_terminal`, `eta`, `vessel_class`, `platform_code`.
6. Stores the result in `app.state.vessel_cache`.

### 12.4 Feeding the Deconfliction Cache

Immediately after caching, the handler calls:

```python
_deconfliction.update_api_vessels(vessels)
```

This iterates every vessel in the response and writes its position into `_api_vessel_cache`, a dict keyed by vessel name. Each entry stores: `lat`, `lon`, `at_dock`, `departing_terminal`, `arriving_terminal`, `vessel_class`, `speed`, and a `timestamp` set to `time.time()`. This cache is what the deconfliction engine uses later to correlate visual detections with known vessel positions.

### 12.5 Generating TACREPs from API Data — Filtering and Reporting

The handler then calls `_generate_api_tacreps(vessels)` ([server.py:3374](src/api/server.py#L3374)). This function iterates every vessel in the response and applies three filters:

- Skip vessels where `in_service` is false.
- Skip vessels where `at_dock` is true.
- Skip vessels with no lat/lon.

For each remaining vessel (underway and in service):

1. **Derive TAI code** via `_get_vessel_tai(vessel)`. This checks user-configured TAI codes first (from the config's `tai_codes` dict, matching against the vessel's departing or arriving terminal name), falls back to a built-in `_terminal_tai_map` dict (19 terminal names mapped to TAI codes like `"SEATTLE"`, `"BAINBRIDGE"`, `"CLINTON"`, etc.), and defaults to `"PUGETSOUND"` if no match.

2. **Check deconfliction** via `_deconfliction.should_report(tai=tai, vessel_key=vessel_name, source="api")`. Since this is an API source, no camera coordinates are passed and no visual correlation happens. The engine checks if `(tai, vessel_name)` already has a report record within the 120-second suppress window:
   - If a record exists from the same source ("api") and is within the window: returns `(False, None, None)`. Suppressed.
   - If a record exists from a different source ("visual") and has not been correlated yet: marks the record `correlated=True`, sets confidence to `"CONFIRMED"`, returns `(False, None, "CONFIRMED")`. Suppressed but noted.
   - If no record exists, or the window has expired: returns `(True, None, None)`. Proceed to send.

3. **Build the TACREP**. Constructs remarks from the vessel name, terminal pair, and speed (e.g., `"VES TOKITAE MUKILTEO TO CLINTON 14.2KTS"`). Sets direction from the arriving terminal (e.g., `"EN ROUTE CLINTON"`). Confidence is always `CONFIRMED` for API-sourced reports since the WSDOT data includes the vessel's identity.

4. **Send the report** via `osint._chatsurfer.send_report(report)`, which pushes the `TacrepReport` onto the ChatSurfer queue for delivery.

5. **Record the report** in the deconfliction engine via `_deconfliction.record_report(tai, vessel_name, "api", platform, "CONFIRMED", serial, ...)`. This stores a `ReportRecord` keyed by `(tai, vessel_name)` with a timestamp, so future calls to `should_report()` for the same vessel in the same TAI will be suppressed for 120 seconds.

6. **Log the TACREP** to the in-memory `_tacrep_log` with `source="api"`, which the frontend's Live Output tab polls via `GET /api/tacrep/recent`.

The result: every time the frontend refreshes the vessel map, any underway ferry that hasn't been reported in the last 120 seconds gets a TACREP sent to ChatSurfer.

### 12.6 Visual Detection Path — Filtering and TAI Lookup

The second source path runs through two code paths that apply different filters.

**scan-all handler** ([server.py:3244](src/api/server.py#L3244)) — triggered by `POST /api/detection/scan-all` from the web UI. Iterates `osint.feed_manager.feeds` and applies two filters:
- Skip feeds where `feed.enabled` is false.
- Skip feeds where `feed.last_frame` is `None` (no frame captured yet, or feed is offline).

For feeds that pass, it runs YOLOv8 on the latest frame. The TAI code comes from `feed.tai_code or "UNASSIGNED"`. If the camera has no TAI assignment (never set via `POST /api/tai-areas` and no `tai_code` in `cameras.yaml`), the TACREP carries `TAI=UNASSIGNED`. Detection still runs and reports still send, but they will not deconflict against API-sourced reports for any real TAI.

**Frame callback** ([app.py:163](src/app.py#L163)) — triggered by the FeedManager polling loop whenever a camera produces a frame. Applies a harder filter:
- Reads `feed.tai_code`. If `None`, attempts a substring match of the feed ID and feed name against keys in `_tai_mapping` (from `tai_mapping.yaml`).
- If neither lookup produces a TAI code, the callback **returns immediately** — no detection is run, no TACREP is generated. The frame is still saved to disk if `save_all_frames` is true, but nothing else happens.

This means the frame callback path silently drops all frames from cameras without TAI assignments, while the scan-all path processes them but tags them `"UNASSIGNED"`.

For both paths, each detection produces a vessel key starting as `"VISUAL_{feed_id}"`. The handler calls `_deconfliction.should_report(tai, "VISUAL_{feed_id}", "visual", camera_lat, camera_lon)`. Because `source="visual"` and camera coordinates are provided, the deconfliction engine runs `correlate_visual_with_api(camera_lat, camera_lon)` before checking the suppress window.

### 12.7 Cross-Source Correlation

`correlate_visual_with_api()` ([deconfliction.py:107](src/reporting/deconfliction.py#L107)) iterates the `_api_vessel_cache` (populated in step 12.4) and computes the Haversine distance between the camera's coordinates and each vessel's last known position. It skips entries older than 60 seconds. If a vessel is within `correlation_radius_nm` (2.0 nautical miles), it returns that vessel's name. If multiple vessels are within range, it returns the closest one.

If correlation succeeds, the deconfliction engine replaces the vessel key. Instead of `"VISUAL_clinton"`, the key becomes `"Tokitae"` (or whichever vessel is closest). This is the mux point — the visual detection is now keyed to the same identity the API path uses.

### 12.8 The Suppress Window Decision

With the (possibly replaced) vessel key, the engine looks up `(tai, vessel_key)` in its `_reports` dict:

**Case 1: API already reported this vessel.** A record exists with `source="api"`, timestamp within 120 seconds. The current source is `"visual"`. The sources differ and the record is not yet correlated. The engine:
- Sets `existing.correlated = True`
- Sets `existing.confidence = "CONFIRMED"`
- Returns `(False, "Tokitae", "CONFIRMED")`

The visual detection does not generate a new TACREP. But the caller notes the correlation — it logs `"Suppressed visual TACREP for Tokitae in CLINTON (already reported via API)"`.

**Case 2: No prior report exists (or the window expired).** The engine returns `(True, "Tokitae", None)`. The handler proceeds to send a TACREP. It enriches the detection dict with the correlated vessel name (`det_dict["vessel_name"] = "Tokitae"`) and records the report in deconfliction with `source="visual"`. The TACREP log entry gets `source="visual+api (Tokitae)"`.

**Case 3: Visual already reported, and another visual detection arrives.** Same source, within window. Returns `(False, ..., None)`. Suppressed.

**Case 4: Visual reported first, then the API path encounters the same vessel.** The API path calls `should_report(tai, "Tokitae", "api")`. A record exists from "visual" source. Different sources, not yet correlated. The engine marks it correlated and returns `(False, None, "CONFIRMED")`. The API TACREP is suppressed, but the correlation is noted.

### 12.9 The Reporting Output

Regardless of which path sends the TACREP, the report goes through the same delivery:

1. `ChatSurferClient.send_report(report)` pushes `(report, image_url)` onto a `Queue`.
2. The worker thread pops it and:
   - Appends the formatted TACREP string to `reports/tacreps.log`.
   - If a ChatSurfer session and room are configured: POSTs to `{server_url}/api/chatserver/message` with the SESSION cookie, room name, and classification header.
   - If mode is `"stdout"`: prints the formatted string to console.
3. `_log_tacrep()` adds the message to the in-memory `_tacrep_log` (capped at 200 entries) with the source label, which the frontend polls.

### 12.10 Timing and Interaction

The two paths run on different schedules:

- **API path**: fires every time the frontend polls `/api/vessels` (driven by the JavaScript map update interval). Each poll triggers `_generate_api_tacreps()` which checks every underway vessel against the deconfliction window. A given vessel gets one API-sourced TACREP, then is suppressed for 120 seconds.

- **Visual path**: fires when `scan-all` is called (manually from the UI or on a timer) or when the FeedManager callback processes a new frame. Camera feeds poll every 30 seconds by default, so a frame callback can trigger detection every 30 seconds per camera.

- **Deconfliction cache freshness**: the `_api_vessel_cache` entries are timestamped. `correlate_visual_with_api()` skips entries older than 60 seconds. If the frontend stops polling `/api/vessels`, the cache goes stale and visual detections can no longer correlate — they fall back to the generic `"VISUAL_{feed_id}"` key and generate their own TACREPs with lower confidence.

- **Suppress window vs. rate limit**: the deconfliction engine's 120-second suppress window operates on `(tai, vessel_key)` pairs. The ChatSurfer client has a separate rate limit of 30 seconds per TAI (across all vessels in that TAI). Both must pass for a report to send. A vessel can pass deconfliction but still be rate-limited if another vessel in the same TAI was reported 10 seconds ago.

### 12.11 Summary Diagram

```
Frontend polls GET /api/vessels
         │
         ▼
    ┌─────────────────────────────────────────┐
    │  Fetch from WSDOT Vessels API           │
    │  Cache result (3s TTL)                  │
    └────┬───────────────────────┬────────────┘
         │                       │
         ▼                       ▼
    ┌────────────────┐    ┌──────────────────────────────┐
    │  update_api_   │    │  _generate_api_tacreps()     │
    │  vessels()     │    │                              │
    │                │    │  For each underway vessel:   │
    │  Writes to     │    │  1. Derive TAI               │
    │  _api_vessel_  │    │  2. should_report(           │
    │  cache         │    │       tai, name, "api")      │
    │                │    │  3. If yes → send TACREP     │
    │                │    │  4. record_report()          │
    └───────┬────────┘    └──────────────────────────────┘
            │
            │  (cache now holds vessel positions)
            │
            ▼
    ┌──────────────────────────────────────────────────┐
    │  Later: scan-all or frame callback               │
    │                                                  │
    │  YOLOv8 detects vessel at camera coordinates     │
    │                                                  │
    │  should_report(tai, "VISUAL_clinton", "visual",  │
    │                camera_lat, camera_lon)           │
    │         │                                        │
    │         ▼                                        │
    │  correlate_visual_with_api(camera_lat, camera_lon)│
    │  → searches _api_vessel_cache by Haversine       │
    │  → finds "Tokitae" at 0.06nm                     │
    │  → vessel_key becomes "Tokitae"                  │
    │         │                                        │
    │         ▼                                        │
    │  Lookup (tai, "Tokitae") in _reports             │
    │  → API already reported 20s ago                  │
    │  → Different source, not yet correlated          │
    │  → Mark correlated, upgrade CONFIRMED            │
    │  → Return (False, "Tokitae", "CONFIRMED")        │
    │  → Visual TACREP suppressed                      │
    └──────────────────────────────────────────────────┘
```

---

## 13. Configuration Reference

### config/settings.yaml

```yaml
cameras_config: config/cameras.yaml
storage_path: ./captures
save_all_frames: true

chatsurfer:
  enabled: true
  callsign: PR01
  mode: stdout
  output_file: reports/tacreps.log
  image_base_url: http://localhost:8080/images/
  min_report_interval_sec: 30.0

detector:
  enabled: true
  model_path: yolov8n.pt
  confidence_threshold: 0.25
  device: cpu

web:
  enabled: true
  host: 0.0.0.0
  port: 8080

wsdot_api:
  enabled: true
  base_url: https://www.wsdot.wa.gov/ferries/api/vessels/rest/
  poll_interval_sec: 5.0

logging:
  level: INFO
  file: logs/osint.log
```

### config/cameras.yaml

Two sections:

**wsdot_terminals** — Each entry:
```yaml
- name: "Clinton Terminal"
  id: clinton
  terminal_id: 5
  url: https://images.wsdot.wa.gov/wsf/clinton/terminal/clinton.jpg
  enabled: true
  coordinates:
    lat: 47.9750
    lon: -122.3519
  refresh_sec: 30
  notes: "Serves Mukilteo-Clinton route"
```

**third_party_cameras** — Same structure, different URL patterns (Whidbey Telecom, HDOnTap).

### config/tai_mapping.yaml

```yaml
tai_codes:
  BALDER:
    terminal: "Point Defiance"
    terminal_id: 16
    coordinates: {lat: ..., lon: ...}
    cameras: [...]
  THOR:
    terminal: "Tahlequah"
    terminal_id: 21
    coordinates: {lat: ..., lon: ...}
    cameras: [...]

platform_codes:
  ORCA:
    class: "Olympic"
    vessels: [Tokitae, Samish, Chimacum, Suquamish]
  WHALE:
    class: "Jumbo Mark II"
    vessels: [Tacoma, Wenatchee, Puyallup]
  # ... etc.

confidence_levels:
  - CONFIRMED
  - PROBABLE
  - POSSIBLE
  - UNKNOWN
```

---

## 14. Dependencies

| Package | Version | Use |
|---------|---------|-----|
| numpy | >= 1.24.0 | Frame arrays |
| opencv-python | >= 4.8.0 | Image decode/encode, annotation drawing |
| Pillow | >= 10.0.0 | Image handling (fallback) |
| PyYAML | >= 6.0 | Configuration parsing |
| aiohttp | >= 3.9.0 | Async HTTP for camera polling and WSDOT API |
| fastapi | >= 0.109.0 | Web framework |
| uvicorn | >= 0.27.0 | ASGI server |
| python-multipart | >= 0.0.6 | Form data parsing |
| sqlalchemy | >= 2.0.0 | ORM (imported, not used for storage) |
| alembic | >= 1.13.0 | Database migrations (imported, not used) |
| ultralytics | >= 8.0.0 | YOLOv8 inference |
| torch | >= 2.0.0 | Neural network backend |
| python-dateutil | >= 2.8.0 | Date parsing |
| requests | >= 2.31.0 | Sync HTTP for ChatSurfer |
| pytest | >= 7.4.0 | Testing |
| pytest-asyncio | >= 0.23.0 | Async test support |
| black | >= 23.0.0 | Code formatting |
| ruff | >= 0.1.0 | Linting |

---

## 15. Threading Model

The process runs three threads:

1. **Main thread** — Runs the uvicorn/FastAPI server. Blocks on `uvicorn.run()`. All API request handlers execute here. Detection inference (YOLOv8) runs synchronously on this thread when triggered by an API call.

2. **FeedManager thread** — Runs an asyncio event loop. Polls camera feeds concurrently (up to 10 at once via semaphore). Calls the frame callback (`_on_frame_captured`) from within this loop, which may run detection and queue reports.

3. **ChatSurfer worker thread** — Pulls from a `Queue`. Writes reports to file, POSTs to ChatSurfer, and/or prints to stdout. Runs until it receives a `None` sentinel.

There is no thread pool, no process pool, and no shared lock (the queue handles thread safety for report delivery). The `TacrepDeconfliction` instance is shared across all three threads — the main thread writes to it during API TACREP generation and scan-all, while the FeedManager thread writes to it during frame callback detection. Detection results and the TACREP log are also written from both threads. The GIL serializes dict writes and list appends, so this works in practice without explicit locking.

---

## 16. Error Handling

### Camera Feeds
- Each feed tracks `consecutive_errors`. On failure, the counter increments. After 5 failures, the feed is marked offline.
- Backoff: `error_backoff_sec * 2^(errors - max_errors)`. A feed with 7 consecutive errors waits `60 * 2^2 = 240` seconds before the next attempt.
- On success, errors reset to 0 and the feed goes back online.

### WSDOT API
- HTTP errors are caught and logged. The vessel cache falls back to the previous result if a request fails.
- 401 errors (invalid API key) are raised as exceptions.
- The WSDOT date format parser (`"/Date(...)/"`) returns `None` for unparseable values.

### ChatSurfer
- Reports are always written to the file log, even if the HTTP POST fails.
- POST failures are logged but do not raise exceptions.
- The retry mechanism (`max_retries=3`, `retry_delay_sec=1.0`) is available in the config but the current `send_chatsurfer_message()` implementation does not retry — it returns false on failure.

### Detection
- If the model fails to load, the error is caught and returned as an HTTP 500.
- If a feed has no frame, the endpoint returns 404.
- Detection results are stored per feed, overwriting previous results.

---

## 17. Security Notes

- No authentication on any endpoint. The server binds to `0.0.0.0` by default.
- No CORS middleware is configured.
- ChatSurfer credentials (SESSION cookie) are stored in memory and in the config dict. They are readable via `GET /api/config`.
- The WSDOT API key is stored in the config dict and readable via `GET /api/config`.
- SSL verification is disabled for ChatSurfer requests.
- No rate limiting on API endpoints.
- No input validation models (uses raw `Request.json()`).

---

## 18. Data Lifecycle

All state is in memory. Nothing persists to a database.

- **Camera frames** — Saved to disk under `./captures/` if `save_all_frames` is true. Not cleaned up automatically.
- **TACREP log** — In-memory list capped at 200 entries (oldest evicted). Also appended to `reports/tacreps.log` on disk.
- **Detection results** — In-memory dict, one entry per feed, overwritten on each detection.
- **Deconfliction records** — In-memory dict capped at 500 entries. Pruned when records exceed 3x the suppress window age.
- **Vessel cache** — In-memory dict with a 3-second TTL for positions and a 24-hour TTL for vessel info.
- **TAI areas** — In-memory list. Lost on restart.
- **Configuration** — Loaded from YAML on startup, modified in memory via `POST /api/config`. Changes are not written back to YAML.
