# AGENTS.md — RMI AIS Vessel Tracker

> **Project:** Recordable Marine Intelligence — AIS Vessel Tracking System  
> **Created:** 2026-10-12  
> **Status:** Planning Phase

---

## 1. Project Overview

Build a **recordable vessel tracking system** that captures, timestamps, and stores AIS (Automatic Identification System) data for selected vessels — mirroring what VesselFinder / MarineTraffic display, but under our control with full recording, playback, and export capabilities.

### What This Is NOT
- Not a replacement for MarineTraffic or VesselFinder
- Not a live map (initially) — it's a **data recorder** first, UI second

### What This IS
- A timestamped AIS data recorder for selected vessels
- A system that can work **offline-first** with simulated/mock data
- Later upgradeable to consume free live AIS feeds (aisstream.io) and public web data (vesselfinder.com)

---

## 2. Data Schema — What Gets Recorded

Every vessel observation record must contain the following fields, matching VesselFinder's display format:

### 2.1 Dynamic Data (changes every few seconds)

| Field | Example | Source (NMEA) | AIS Message Type |
|---|---|---|---|
| **Speed** (SOG) | `13.9 kn` | Bits 50–59 (÷10) | Type 1, 2, 3 |
| **Course** (COG) | `107.3°` (relative to True North) | Bits 116–127 (÷10) | Type 1, 2, 3 |
| **Heading** (HDG) | `109°` | Bits 128–136 | Type 1, 2, 3 |
| **Navigation Status** | `Sailing` / `Moored` / `At Anchor` | Bits 38–41 (lookup) | Type 1, 2, 3 |
| **Latitude** | `37.8084` | Bits 89–115 (÷600000) | Type 1, 2, 3 |
| **Longitude** | `122.4082` | Bits 61–88 (÷600000) | Type 1, 2, 3 |
| **Timestamp** | `2026-10-12T23:00:00Z` | System clock / AIS second | Type 1, 2, 3 |

### 2.2 Static / Voyage Data (changes infrequently)

| Field | Example | Source (NMEA) | AIS Message Type |
|---|---|---|---|
| **Ship Name** | `Virgo 18` | Bits 112–231 (6-bit ASCII) | Type 5 |
| **Ship Type / Class** | `Passenger/Ro-Ro Cargo Ship` | Bits 232–239 (lookup) | Type 5 |
| **MMSI** | `525019876` | Bits 8–37 | All types |
| **IMO Number** | `9123456` | Bits 40–69 | Type 5 |
| **Call Sign** | `YCGR` | Bits 70–111 (6-bit ASCII) | Type 5 |
| **Draught** | `5.2 m` | Bits 302–309 (÷10) | Type 5 |
| **Destination** | `MERAK` | Bits 310–429 (6-bit ASCII) | Type 5 |
| **ETA** | `10-13 06:00` | Bits 274–293 | Type 5 |

### 2.3 Vessel Particulars (static, rarely changes)

| Field | Example | Notes |
|---|---|---|
| **Gross Tonnage** | `9989 GT` | From API / manual entry |
| **Deadweight (DWT)** | `3500 tonnes` | From API / manual entry |
| **Length (LOA)** | `134 m` | Derived from AIS dim A+B, or API |
| **Beam / Width** | `22 m` | Derived from AIS dim C+D, or API |
| **Year Built** | `2015` | From API (VD02 endpoint) |
| **Flag State** | `Indonesia` | From MMSI MID code or API |

### 2.4 Observation Metadata (our system adds this)

| Field | Example | Notes |
|---|---|---|
| **Observation ID** | `OBS-20261012-001` | Auto-generated UUID |
| **Observation Window** | `23:00 — 23:18` | Start/end of recording session |
| **Observer / Station** | `Station-Merak-01` | Which station/session recorded it |
| **Recording Mode** | `live` / `manual` / `simulation` | How the data was captured |

---

## 3. NMEA / AIS Format Reference

> [!IMPORTANT]
> AIS data is transmitted over VHF radio as NMEA 0183 sentences. Understanding this is critical because all AIS data originates from this protocol, and raw NMEA is what we archive for every observation.

### 3.1 NMEA 0183 Sentence Structure

