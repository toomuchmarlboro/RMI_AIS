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
  - **Phase 3 (Ready):** `vesselfinder.com` public-page scraper — no API key — for vessel particulars and voyage data.
  - **Recommended hybrid (default):** aisstream.io for precise positions + background VesselFinder enrichment for GT/DWT/LOA/beam/year/flag, the master data AIS never transmits.
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

### Switching to the `vesselfinder.com` Scraper (Phase 3)

No API key required — this source reads VesselFinder's public vessel pages.

1. Set the mode and an MMSI watchlist in `.env`:
   ```env
   AIS_DATA_SOURCE=vesselfinder
   VESSELFINDER_TARGET_MMSIS=636092932,232003233
   VESSELFINDER_POLL_INTERVAL_SEC=120
   ```
   Or use the dashboard's **Feed Source → Change → VesselFinder** modal, which
   switches live without a restart.
2. Tune behaviour in `config.yaml` under `vesselfinder:`.

> [!IMPORTANT]
> **VesselFinder gives exact vessel data but only approximate positions.**
> Its public pages round coordinates to **whole degrees** (±0.5°, ~55 km) and
> publish no heading — the live map draws vessels as server-side image tiles, so
> precise coordinates are never sent to the browser.
>
> | Exact | Approximate / missing |
> |---|---|
> | Name, IMO, call sign, ship type, flag | Latitude & longitude (whole degrees) |
> | GT, DWT, LOA, beam, year built | Heading (not published) |
> | Draught, destination, ETA, SOG, COG, nav status, fix time | Historical tracks (not public) |
>
> This is an upstream change, not a parsing gap: the reference scraper
> [`derhuerst/scrape-vesselfinder`](https://github.com/derhuerst/scrape-vesselfinder)
> reads the same element and documents full precision, but that same vessel now
> returns whole degrees. See `AGENTS.md` §7.3.
>
> Observations recorded from this source are tagged `POS:APPROX` in `raw_nmea`.
> Set `vesselfinder.include_approximate_position: false` to record static and
> voyage data only.
>
> **Recommended setup:** the hybrid below — `aisstream` for positions,
> VesselFinder for particulars. It is the default.

**Scraping conduct:** only `robots.txt`-permitted paths are requested, with a
real User-Agent, a minimum 2 s gap between requests, a 30 s floor on the poll
interval, and detail pages cached for 6 hours.

### ⭐ Recommended: aisstream for positions, VesselFinder for particulars

This is the default configuration, and it plays to each source's strength:

| | aisstream.io | VesselFinder |
|---|---|---|
| Position, SOG, COG, heading, nav status | ✅ precise, real-time | ❌ whole degrees, no heading |
| Name, IMO, call sign, destination, ETA, draught | ✅ (AIS Types 5/24) | ✅ |
| **GT, DWT, year built, flag** | ❌ **never transmitted by AIS** | ✅ |
| LOA / beam | ⚠️ antenna offsets only | ✅ registered values |

A `ParticularsEnricher` runs in the background, off the ingestion path: as the
position feed reports vessels, eligible ones are queued, looked up on
vesselfinder.com, and their master data merged into the database and the live
dashboard.

**AIS stays authoritative for everything it transmits.** The enricher only
fills gaps — it never overwrites a live destination, ETA, draught or position
with VesselFinder's possibly-staler copy. The one deliberate exception is
LOA/beam, where VesselFinder's registered dimensions supersede values derived
from AIS antenna offsets.

Configure it under `enrichment:` in `config.yaml`:

```yaml
enrichment:
  enabled: true
  scope: "recording"     # watchlist | recording | all
  max_per_hour: 250
  refresh_after_days: 30
  retry_after_days: 7
```

> [!IMPORTANT]
> **Scope matters.** A wide aisstream bounding box discovers tens of thousands
> of vessels — this project's Indonesia box has seen 27,000+, most of them
> once. `scope: all` would mean tens of thousands of lookups, so the default
> `recording` restricts enrichment to vessels you are actually recording, and
> `max_per_hour` caps the rest with a sliding one-hour window.

> [!WARNING]
> **vesselfinder.com throttles persistent clients.** During development a
> sustained burst of roughly 100 lookups/hour (at 2 s spacing) was enough for
> the site to stop accepting connections from this machine entirely — not an
> HTTP 403, just refused/hanging TCP connections, while every other host stayed
> reachable. Defaults were therefore lowered to **5 s spacing and 60
> lookups/hour**, and a **circuit breaker** pauses enrichment for 15 minutes
> after 5 consecutive transport failures.
>
> If `GET /api/state` shows `"paused": true` or a climbing `errors` count, you
> are likely being throttled — leave it alone and it resumes on its own.
> **Position recording is never affected**; enrichment is strictly best-effort
> and runs off the ingestion path.

Results are persisted (`particulars_source`, `particulars_updated_utc`), so
each vessel is looked up **once** and then left alone until
`refresh_after_days`; vessels VesselFinder has no record of back off for
`retry_after_days`. Coverage therefore accumulates across runs.

Because enrichment is asynchronous, a vessel's **first** observations in a
session can land before its lookup completes — in a 70 s cold-start test, 183
of 1,324 records carried tonnage. Run longer, or record the same fleet again,
and coverage climbs as the persisted set grows.

To force a lookup immediately, bypassing scope and freshness checks:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/vessels/636092932/enrich
```

Live progress is reported in the `enrichment` block of `GET /api/state`.

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

All 73 unit tests cover:
- NMEA 0183 checksum verification & multi-sentence fragment reassembly
- `pyais` decoding for Type 1 position reports & Type 5 static voyage data
- SQLite thread-safe WAL storage and CRUD operations
- Simulator physics step & AIVDM encoding
- Session recording manager & CSV/JSON exporters
- VesselFinder payload/HTML parsing, de-duplication and rate-limit guards
- Enrichment scope/budget/back-off, the AIS-authoritative merge policy, and
  the vessels-table schema migration
  (all offline — no test makes a network call)

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
│   ├── sources/            # Data sources (Simulator, Aisstream, VesselFinder)
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
