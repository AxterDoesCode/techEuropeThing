# AegisRoute: Continuous Multi-Agent Urban Safety World Model & Dynamic Routing
## Engineering Handoff & Ground-Up Build Specification (for Opencode / Automated Agents)

---

### 1. Executive Summary & Problem Statement

Standard navigation systems (Google Maps, Apple Maps, Waze) optimize almost exclusively for **travel time** and **distance**. Consequently, they frequently direct pedestrians and cyclists through unlit alleyways, high-crime corridors, and zones with active civil or infrastructure disturbances.

**AegisRoute** builds a **real-time, continuous World Safety Model** powered by **constant distributed background agents** running on **Modal** and validated through strict **Pydantic v2** models. These agents continuously harvest, correlate, and extrapolate urban risk signals (Police CAD dispatch, geo-located social alerts, local news feeds, municipal sensor telemetry, and nighttime satellite illumination) to produce a dynamic safety field. 

Commuters receive dynamic, turn-by-turn routing that penalizes hazardous zones while prioritizing high-illumination boulevards, CCTV coverage, and 24/7 verified safe havens.

---

### 2. Technology Stack & Hard Constraints

| Layer | Technology | Purpose / Role |
| :--- | :--- | :--- |
| **Agent Compute** | **Modal** (`modal>=0.63.0`) | Serverless cron workers, distributed microVMs, async map-reduce ingestion |
| **Data Validation** | **Pydantic v2** (`pydantic>=2.6.0`) | Strict runtime schemas, structured LLM outputs, mathematical decay methods |
| **Cognitive Filter** | **Gemini 2.5 Flash / OpenAI** | Extrapolates unstructured reports into coordinates, severity, radius, and half-life |
| **Spatial Graph** | **NetworkX / H3-py / Shapely** | Road network graph representation & cost-weighted pathfinding |
| **Backend API** | **FastAPI** (`fastapi>=0.110.0`) | ASGI web interface mounted on Modal (`@modal.asgi_app()`) |
| **Frontend Client** | **React 19, TypeScript, Vite, Tailwind CSS** | Interactive commuter navigation dashboard & agent fleet telemetry |
| **Map Rendering** | **Leaflet / Google Maps Platform** | Dual engine: high-contrast vector cartography + Google Maps JavaScript API |

---

### 3. System Architecture & Data Pipeline

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          RAW INGESTION FEEDS                             │
│  [Police CAD 911]  [Twitter/X Geo-Alerts]  [News Wires]  [Municipal IoT] │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                   MODAL INGESTION WORKERS (Python)                       │
│  @app.function(schedule=modal.Cron("*/2 * * * *"))                       │
│  Polls APIs, normalizes raw text, packages into RawFeedItem              │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│              COGNITIVE EXTRAPOLATION AGENT (Pydantic v2)                 │
│  Validates output against ExtrapolatedRiskEvent schema                   │
│  Extracts: Lat/Lng, Category, Severity [1-10], Radius, Decay Half-life   │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│               CONTINUOUS WORLD STATE CACHE (modal.Dict)                  │
│  Applies Exponential Temporal Decay: R(t) = R₀ · 0.5^(Δt / t_half)       │
│  Aggregates spatial risk fields & safe haven coordinates                 │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    AEGISROUTE PATHFINDING ENGINE                         │
│  Cost(edge) = Distance · [1 + α · RiskDensity] - β · SafeHavenProximity  │
│  Generates: "Guardian Safe Path" (96% safe) vs. "Fastest Direct" (38%)   │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│             NAVIGATION CLIENT & TURN-BY-TURN HUD (Frontend)              │
│  Real-time reroute notifications, progress simulation, and map overlays  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

### 4. Mathematical Modeling

#### 4.1. Dynamic Risk Decay Formula
Incidents are not static; a bar brawl subsides in 45 minutes, while broken streetlights persist for days. Every incident has an initial severity $S_0 \in [1.0, 10.0]$, confidence $C \in [0.1, 1.0]$, and a half-life $t_{half}$ (in minutes):

$$R(t) = (S_0 \times 10) \times C \times 0.5^{\left(\frac{\Delta t}{t_{half}}\right)}$$

When $R(t) < 15.0$, the incident is archived from the active routing field.

#### 4.2. Routing Cost Function
Given a graph of road edges $E$, the traversal cost of edge $e$ with length $L(e)$ is calculated as:

$$\text{Cost}(e) = L(e) \times \left(1 + \alpha \sum_{i \in \text{Hazards}} \text{RiskDensity}(e, i)\right) - \beta \sum_{h \in \text{Havens}} \text{ProximityBonus}(e, h)$$

- $\alpha$ = Commuter safety bias factor ($0.0 \le \alpha \le 2.0$, default $0.8$).
- $\text{RiskDensity}(e, i)$ = Spatial decay if edge $e$ intersects hazard $i$'s radius buffer $r_i$:
  $$\text{RiskDensity}(e, i) = \max\left(0, 1 - \frac{\text{dist}(e, \text{loc}_i)}{r_i}\right) \times R_i(t)$$