```
!AIVDM,1,1,,B,19NWrrP02sbuuuuhM86hA0=n2<0:,0*12
  │     │ │ │ │  │                              │ │
  │     │ │ │ │  └─ AIS payload (6-bit ASCII)   │ └─ Checksum
  │     │ │ │ └─ Radio channel (A or B)         └─ Fill bits
  │     │ │ └─ Sequential msg ID (multi-part)
  │     │ └─ Fragment number (this sentence)
  │     └─ Total fragments needed
  └─ Sentence type (AIVDM = from other ships, AIVDO = own ship)
```

### 3.2 Key AIS Message Types We Care About

| Message Type | Name | Content | Broadcast Interval |
|---|---|---|---|
| **1, 2, 3** | Position Report (Class A) | Position, SOG, COG, heading, nav status | 2–10 seconds |
| **5** | Static & Voyage Data | Name, type, dimensions, draught, destination | Every 6 minutes |
| **18** | Position Report (Class B) | Position, SOG, COG (smaller vessels) | Every 30 seconds |
| **24** | Class B Static Data | Name, type, dimensions, call sign | Every 6 minutes |

### 3.3 Navigation Status Codes

```
0 = Under way using engine       8 = Under way sailing
1 = At anchor                    9 = Reserved (HSC)
2 = Not under command           10 = Reserved (WIG)
3 = Restricted manoeuvrability  11 = Power-driven towing astern
4 = Constrained by draught      12 = Power-driven pushing/towing
5 = Moored                      13 = Reserved
6 = Aground                     14 = AIS-SART, MOB, EPIRB
7 = Engaged in fishing          15 = Not defined (default)
```

---

## 4. Architecture — Phased Approach

### Phase 1: Offline Simulator (MVP — No API Key Needed)

```
┌────────────────────────────────────────────────────┐
│                   RMI AIS Tracker                  │
│                                                    │
│  ┌──────────────┐    ┌──────────────┐              │
│  │ NMEA         │───▶│ AIS Decoder  │              │
│  │ Simulator    │    │ (Parser)     │              │
│  │ (Mock Data)  │    └──────┬───────┘              │
│  └──────────────┘           │                      │
│                    ┌────────▼────────┐              │
│                    │ Data Store      │              │
│                    │ (SQLite/JSON)   │              │
│                    └────────┬────────┘              │
│                    ┌────────▼────────┐              │
│                    │ Recording       │              │
│                    │ Manager         │              │
│                    │ (Start/Stop/    │              │
│                    │  Timestamp)     │              │
│                    └────────┬────────┘              │
│                    ┌────────▼────────┐              │
│                    │ CLI / Simple UI │              │
│                    │ (View + Export) │              │
│                    └─────────────────┘              │
└────────────────────────────────────────────────────┘
```

**Deliverables:**
- NMEA sentence generator (simulates realistic vessel data)
- AIS decoder/parser (AIVDM → structured JSON)
- SQLite database with proper schema
- CLI tool to start/stop recording sessions
- Export to CSV/JSON

### Phase 2: Free Live Data (aisstream.io / AISHub)

```
┌────────────────┐
│ aisstream.io   │──WebSocket──▶ Same pipeline as Phase 1
│ (Free API key) │               but with REAL data
└────────────────┘
```

**Deliverables:**
- WebSocket client for aisstream.io (free, real-time, global AIS)
- Vessel selection/filtering (watch specific MMSIs or areas)
- Auto-recording when a watched vessel appears

### Phase 3: VesselFinder Scraper Enrichment (Free)

> MarineTraffic (Kpler) was dropped — no free tier, sales contact required.
> Phase 3 instead scrapes vesselfinder.com's public vessel pages, which need
> no key and carry the master data AIS never transmits.

```
┌──────────────────────────────┐
│ vesselfinder.com (public)    │
│                              │
│ /api/pub/click/<mmsi>  │─JSON─▶ name, IMO, type, flag, GT, DWT,
│                        │        LOA, beam, year, draught, dest,
│                        │        ETA, SOG, COG, nav status, fix time
│ /vessels/details/<imo> │─HTML─▶ call sign, AIS type, LOA/beam,
│                        │        coarse position (whole degrees)
│ /api/pub/vi2/<mmsi>    │─JSON─▶ voyage / last port call
└──────────────────────────────┘
```

