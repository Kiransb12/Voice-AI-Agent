"""REST API and WebSocket Audio Streaming endpoints for Pipecat Voice Agent.

Exposes:
1. REST API endpoints for appointments, weather, timezone, knowledge search, and text chat.
2. Web client WebSocket audio streaming endpoint (/api/voice-stream).
"""

import json
import os
import sqlite3
import time
import aiohttp
from aiohttp import web
from loguru import logger
import openai

from prompts import SYSTEM_PROMPT
from tools import (
    DB_PATH,
    execute_book_appointment,
    execute_get_booked_appointments,
    execute_get_current_time,
    execute_get_current_weather,
    execute_search_knowledge,
)


def register_api_routes(app: web.Application, bridge_instance=None):
    """Registers all REST API and audio endpoints to the aiohttp web application."""

    # =========================================================================
    # 1. System Status Endpoint
    # =========================================================================
    async def handle_status(request: web.Request):
        return web.json_response(
            {
                "status": "online",
                "framework": "Pipecat AI 1.8.x",
                "models": {
                    "llm": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    "stt": "Deepgram Nova-2",
                    "tts": "Cartesia Sonic",
                },
                "active_visualizer_clients": len(bridge_instance.websockets) if bridge_instance else 0,
                "endpoints": {
                    "status": "GET /api/status",
                    "appointments": "GET, POST /api/appointments",
                    "weather": "GET /api/weather?location={loc}",
                    "time": "GET /api/time?location={loc}",
                    "knowledge_search": "POST /api/search",
                    "chat": "POST /api/chat",
                    "event_websocket": "GET /ws",
                },
            }
        )

    # =========================================================================
    # 2. Appointments REST Endpoints (SQLite)
    # =========================================================================
    async def handle_get_appointments(request: web.Request):
        customer = request.query.get("customer_name")
        code = request.query.get("booking_code")
        result = execute_get_booked_appointments(customer_name=customer, booking_code=code)
        return web.json_response(result)

    async def handle_create_appointment(request: web.Request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        required = ["customer_name", "service_name", "date", "time"]
        missing = [f for f in required if f not in data]
        if missing:
            return web.json_response(
                {"error": f"Missing required fields: {', '.join(missing)}"},
                status=400,
            )

        result = execute_book_appointment(
            customer_name=data["customer_name"],
            service_name=data["service_name"],
            date=data["date"],
            time_slot=data["time"],
            notes=data.get("notes"),
        )
        return web.json_response(result)

    async def handle_delete_appointment(request: web.Request):
        identifier = request.match_info.get("id_or_code", "")
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM appointments WHERE id = ? OR booking_code = ?",
                (identifier, identifier.upper()),
            )
            conn.commit()
            deleted = cursor.rowcount > 0

        if deleted:
            return web.json_response(
                {"status": "success", "message": f"Appointment '{identifier}' cancelled."}
            )
        return web.json_response(
            {"status": "error", "message": f"Appointment '{identifier}' not found."},
            status=404,
        )

    # =========================================================================
    # 3. Weather & Time Live Tool REST Endpoints
    # =========================================================================
    async def handle_weather(request: web.Request):
        location = request.query.get("location")
        if not location:
            return web.json_response(
                {"error": "Query param 'location' is required (e.g. ?location=Tokyo)"},
                status=400,
            )
        unit = request.query.get("unit", "celsius")
        result = await execute_get_current_weather(location=location, unit=unit)
        return web.json_response(result)

    async def handle_time(request: web.Request):
        location = request.query.get("location")
        if not location:
            return web.json_response(
                {"error": "Query param 'location' is required (e.g. ?location=London)"},
                status=400,
            )
        result = await execute_get_current_time(location=location)
        return web.json_response(result)

    async def handle_search(request: web.Request):
        try:
            data = await request.json()
            query = data.get("query")
        except Exception:
            query = request.query.get("q")

        if not query:
            return web.json_response({"error": "Field 'query' is required"}, status=400)

        result = await execute_search_knowledge(query=query)
        return web.json_response(result)

    # =========================================================================
    # 4. Text-Based Conversational Chat API
    # =========================================================================
    async def handle_chat(request: web.Request):
        user_message = None
        history = []
        try:
            data = await request.json()
            user_message = data.get("message")
            history = data.get("history", [])
        except Exception:
            user_message = request.query.get("message")

        if not user_message:
            return web.json_response(
                {"error": "Field 'message' is required (e.g. ?message=Hello or JSON body)"},
                status=400,
            )

        history = data.get("history", [])
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        if not api_key:
            return web.json_response({"error": "OPENAI_API_KEY is not configured"}, status=500)

        client = openai.AsyncOpenAI(api_key=api_key)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                max_tokens=250,
            )
            reply = response.choices[0].message.content
            return web.json_response(
                {
                    "status": "success",
                    "reply": reply,
                    "model": model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                    },
                }
            )
        except Exception as e:
            logger.error(f"Chat API error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    # =========================================================================
    # 5. WebRTC Cloud Session Management (Daily.co)
    # =========================================================================
    async def handle_webrtc_session(request: web.Request):
        """Unified endpoint: Creates a Daily WebRTC room, spawns the Pipecat bot into it, and returns the room URL."""
        daily_api_key = os.getenv("DAILY_API_KEY", "")
        sample_room = os.getenv("DAILY_SAMPLE_ROOM_URL", "")

        room_url = sample_room
        token = None

        # 1. Create a dynamic room via Daily REST API if DAILY_API_KEY is available
        if daily_api_key and "your_" not in daily_api_key:
            try:
                headers = {"Authorization": f"Bearer {daily_api_key}", "Content-Type": "application/json"}
                async with aiohttp.ClientSession() as session:
                    # Create room with 1-hour expiry
                    async with session.post(
                        "https://api.daily.co/v1/rooms",
                        headers=headers,
                        json={"properties": {"exp": int(time.time()) + 3600, "enable_chat": True}},
                    ) as resp:
                        if resp.status == 200:
                            room_data = await resp.json()
                            room_url = room_data.get("url")
                            logger.info(f"Created new Daily WebRTC room: {room_url}")

                    # Generate meeting token for the bot
                    if room_url:
                        async with session.post(
                            "https://api.daily.co/v1/meeting-tokens",
                            headers=headers,
                            json={"properties": {"room_name": room_url.split("/")[-1], "is_owner": True}},
                        ) as token_resp:
                            if token_resp.status == 200:
                                token_data = await token_resp.json()
                                token = token_data.get("token")
            except Exception as e:
                logger.warning(f"Could not provision dynamic Daily room: {e}")

        if not room_url:
            return web.json_response(
                {
                    "error": "No WebRTC room available. Set DAILY_API_KEY or DAILY_SAMPLE_ROOM_URL in .env"
                },
                status=500,
            )

        # 2. Spawn the Daily Voice Agent in the background (supported on Linux/WSL2/macOS/Cloud Docker)
        import sys
        is_windows_native = sys.platform == "win32"

        if not is_windows_native:
            try:
                from main import run_daily_voice_agent
                import asyncio
                asyncio.create_task(run_daily_voice_agent(room_url=room_url, token=token, bridge=bridge_instance))
                logger.info(f"Spawned Daily Voice Agent into room: {room_url}")
            except Exception as e:
                logger.error(f"Failed to spawn Daily Voice Agent: {e}")

        return web.json_response(
            {
                "status": "ready",
                "room_url": room_url,
                "token": token,
                "platform": sys.platform,
                "cloud_ready": True,
                "note": "On cloud deployment (Linux / Docker / WSL2), Daily WebRTC bot joins the room automatically. On Windows native, test local hardware mode: python main.py --mode local",
            }
        )

    # Attach all routes to app (supports both GET and POST for testing)
    app.router.add_get("/api/status", handle_status)
    app.router.add_get("/api/appointments", handle_get_appointments)
    app.router.add_post("/api/appointments", handle_create_appointment)
    app.router.add_delete("/api/appointments/{id_or_code}", handle_delete_appointment)
    app.router.add_get("/api/weather", handle_weather)
    app.router.add_get("/api/time", handle_time)
    app.router.add_get("/api/search", handle_search)
    app.router.add_post("/api/search", handle_search)
    app.router.add_get("/api/chat", handle_chat)
    app.router.add_post("/api/chat", handle_chat)
    app.router.add_get("/api/webrtc/session", handle_webrtc_session)
    app.router.add_post("/api/webrtc/session", handle_webrtc_session)

    logger.info("Registered REST & WebRTC API routes at /api/*")