- $\beta$ = Haven attraction weight prioritizing passage near staffed police stations, 24/7 hospitals, or monitored transit concourses.

---

### 5. Pydantic v2 Data Schemas (`backend/schemas.py`)

```python
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

class IncidentCategory(str, Enum):
    VIOLENT_CRIME = "violent_crime"
    PROPERTY_CRIME = "property_crime"
    CIVIL_UNREST = "civil_unrest"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    TRANSIT_DISRUPTION = "transit_disruption"
    ENVIRONMENTAL_HAZARD = "environmental_hazard"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"

class FeedSourceType(str, Enum):
    POLICE_CAD = "police_cad"
    TWITTER_X_GEO = "twitter_x_geo"
    LOCAL_NEWS_RSS = "local_news_rss"
    SATELLITE_LIGHTING = "satellite_light"
    CITIZEN_REPORTS = "citizen_dispatch"

class GeoCoordinate(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)

class ExtrapolatedRiskEvent(BaseModel):
    id: str
    title: str = Field(..., max_length=120)
    category: IncidentCategory
    location: GeoCoordinate
    severity: float = Field(..., ge=1.0, le=10.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    radius_meters: float = Field(default=200.0, ge=20.0, le=2000.0)
    decay_half_life_minutes: float = Field(default=60.0, ge=5.0)
    sources: List[FeedSourceType] = Field(default_factory=list)
    source_citations: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def compute_current_risk(self, target_time: Optional[datetime] = None) -> float:
        now = target_time or datetime.utcnow()
        elapsed_mins = max(0.0, (now - self.timestamp).total_seconds() / 60.0)
        decay_factor = 0.5 ** (elapsed_mins / self.decay_half_life_minutes)
        return round(self.severity * 10.0 * self.confidence * decay_factor, 2)

class SafeHaven(BaseModel):
    id: str
    name: str
    location: GeoCoordinate
    haven_type: str
    open_24_7: bool = True

class RouteWaypoint(BaseModel):
    coordinate: GeoCoordinate
    cumulative_distance_m: float
    segment_risk_score: float

class NavigationRoute(BaseModel):
    route_id: str
    name: str
    total_distance_km: float
    estimated_time_mins: float
    safety_score: float = Field(..., ge=0.0, le=100.0)
    path: List[List[float]]  # [[lat, lng], ...]
    avoided_hazards: List[str] = Field(default_factory=list)
```

---

### 6. Modal Serverless Pipeline (`backend/modal_pipeline.py`)

```python
import modal
from datetime import datetime
from typing import List, Dict, Any

image = modal.Image.debian_slim().pip_install(
    "pydantic>=2.6.0",
    "fastapi[standard]>=0.110.0",
    "httpx>=0.27.0",
    "google-genai>=0.1.1",
    "networkx>=3.2.1"
)

app = modal.App("aegisroute-world-model", image=image)
world_state_dict = modal.Dict.from_name("aegisroute-world-state", create_if_missing=True)

# 1. Scheduled Background Ingestion Cron
@app.function(schedule=modal.Cron("*/2 * * * *"), secrets=[modal.Secret.from_name("gemini-secret", required=False)])
def continuous_world_ingestion_cron():
    raw_signals = [
        {"id": "raw_1", "raw_text": "Armed robbery at 6th & Stevenson", "lat": 37.7818, "lng": -122.4086},
        {"id": "raw_2", "raw_text": "BART entrance crowd surge with broken bottles", "lat": 37.7792, "lng": -122.4138}
    ]
    events = list(extrapolate_signal_worker.map(raw_signals))
    world_state_dict["latest_incidents"] = [e for e in events if e]
    world_state_dict["last_sync"] = datetime.utcnow().isoformat()

# 2. Cognitive Filter Extrapolator Worker
@app.function(timeout=60)
def extrapolate_signal_worker(signal: Dict[str, Any]) -> Dict[str, Any]:
    text = signal.get("raw_text", "").lower()
    severity = 8.5 if "armed" in text or "robbery" in text else 6.0
    return {
        "id": f"risk_{signal['id']}",
        "title": signal["raw_text"],
        "category": "violent_crime" if "robbery" in text else "suspicious_activity",
        "location": {"latitude": signal["lat"], "longitude": signal["lng"]},
        "severity": severity,
        "confidence": 0.92,
        "radius_meters": 250.0,
        "decay_half_life_minutes": 60.0,
        "timestamp": datetime.utcnow().isoformat()
    }

# 3. Serverless ASGI API Endpoint
@app.function()
@modal.asgi_app()
def fastapi_app():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    web = FastAPI(title="AegisRoute World Model API")
    web.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @web.get("/api/world-model")
    def get_world():
        return {
            "version": "2.4.0",
            "last_sync": world_state_dict.get("last_sync", datetime.utcnow().isoformat()),
            "incidents": world_state_dict.get("latest_incidents", []),
            "global_safety_index": 7.6
        }

    return web
```