**Deliverables:**
- `VesselFinderSource` watchlist poller + `VesselFinderScraper` HTTP client
- Rate limiting, TTL caching, retry/backoff (A02, A03)
- `ParticularsEnricher` background worker implementing the **hybrid
  architecture** (below), plus `POST /api/vessels/{mmsi}/enrich` for
  on-demand lookups

### Phase 3a: Hybrid Architecture (the intended runtime shape)

```
┌──────────────┐  precise positions   ┌─────────────────┐
│ aisstream.io │─────────────────────▶│ RecordingManager│──▶ SQLite
└──────────────┘  SOG/COG/HDG/status  └────────┬────────┘
                                               │ submit(mmsi)
                                      ┌────────▼────────────┐
                                      │ ParticularsEnricher │
                                      │  scope + budget     │
                                      └────────┬────────────┘
                                               │ GT, DWT, LOA, beam,
┌───────────────┐                              │ year built, flag
│ vesselfinder  │◀─────────────────────────────┘
└───────────────┘   (AIS never transmits these)
```

| Field group | Authoritative source |
|---|---|
| Position, SOG, COG, heading, nav status | **aisstream** (VesselFinder is whole-degree) |
| Destination, ETA, draught, name, IMO, call sign | **aisstream** (AIS Types 5/24); VesselFinder fills gaps only |
| GT, DWT, year built, flag | **VesselFinder** (absent from AIS entirely) |
| LOA / beam | **VesselFinder** registered values override AIS antenna offsets |

