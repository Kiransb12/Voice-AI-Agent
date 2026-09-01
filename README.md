# 🎙️ Real-Time Voice Agent with Pipecat & 3D WebGL Visualizer

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Pipecat AI](https://img.shields.io/badge/Framework-Pipecat%201.8.x-orange.svg)](https://github.com/pipecat-ai/pipecat)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An ultra-low latency, real-time conversational AI voice agent built with the [Pipecat AI](https://github.com/pipecat-ai/pipecat) framework and a 3D WebGL particle orb visualizer.

---

## ✨ Features

- 🎙️ **Direct Local Audio Transport**: Hardware mic and speaker via `pyaudio` with zero network overhead.
- 🛡️ **Acoustic Echo Shield**: `LocalAcousticEchoSuppressor` frame processor prevents speaker feedback loops and self-interruptions.
- ⚡ **Ultra-Fast VAD Turn Pacing**: Tuned Silero VAD (80ms start detection, 400ms turn stop) for instant responsiveness.
- 🔮 **3D WebGL Orb Visualizer**: Native Three.js shader-based particle orb that dynamically morphs and deforms in real-time with 0% CPU footprint.
- 🌐 **Full REST API Suite**: Exposes endpoints for Appointments, Weather, Timezone, Knowledge Search, and Text Chat.
- 📡 **Real-Time WebSocket Event Stream**: Bi-directional event stream broadcasting agent states (`listening`, `thinking`, `speaking`, `idle`), transcripts, and audio waveforms.

---

## 📁 Repository Structure

```
voice-agent-pipecat/
├── main.py              # Core Pipecat pipeline runner & multi-transport orchestrator
├── visualizer_bridge.py # Zero-overhead HTTP & WebSocket server for 3D visualizer
├── api_routes.py        # REST API endpoints (/api/status, /api/appointments, etc.)
├── tools.py             # Live function schemas & handlers (Weather, Timezone, SQLite, Search)
├── prompts.py           # System persona & voice conversational style prompts
├── visualizer/          # Self-contained 3D WebGL visualizer frontend (HTML, CSS, JS)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── requirements.txt     # Python dependencies
├── .env.example         # Environment configuration template
├── .gitignore           # Git ignore rules (protects API keys & database)
├── LICENSE              # MIT License
└── README.md            # Project documentation
```

---

## ⚡ Architecture Flow

```
[ User Microphone ]
       │
       ▼ (Audio Stream)
[ Audio Transport + LocalAcousticEchoSuppressor + Silero VAD ]
       │
       ▼ (Audio Chunks)
[ Deepgram STT Service ] (Nova-2 streaming transcription)
       │
       ▼ (User Text Transcripts)
[ OpenAI LLM Service ] (GPT-4o-mini + Live Tool Calling)
       │  ├── Tool Execution ➔ [ tools.py ] (Live Open-Meteo, zoneinfo, SQLite, Web Search)
       │
       ▼ (Assistant Response Stream)
[ Cartesia TTS Service ] (Sonic streaming voice synthesis)
       │
       ▼ (Audio Stream)
[ Audio Transport Output ] ───► [ User Speakers / Headphones ]
       │
       ▼ (WebSocket Events)
[ 3D WebGL Visualizer UI ] (http://localhost:8765)
```

---

## 🛠️ Built-in Genuine Live Tools (100% Real-Time)

| Tool Name | Backend Service | Example Query |
|---|---|---|
| `get_current_weather` | **Live Open-Meteo API** (Global live temperature, condition, humidity, wind) | *"What's the weather like in Tokyo right now?"* |
| `get_current_time` | **Live Geocoding + IANA `zoneinfo`** (Accurate local clock & timezone) | *"What time is it in London or New York?"* |
| `book_appointment` | **Persistent SQLite Database (`appointments.db`)** | *"Book a consultation for tomorrow at 2 PM under Sarah."* |
| `get_booked_appointments` | **Persistent SQLite Database Query** | *"What appointments do I have booked under Sarah?"* |
| `search_knowledge` | **Live DuckDuckGo Knowledge API** | *"Tell me about Quantum Computing."* |

---

## 🌐 Exposed REST API Endpoints

When the agent runs, the following REST endpoints are available at `http://localhost:8765`:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/status` | Get pipeline health, active model configurations, and connected clients. |
| `GET` | `/api/appointments` | List all booked appointments from SQLite (`?customer_name=...`). |
| `POST` | `/api/appointments` | Book a new appointment via JSON payload. |
| `DELETE` | `/api/appointments/{id}` | Cancel/delete an appointment by ID or booking code. |
| `GET` | `/api/weather?location=Paris` | Proxy live Open-Meteo weather data. |
| `GET` | `/api/time?location=Tokyo` | Proxy live timezone clock calculations. |
| `POST` | `/api/search` | Execute real-time DuckDuckGo knowledge search. |
| `POST` | `/api/chat` | Send a text message to GPT-4o-mini with tool execution. |
| `GET` | `/ws` | Real-time WebSocket event stream for visualizer or custom clients. |

---

## 🚀 Getting Started

### 1. Installation

```bash
git clone https://github.com/Kiransb12/Voice-AI-Agent.git
cd Voice-AI-Agent/voice-agent-pipecat

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1   # On Windows
# source venv/bin/activate    # On Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

---

### 2. Configure Environment Variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Open `.env` and fill in your API credentials:

```env
OPENAI_API_KEY=sk-proj-xxxx...
OPENAI_MODEL=gpt-4o-mini

DEEPGRAM_API_KEY=xxxx...

CARTESIA_API_KEY=sk_car_xxxx...
CARTESIA_VOICE_ID=79a125e8-cd45-4c13-8a67-188112f4dd22
```

---

### 3. Run the Voice Agent & Visualizer

```bash
python main.py
```

Then open your browser to:
```
http://localhost:8765
```

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for more details.
