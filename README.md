# Real-Time Voice Agent with Pipecat

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Pipecat AI](https://img.shields.io/badge/Framework-Pipecat%201.8.x-orange.svg)](https://github.com/pipecat-ai/pipecat)
[![STT: Deepgram](https://img.shields.io/badge/STT-Deepgram%20Nova--2-purple.svg)](https://deepgram.com/)
[![TTS: Cartesia](https://img.shields.io/badge/TTS-Cartesia%20Sonic-yellow.svg)](https://cartesia.ai/)
[![LLM: OpenAI](https://img.shields.io/badge/LLM-GPT--4o--mini-brightgreen.svg)](https://openai.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An ultra-low latency, real-time conversational AI voice agent built with the [Pipecat AI](https://github.com/pipecat-ai/pipecat) framework and a dynamic 3D WebGL particle orb visualizer.

It integrates:
- **Audio Transport**: Direct Local Microphone & Speaker (`pyaudio`) or WebRTC (`Daily.co`)
- **Echo Suppression**: Built-in `LocalAcousticEchoSuppressor` preventing speaker-to-mic feedback loops
- **Voice Activity Detection**: Tuned Silero VAD for natural conversational pacing
- **Speech-to-Text (STT)**: Deepgram Nova-2 streaming transcription
- **LLM Engine**: OpenAI GPT-4o-mini with live dynamic tool execution
- **Text-to-Speech (TTS)**: Cartesia Sonic sub-second streaming audio synthesis

---

## Repository Structure

```
voice-agent-pipecat/
├── main.py              # Core Pipecat pipeline runner & multi-transport orchestrator
├── dns_resolver.py      # Resilient Google/Cloudflare UDP DNS fallback engine
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
├── .gitignore           # Git ignore rules
├── LICENSE              # MIT License
└── README.md            # Project documentation
```

---

## Architecture Flow

```
[ User Microphone ]
       │
       v (Audio Stream)
[ Audio Transport + Silero VAD (60ms start / 280ms stop) ]
       │
       v (Audio Chunks)
[ Deepgram STT Service ] (Nova-2 with 120ms endpointing & interim streaming)
       │
       v (User Transcripts)
[ Turn-Stop Strategy ] (SpeechTimeout 280ms -> Bypasses CPU ONNX model)
       │
       v (User Turn Frame)
[ OpenAI LLM Service ] (GPT-4o-mini with Live Tool Calling)
       │  ├── Tool Execution -> [ tools.py ] (Open-Meteo, zoneinfo, SQLite, Search)
       │
       v (Assistant Response Stream)
[ Cartesia TTS Service ] (Sonic Token-by-Token Streaming Synthesis)
       │
       v (Audio Stream)
[ Audio Output Transport ] ---> [ User Speakers / Headphones ]
       │
       v (WebSocket Events)
[ 3D WebGL Visualizer UI ] (http://localhost:8765)
```

---

## Built-in Genuine Live Tools (100% Real-Time)

| Tool Name | Backend Service | Example Query |
|---|---|---|
| `get_current_weather` | **Live Open-Meteo API** (Real-time temperature, condition, humidity, wind) | *"What's the weather like in Tokyo right now?"* |
| `get_current_time` | **Live Geocoding + IANA `zoneinfo`** (Accurate local clock & timezone) | *"What time is it in London or New York?"* |
| `book_appointment` | **Persistent SQLite Database (`appointments.db`)** | *"Book a consultation for tomorrow at 2 PM under Sarah."* |
| `get_booked_appointments` | **Persistent SQLite Database Query** | *"What appointments do I have booked under Sarah?"* |
| `search_knowledge` | **Live DuckDuckGo Knowledge API** | *"Tell me about Quantum Computing."* |

---

##  Getting Started

When running in `--mode server`, the following REST endpoints are available at `http://localhost:8765`:

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/status` | Get pipeline health, model configurations, and connected clients. |
| `GET` | `/api/appointments` | List all booked appointments from SQLite (`?customer_name=...`). |
| `POST` | `/api/appointments` | Book a new appointment via JSON payload. |
| `DELETE` | `/api/appointments/{id}` | Cancel/delete an appointment by ID or booking code. |
| `GET` | `/api/weather?location=Paris` | Proxy live Open-Meteo weather data. |
| `GET` | `/api/time?location=Tokyo` | Proxy live timezone clock calculations. |
| `POST` | `/api/search` | Execute real-time DuckDuckGo knowledge search. |
| `POST` | `/api/chat` | Send a text message to GPT-4o-mini with tool execution. |
| `GET/POST` | `/api/interrupt` | **Instant Barge-In**: Immediately halt assistant speech and reset to listening. |
| `GET/POST` | `/api/webrtc/session` | Create cloud Daily WebRTC room and connect agent. |
| `GET` | `/ws` | Real-time WebSocket event stream for visualizer or custom clients. |

---

## How Barge-In (Interruption) Works

1. **Voice Barge-In (Default)**:
   - When the assistant is speaking, simply start talking.
   - Deepgram transcribes your interim words and triggers `TranscriptionUserTurnStartStrategy`.
   - The pipeline broadcasts an `InterruptionFrame`, and Cartesia TTS halts audio output within **~40ms**.
2. **Visualizer Tap / Click**:
   - Tap or click anywhere on the 3D orb in your browser to immediately interrupt assistant speech.
3. **Keyboard Hotkey**:
   - Press **`Spacebar`** or **`Escape`** in the browser window to instantly stop the assistant.
4. **REST API**:
   - Send `POST http://localhost:8765/api/interrupt` to interrupt programmatically.

---

## Getting Started

### 1. Installation

```bash
git clone https://github.com/Kiransb12/Voice-AI-Agent.git
cd Voice-AI-Agent/voice-agent-pipecat

# Create and activate virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell
# source venv/bin/activate    # Linux / macOS

# Install dependencies
pip install -r requirements.txt
```

---

### 2. Configure Environment Variables

Create a `.env` file in `voice-agent-pipecat/`:

```env
OPENAI_API_KEY=sk-proj-xxxx...
OPENAI_MODEL=gpt-4o-mini

DEEPGRAM_API_KEY=xxxx...

CARTESIA_API_KEY=sk_car_xxxx...
CARTESIA_VOICE_ID=79a125e8-cd45-4c13-8a67-188112f4dd22
```

---

### 3. Running the Agent

#### Unified Server Mode (Local Voice + REST API + 3D Visualizer)
```bash
python main.py --mode server
```
Open `http://localhost:8765` in your browser.

#### Pure Local Mode (Hardware Mic & Speaker)
```bash
python main.py --mode local
```

#### Cloud WebRTC Mode (Daily.co)
```bash
python main.py --mode daily --url https://your-domain.daily.co/your-room-name
```

---

## CLI Options

| Argument | Description | Default |
|---|---|---|
| `--mode` | Transport mode: `server` (Local Voice + REST + Visualizer), `local`, or `daily` | `server` |
| `--headphones` | Enable full-duplex mic streaming when wearing headphones/headset | `False` (enables Speaker Echo Shield) |
| `--no-fillers` | Disable spoken tool fillers for immediate direct answers | `False` |
| `--allow-interruptions` | Mid-sentence speech interruptions enabled | `True` |
| `--no-interruptions` | Flag to disable barge-in interruptions | `False` |
| `--no-visualizer` | Disable the 3D WebGL web server | `False` |
| `--visualizer-port` | Port for the visualizer and REST APIs | `8765` |
| `-u`, `--url` | Daily WebRTC room URL (required for `daily` mode) | None |
| `-t`, `--token` | Daily meeting token (optional) | None |

---

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for more details.