**Scope is mandatory, not optional.** A wide bounding box discovers tens of
thousands of vessels (27,000+ in this project's Indonesia box, most seen once),
so `enrichment.scope` defaults to `recording` and `max_per_hour` caps outbound
lookups on a sliding window. Outcomes persist in
`vessels.particulars_source` / `particulars_updated_utc` /
`enrichment_status` / `enrichment_attempted_utc`, so each vessel is looked up
once and coverage accumulates across runs.

Enrichment is skipped entirely when VesselFinder is itself the position source.

**Position precision limit (important):**
VesselFinder publishes **no precise coordinates** to unauthenticated clients —
its live map renders vessels into server-side PNG tiles, and the detail page
rounds `ship_lat`/`ship_lon` to **whole degrees** (±0.5°, ~55 km). Heading is
not published at all. Everything else is exact.

Therefore: **aisstream.io remains the position feed**; VesselFinder is the
particulars/voyage enrichment source. Observations recorded from VesselFinder
are tagged `POS:APPROX` in `raw_nmea`, and coordinates can be suppressed with
`vesselfinder.include_approximate_position: false`.

**Scraping conduct:** only paths permitted by `robots.txt` are read
(`/vessels/view/*`, `/aismap*`, `/worker1/*`, `/worker2/*` and the photo paths
are disallowed and never touched). Requests are serialised with a minimum
2 s spacing and a 30 s floor on the poll interval.

---

## 5. Project Rules

### 5.1 General Rules

| # | Rule | Rationale |
|---|---|---|
| R01 | **Python 3.11+** as primary language | Ecosystem support for AIS/NMEA libs |
| R02 | **No paid API calls until Phase 3** | Use simulator (Phase 1) and free APIs (Phase 2) first |
| R03 | **All data must be timestamped in UTC** | Consistency across time zones |
| R04 | **SQLite for storage** (upgrade to PostgreSQL later if needed) | Zero-config, portable, good enough for MVP |
| R05 | **Every module must have unit tests** | Non-negotiable for data integrity |
| R06 | **Config via `.env` file or `config.yaml`** | Never hardcode API keys or secrets |
| R07 | **Modular architecture** — each component is a separate module | Swap simulator for real API without rewriting |
| R08 | **Type hints everywhere** | Maintainability |
| R09 | **Record raw NMEA sentences alongside decoded data** | Audit trail; can re-decode later |
| R10 | **Git — commit after every working feature** | Incremental progress |

### 5.2 Data Rules

| # | Rule | Rationale |
|---|---|---|
| D01 | **MMSI is the primary vessel identifier** | It's the universal AIS key |
| D02 | **Store both raw and decoded forms** | Raw NMEA + parsed JSON |
| D03 | **Timestamps must include: AIS timestamp + system receive time** | AIS time can differ from wall clock |
| D04 | **Navigation status stored as both code and human label** | `5` AND `"Moored"` |
| D05 | **Speed in knots, dimensions in meters, angles in degrees** | Match maritime conventions |
| D06 | **Gross Tonnage unit is GT (not kg)** | GT is a volume measurement, not mass |
| D07 | **Observation windows (start–end) are mandatory for recording sessions** | Core requirement from spec |
| D08 | **Vessel particulars can be manually entered OR fetched from a source** | Phase 1 has no API, so manual entry must work; manual entry outranks the enricher |
| D09 | **AIS is authoritative for anything AIS transmits** | Scraped voyage/dynamic data may be staler than the live feed; the enricher fills gaps only |

### 5.3 API Integration Rules

| # | Rule | Rationale |
|---|---|---|
| A01 | **Abstract all data sources behind a common interface** | `DataSource` ABC → `SimulatorSource`, `AisStreamSource`, `VesselFinderSource`; built via `sources/factory.py` |
| A02 | **Rate-limit all outbound calls** | aisstream has fair-use limits; scraping must stay polite (min 2 s spacing, 30 s poll floor) |
| A03 | **Cache vessel particulars aggressively** | Static data rarely changes; detail pages cached 6 h, click payloads 60 s |
| A04 | **Log every outbound call with timestamp, endpoint, and response status** | Debugging + conduct audit |
| A05 | **Graceful degradation: if a source is down, keep recording from cache** | Don't lose a session because of network hiccups |
| A06 | **Scrapers must honour `robots.txt` and send a real User-Agent** | Disallowed paths are never fetched |
| A07 | **Never record scraped coordinates without a precision tag** | VesselFinder rounds to whole degrees; tagged `POS:APPROX` |

### 5.4 Code & File Structure Rules

| # | Rule | Rationale |
|---|---|---|
| F01 | **Flat-ish module layout** (no over-engineering) | Keep it navigable |
| F02 | **`/src` for source, `/tests` for tests, `/data` for SQLite/exports** | Convention |
| F03 | **`/docs` for additional documentation** | Architecture diagrams, API notes |
| F04 | **`/scripts` for one-off utilities** | NMEA test generators, data importers |
| F05 | **README.md always reflects current state** | Living document |

---

## 6. Proposed File Structure

```
RMI_AIS/
├── AGENTS.md                    # This file — project rules & plan
├── README.md                    # Project overview & quick start
├── requirements.txt             # Python dependencies
├── .env.example                 # Template for secrets/config
├── config.yaml                  # App configuration
│
├── src/
│   ├── __init__.py
│   ├── main.py                  # Entry point
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── vessel.py            # Vessel data model (Pydantic)
│   │   ├── observation.py       # Observation/recording session model
│   │   └── nmea.py              # NMEA sentence model
│   │
│   ├── decoder/
│   │   ├── __init__.py
│   │   ├── ais_decoder.py       # AIVDM → structured data
│   │   └── nmea_parser.py       # Raw NMEA sentence parser
│   │
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py              # Abstract DataSource interface
│   │   ├── simulator.py         # Phase 1: Mock NMEA generator
│   │   ├── aisstream.py         # Phase 2: aisstream.io WebSocket
│   │   ├── vesselfinder.py      # Phase 3: vesselfinder.com scraper
│   │   └── factory.py           # Mode → DataSource construction
│   │
│   ├── recorder/
│   │   ├── __init__.py
│   │   ├── session.py           # Recording session manager
│   │   ├── enricher.py          # Background particulars enrichment
│   │   └── exporter.py          # Export to CSV/JSON/Excel
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── database.py          # SQLite operations
│   │   └── migrations.py        # Schema versioning
│   │
│   └── ui/
│       ├── __init__.py
│       └── cli.py               # Command-line interface
│
├── tests/
│   ├── __init__.py
│   ├── test_decoder.py
│   ├── test_simulator.py
│   ├── test_recorder.py
│   └── test_models.py
│
├── data/
│   ├── rmi_ais.db               # SQLite database (gitignored)
│   └── exports/                 # Exported CSV/JSON files
│
├── scripts/
│   └── generate_test_nmea.py    # Utility to create test NMEA data
│
└── docs/
    ├── architecture.md          # Detailed architecture notes
    ├── nmea_reference.md        # NMEA/AIS format cheat sheet
    └── vesselfinder_scraping.md # VF endpoint/field reference
```

---

## 7. VesselFinder Scraping Reference (for Phase 3)

### 7.1 Endpoints Used

| Endpoint | Auth | Format | Returns |
|---|---|---|---|
| `/api/pub/click/<mmsi>` | none | JSON | Identity, particulars, SOG, COG, draught, destination, ETA, nav status, last-fix time |
| `/vessels/details/<imo>` | none | HTML | Call sign, AIS type, flag, LOA/beam, current draught, whole-degree position |
| `/api/pub/vi2/<mmsi>` | none | JSON | Voyage / last port call (next port, ETA, ATD) |

MMSI → IMO is resolved from the click payload's `imo` field. Vessels **without**
an IMO (small craft, many ferries and tugs) are served at the same path by
MMSI, so the scraper falls back to `/vessels/details/<mmsi>` — important for
this project's Indonesian ferry fleet, much of which has no IMO number.

The position/SOG/COG bootstrap lives in a `<div id="djson" data-json='…'>`
element on the detail page.

### 7.2 Click Payload Field Map

| Key | Meaning | Notes |
|---|---|---|
| `name` | Vessel name | Echoes the MMSI string when unknown |
| `imo` / `y` / `gt` / `dw` | IMO, year built, gross tonnage, deadweight | `0` means unknown |
| `type` | Ship type text | `"unknown type"` means unknown |
| `country` / `a2` | Flag state / ISO-2 code | |
| `al` / `aw` | Length overall / beam (m) | `0` means unknown |
| `ss` / `cu` | SOG (kn) / COG (deg) | `-1` means unknown |
| `draught` | Current draught × 10 | `128` → 12.8 m |
| `drm` | Max draught (string, m) | |
| `dest` / `etaTS` | Destination / ETA (unix) | ETA rendered as `MM-DD HH:MM` |
| `.ns` | Navigation status code | `-1` means unknown → status 15 |
| `ts` | Last position report (unix UTC) | Used for de-duplicating observations |

### 7.3 Limits & Conduct
- **No precise position.** Map vessels are rendered into server-side PNG tiles;
  the detail page rounds `ship_lat`/`ship_lon` to whole degrees (±0.5°, ~55 km).
  **No heading** is published. Use aisstream.io for real tracks.

  > **This is a deliberate upstream change, not a parsing bug.**
  > [`derhuerst/scrape-vesselfinder`](https://github.com/derhuerst/scrape-vesselfinder)
  > reads the same `#djson` element and documents full precision
  > (`ship_lat: 52.4449, ship_lon: 13.15839`) for MMSI 211627650. Re-checking
  > that exact vessel on 2026-10-14 returns `ship_lat: 52, ship_lon: 13`.
  > VesselFinder has since reduced public coordinate precision site-wide —
  > verified across IMO and non-IMO vessels, satellite and terrestrial
  > coverage, and fixes as fresh as "0 min ago". Don't re-attempt this.

- `/api/pub/weather/at/<mmsi>` returns live wind/wave/temp **at** the vessel
  (`wsp`, `wdir`, `temp`, `htsgw`) but discloses no coordinates — the server
  knows the precise position and will not return it.

- **The site throttles persistent clients, silently.** A sustained ~100
  lookups/hour at 2 s spacing caused vesselfinder.com to stop accepting TCP
  connections from the dev machine altogether (no 403 — just hangs/refusals,
  while every other host stayed reachable). Hence: 5 s minimum spacing,
  `max_per_hour: 60`, and a circuit breaker that pauses enrichment for 15 min
  after 5 consecutive transport failures. Treat timeouts as back-pressure, not
  as "vessel not found" — the two are reported separately
  (`enrichment_status` of `error` vs `not_found`; HTTP 502 vs 404).
- **No bounding-box query**, so the source is a watchlist poller keyed by MMSI.
- **No historical track** endpoint is publicly reachable (`/api/pub/dm3/` returns
  `{"success": false}` unauthenticated).
- **Conduct:** honour `robots.txt`, real User-Agent, min 2 s between requests,
  30 s floor on poll interval, detail pages cached 6 h.

---

## 8. Free Data Sources for Development

| Source | Type | Cost | Data Quality | Best For |
|---|---|---|---|---|
| **Built-in Simulator** | Mock NMEA | Free | Synthetic | Phase 1 development & testing |
| **aisstream.io** | WebSocket | Free (API key) | Real global AIS | Phase 2 live testing; **the position feed** |
| **vesselfinder.com** | Web scrape | Free (no key) | Exact particulars, coarse position | Phase 3 particulars/voyage enrichment |
| **AISHub** | REST/TCP | Free (give-to-get) | Real aggregated AIS | Alternative to aisstream |
| **RTL-SDR + AIS-catcher** | Hardware | ~$30 dongle | Local live AIS | Ultimate DIY option |

---

## 9. Technology Stack

| Component | Technology | Why |
|---|---|---|
| Language | Python 3.11+ | Rich AIS/NMEA ecosystem |
| Data Models | Pydantic v2 | Validation, serialization, type safety |
| AIS Decoding | `pyais` library | Battle-tested AIVDM decoder |
| Database | SQLite (via `sqlite3` or `SQLAlchemy`) | Zero-config, portable |
| WebSocket Client | `websockets` | For aisstream.io (Phase 2) |
| HTTP Client | `httpx` | Async-capable, for the VesselFinder scraper |
| CLI | `click` or `typer` | Nice CLI UX |
| Config | `python-dotenv` + `pyyaml` | `.env` for secrets, YAML for settings |
| Testing | `pytest` | Standard |
| Linting | `ruff` | Fast, modern |

---

## 10. Definition of Done — Phase 1 (MVP)

- [ ] Can generate realistic mock NMEA sentences for N configurable vessels
- [ ] Can decode AIVDM Type 1/2/3 (position) and Type 5 (static/voyage) messages
- [ ] Stores decoded data in SQLite with proper schema
- [ ] Stores raw NMEA alongside decoded records
- [ ] Recording sessions have start/stop with timestamped windows
- [ ] Can list all recorded vessels and their observations
- [ ] Can export a session to CSV and JSON
- [ ] All recorded fields match the spec in Section 2
- [ ] Unit tests pass for decoder, simulator, and recorder
- [ ] README reflects how to install and run

---

## 11. Glossary

| Term | Definition |
|---|---|
| **AIS** | Automatic Identification System — mandatory transponder on ships |
| **MMSI** | Maritime Mobile Service Identity — 9-digit vessel radio ID |
| **IMO** | International Maritime Organization number — permanent vessel ID |
| **NMEA 0183** | Serial communication standard for marine electronics |
| **AIVDM** | AIS VHF Data-link Message — the NMEA sentence carrying AIS data |
| **SOG** | Speed Over Ground (knots) |
| **COG** | Course Over Ground (degrees, true north) |
| **HDG** | True Heading (degrees) |
| **GT** | Gross Tonnage — a measure of internal ship volume, NOT weight |
| **DWT** | Deadweight Tonnage — max cargo weight a ship can carry |
| **LOA** | Length Overall — total ship length in meters |
| **Draught** | Depth of ship below waterline (meters) |
| **VHF** | Very High Frequency radio (AIS uses 161.975 & 162.025 MHz) |

---

## 12. Resolved Architectural Decisions

> [!NOTE]
> Discussed and finalized on 2026-10-12.

1. **UI Priority:** **Simple Web Dashboard** included from Phase 1. Users can start/stop recordings, view live incoming vessel data, see voyage particulars, and export recordings directly from their browser.
2. **Observation Granularity:** **Record every AIS message.** High-fidelity capture (no artificial downsampling), storing both raw NMEA and decoded telemetry with dual timestamps (system UTC + AIS message time).
3. **Station Topology:** **Single station** for MVP (desktop local recorder), with an optional configurable station/observer identifier (e.g. `DESKTOP-STATION-01`) attached to every observation session.
4. **Data Source Progression:** 
   - **Phase 1:** Built-in realistic NMEA Simulator for robust offline development and testing.
   - **Phase 2 (Immediate next step):** `aisstream.io` real-time WebSocket connection using free API key.
   - **Phase 3:** `vesselfinder.com` public-page scraper for vessel particulars and voyage data (MarineTraffic dropped — no free tier). Positions stay on aisstream.io; VesselFinder's public coordinates are whole-degree only.
5. **Deployment Target:** **Windows Desktop environment**.
6. **Vessel Particulars & Gross Tonnage:** Gross Tonnage stored as `GT` (volume measure), with optional Deadweight (`DWT` in tonnes) and physical dimensions (Length `LOA`, Beam/Width in meters).

---

*Last updated: 2026-10-12 by AI Agent*