---

### 7. Step-by-Step Build & Implementation Order for Opencode

Follow this exact sequential implementation checklist:

#### Step 1: Environment & Dependency Setup
1. Clone / initialize the project directory.
2. Initialize Node / React frontend:
   ```bash
   npm install react react-dom express dotenv motion lucide-react leaflet @types/leaflet
   npm install -D @tailwindcss/vite tailwindcss tsx esbuild typescript @types/node @types/express
   ```
3. Initialize Python environment for Modal:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install modal pydantic fastapi "httpx>=0.27" networkx
   ```

#### Step 2: Backend Modal & Pydantic Files
1. Create `backend/schemas.py` containing the Pydantic v2 models defined in Section 5.
2. Create `backend/modal_pipeline.py` containing the Modal App definition, cron schedules, map-reduce worker functions, and FastAPI mount defined in Section 6.
3. Test Modal locally:
   ```bash
   modal setup
   modal run backend/modal_pipeline.py
   ```

#### Step 3: Full-Stack Express + Vite Integration Server (`server.ts`)
1. Create `server.ts` to expose endpoints:
   - `GET /api/world-model`: Returns currently active extrapolated risk nodes with calculated dynamic risk scores and safe havens.
   - `POST /api/extrapolate-incident`: Accepts raw unstructured text, calls Gemini/LLM or heuristic engine, validates against Pydantic schema, and prepends to the active world state.
   - `POST /api/reset-world-model`: Reinitializes world state to baseline.
   - `GET /api/python-sources`: Serves the raw Python code files for inspection.
2. Mount Vite middleware for dev mode and static file serving for production.

#### Step 4: Routing & Mathematical Engine (`src/utils/routingEngine.ts`)
1. Define commuter scenarios (origin/destination coordinates).
2. Implement route evaluation calculating:
   - **Direct Route**: shortest physical distance regardless of danger nodes.
   - **Guardian Safe Route**: geometric detour around hazard radii, favoring safe havens and high street illumination.
   - Metric computations: Composite Safety Score (0-100), CCTV coverage percentage, and street lighting percentage.

#### Step 5: Map & Navigation UI (`src/components/`)
1. `SafetyMap.tsx`: Interactive Leaflet map rendering CartoDB Dark/Light tiles, animated pulsating danger spheres, emerald safe havens, and cyan/red route lines.
2. `NavigationHUD.tsx`: Turn-by-turn instruction feed, route switcher (Guardian 96% vs Direct 38%), and interactive commute simulation animation controls.
3. `AgentFleetPanel.tsx`: Live display of Modal background worker statuses, incident feed ticker, and live event injection simulator.
4. `ModalCodeModal.tsx`: In-app code viewer allowing one-click copy of `schemas.py` and `modal_pipeline.py`.
5. `GoogleMapsKeyModal.tsx`: Modal allowing optional entry of Google Maps Platform API key.

#### Step 6: Verification & Execution Commands
1. Run lint check: `npm run lint` (`tsc --noEmit`).
2. Run build verification: `npm run build`.
3. Start dev server: `npm run dev` (running on `0.0.0.0:3000`).
4. Deploy Modal backend: `modal deploy backend/modal_pipeline.py`.

---

### 8. API Specification Reference

#### `GET /api/world-model`
- **Response**:
  ```json
  {
    "status": "ok",
    "world_model_version": "2.4.0",
    "last_sync": "2026-09-19T04:40:00Z",
    "orchestrator": {
      "framework": "Modal Serverless",
      "schema": "Pydantic v2",
      "active_workers": [
        { "name": "Police CAD Streamer", "status": "polling", "interval": "30s" }
      ]
    },
    "incidents": [
      {
        "id": "risk_cad_101",
        "title": "Aggressive Robbery & Alley Dispute",
        "category": "violent_crime",
        "location": { "latitude": 37.7818, "longitude": -122.4086 },
        "severity": 8.7,
        "confidence": 0.94,
        "radius_meters": 220,
        "decay_half_life_minutes": 50,
        "dynamic_risk_score": 78.4
      }
    ],
    "safe_havens": [
      { "id": "haven_1", "name": "SF Central Police Substation", "lat": 37.7865, "lng": -122.4055 }
    ]
  }
  ```

#### `POST /api/extrapolate-incident`
- **Request Body**:
  ```json
  {
    "raw_text": "Code 3: Aggravated street robbery at 6th & Stevenson",
    "source_type": "police_cad",
    "user_lat": 37.7818,
    "user_lng": -122.4086
  }
  ```
- **Response**:
  ```json
  {
    "status": "success",
    "incident": {
      "id": "risk_extrapolated_1710000000",
      "title": "Aggravated street robbery",
      "category": "violent_crime",
      "severity": 9.2,
      "radius_meters": 300,
      "decay_half_life_minutes": 60
    }
  }
  ```
