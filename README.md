# RMI AIS — Vessel Tracking & Recording Intelligence

> **Recordable Marine Intelligence (RMI AIS)**  
> Offline-first AIS vessel data recorder and monitoring dashboard mirroring VesselFinder / MarineTraffic formats with timestamped high-frequency observation sessions, raw NMEA archiving, and CSV/JSON export.

---

## 🌟 Key Features

- **High-Fidelity AIS Recording:** Records every incoming AIS message with dual timestamps (System Receive UTC + AIS Message UTC).
- **VesselFinder Data Matching:**
  - **Dynamic Telemetry:** Speed (SOG in knots), Course (COG relative to North), True Heading (HDG), Draught (meters), Navigational Status (e.g. *Sailing* / *Under way using engine*), Coordinates (Latitude, Longitude).
  - **Static & Voyage Data:** Ship Name (e.g. *Virgo 18*), Ship Class/Type (*Passenger/Ro-Ro Cargo Ship*), MMSI, IMO Number, Call Sign, Destination, ETA.
  - **Vessel Master Particulars:** Gross Tonnage (*GT*), Deadweight (*DWT tonnes*), Length Overall (*LOA* in meters), Beam/Width (*meters*), Year Built, Flag State.
  - **Observation Metadata:** Observation ID (e.g. `OBS-20261012-001`), Observation Window (e.g. `23:00:00 — 23:18:00`), Observer Station (`Station-Desktop-01`).
- **Interactive Web Dashboard:** Real-time nautical map (Leaflet.js) + Vessel Cards + High-frequency NMEA stream log.
- **Multi-Source Architecture:**
  - **Phase 1 (Active):** Realistic NMEA Simulator simulating authentic `!AIVDM` sentences with geographic physics.
  - **Phase 2 (Ready):** `aisstream.io` global real-time WebSocket feed (free API key).
  - **Phase 3 (Plug & Play):** MarineTraffic REST / NMEA push endpoints (`PS07`, `PS01`, `PS06`, `VD02`).
- **Export Capabilities:** One-click instant export of any recorded session to standardized CSV or structured JSON.

---

## 🚀 Quick Start (Windows Desktop)

### 1. Prerequisites
- Python 3.11+ (Tested on Python 3.14)

### 2. Activate Virtual Environment & Install Dependencies

```powershell
# In PowerShell (inside C:\Users\LENOVO THINKPAD T14\Documents\RMI_AIS)
.\.venv\Scripts\Activate.ps1

# Or install dependencies if setting up on a new machine:
python -m pip install -r requirements.txt
```

### 3. Run the Application

```powershell
python -m src.main
```

Then open your browser to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 🖥️ How to Use the Dashboard

1. **View the Live Fleet:**
   - Watch vessels move along their courses in the Sunda Strait / Java Sea region (including *Virgo 18*, *Batam Jet 2*, *Samudera Nusantara*).
   - Click on any vessel marker on the map or in the table to display its **Vessel Particulars Card** (Gross Tonnage, Dimensions, Speed, Draught, Destination, ETA).
2. **Start an Observation Session:**
   - Enter your **Observer Station ID** (default: `Station-Desktop-01`).
   - Click **Start Recording**.
   - The status badge will turn red and blink with your active session ID (e.g. `OBS-20261012-154535-3E7E92`).
3. **Capture Telemetry:**
   - The system records every incoming AIS message, logging raw NMEA sentences and decoded telemetry to SQLite (`data/rmi_ais.db`).
4. **Stop & Save:**
   - Click **Stop & Save**. The observation window (e.g. `23:00 — 23:18`) is locked and finalized.
5. **Export Data:**
   - Scroll down to the **Recorded Sessions & Export** table.
   - Click **CSV** or **JSON** to download your session dataset directly.

---

## ⚙️ Configuration & Feed Switching

Configuration is stored in `config.yaml` and `.env`:

### Switching to Live `aisstream.io` (Phase 2)
1. Get a free API key at [https://aisstream.io](https://aisstream.io).
2. Create or edit `.env` in the project root:
   ```env
   AIS_DATA_SOURCE=aisstream
   AISSTREAM_API_KEY=your_free_api_key_here
   DEFAULT_STATION_ID=Station-Desktop-01
   DASHBOARD_PORT=8000
   ```
3. Restart `python -m src.main`. The system will automatically connect to global real-time WebSockets!

### Simulator Settings (`config.yaml`)
To adjust simulation speed or add new vessels, edit `config.yaml`:
```yaml
simulator:
  update_interval_sec: 3.0
  vessels:
    - mmsi: 525019876
      name: "Virgo 18"
      ship_type: "Passenger/Ro-Ro Cargo Ship"
      imo: 9123456
      call_sign: "YCGR"
      draught: 5.2
      destination: "BAKAUHENI"
      gross_tonnage: 9989
      length: 134.0
      beam: 22.0
      initial_lat: -5.9320
      initial_lon: 105.9980
      initial_sog: 13.9
      initial_cog: 107.3
```

---

## 🧪 Running Automated Tests

All modules include unit and integration tests:

```powershell
.\.venv\Scripts\pytest.exe -v
```

All 13 unit tests cover:
- NMEA 0183 checksum verification & multi-sentence fragment reassembly
- `pyais` decoding for Type 1 position reports & Type 5 static voyage data
- SQLite thread-safe WAL storage and CRUD operations
- Simulator physics step & AIVDM encoding
- Session recording manager & CSV/JSON exporters

---

## 📁 Project Structure

```
RMI_AIS/
├── AGENTS.md               # Project rules, architecture & roadmap
├── README.md               # Setup & usage guide
├── config.yaml             # Vessel profiles & simulator configuration
├── .env.example            # Environment variables template
├── requirements.txt        # Python package dependencies
│
├── src/
│   ├── main.py             # Application launcher
│   ├── models/             # Pydantic data models (vessel, nmea, observation)
│   ├── decoder/            # NMEA sentence parser & pyais AIVDM decoder
│   ├── sources/            # Data sources (Simulator, Aisstream, MarineTraffic)
│   ├── storage/            # SQLite database manager (WAL mode)
│   ├── recorder/           # Session recording manager & CSV/JSON exporter
│   └── ui/                 # FastAPI server & Leaflet web dashboard
│
├── tests/                  # Pytest test suite (13 passing tests)
├── data/
│   ├── rmi_ais.db          # SQLite database (auto-created)
│   └── exports/            # Exported CSV / JSON files
└── scripts/
    └── test_live_server.py # End-to-end integration test utility
```
