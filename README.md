# 🎙️ Real-Time Voice Agent with Pipecat

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Pipecat AI](https://img.shields.io/badge/Framework-Pipecat%201.8.x-orange.svg)](https://github.com/pipecat-ai/pipecat)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

An ultra-low latency, real-time conversational AI voice agent built with the [Pipecat AI](https://github.com/pipecat-ai/pipecat) framework.

It integrates:
- 🎙️ **Audio Transport**: Direct Local Microphone & Speaker (`pyaudio`) or WebRTC (`Daily.co`)
- 🛡️ **Echo Suppression**: Built-in `LocalAcousticEchoSuppressor` preventing speaker-to-mic feedback loops
- ⚡ **Voice Activity Detection**: Tuned Silero VAD for natural conversational pacing
- 🗣️ **Speech-to-Text (STT)**: Deepgram Nova-2 streaming transcription
- 🧠 **LLM Engine**: OpenAI GPT-4o-mini with live dynamic tool execution
- 🔊 **Text-to-Speech (TTS)**: Cartesia Sonic sub-second streaming audio synthesis

---

## 📁 Repository Structure

```
voice-agent-pipecat/
├── main.py              # Core Pipecat pipeline, transport modes, and runner
├── tools.py             # Live function schemas & handlers (Weather, Timezone, SQLite, Search)
├── prompts.py           # System persona & voice conversational style prompts
├── requirements.txt     # Python package dependencies
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
[ Deepgram STT Service ] (Nova-2 low-latency streaming transcription)
       │
       ▼ (User Text Transcripts)
[ OpenAI LLM Service ] (GPT-4o-mini + Live Tool Calling)
       │  ├── Tool Execution ➔ [ tools.py ] (Live Open-Meteo, zoneinfo, SQLite, Web Search)
       │
       ▼ (Assistant Response Stream)
[ Cartesia TTS Service ] (Sonic low-latency streaming voice synthesis)
       │
       ▼ (Audio Stream)
[ Audio Transport Output ]
       │
       ▼
[ User Speakers / Headphones ]
```

---

## 🛠️ Built-in Genuine Live Tools (100% Real-Time)

| Tool Name | Backend Service | Example Query |
|---|---|---|
| `get_current_weather` | **Live Open-Meteo API** (Global live temperature, condition, humidity, wind) | *"What's the weather like in Tokyo right now?"* |
| `get_current_time` | **Live Geocoding + IANA `zoneinfo`** (Accurate local clock & timezone) | *"What time is it in London or New York?"* |
| `book_appointment` | **Persistent SQLite Database (`appointments.db`)** | *"Book a consultation for tomorrow at 2 PM under Sarah."* |
| `get_booked_appointments` | **Persistent SQLite Database Query** | *"What appointments do I have booked under Sarah?"* |
| `search_knowledge` | **Live DuckDuckGo Knowledge API** | *"Tell me about SpaceX or Quantum Computing."* |

---

## 🚀 Getting Started

### 1. Prerequisites
- Python **3.10**, **3.11**, or **3.12**
- Microphone and Speaker (or Headphones)
- API Keys for:
  - [OpenAI](https://platform.openai.com) (LLM)
  - [Deepgram](https://deepgram.com) (STT)
  - [Cartesia](https://cartesia.ai) (TTS)

---

### 2. Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-username/voice-agent-pipecat.git
   cd voice-agent-pipecat
   ```

2. **Create and activate a virtual environment**:
   - **Windows (PowerShell)**:
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
   - **macOS / Linux**:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

### 3. Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```
*(On Windows PowerShell: `Copy-Item .env.example .env`)*

Open `.env` and fill in your API credentials:

```env
OPENAI_API_KEY=sk-proj-xxxx...
OPENAI_MODEL=gpt-4o-mini

DEEPGRAM_API_KEY=xxxx...

CARTESIA_API_KEY=sk_car_xxxx...
CARTESIA_VOICE_ID=79a125e8-cd45-4c13-8a67-188112f4dd22
```

---

### 4. Run the Voice Agent

#### Direct Local Microphone & Speakers (Default)
```bash
python main.py
```

#### With Live Interruption Support (Recommended with Headphones)
```bash
python main.py --allow-interruptions
```

#### Daily WebRTC Mode (Linux / WSL2 / Cloud)
```bash
python main.py --mode daily -u https://your-domain.daily.co/your-room-name
```

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for more details.
