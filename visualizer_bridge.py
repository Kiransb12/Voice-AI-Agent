"""Zero-overhead 3D Visualizer Bridge for Pipecat Voice Agent.

Streamlines WebSocket state synchronization without adding any processing
overhead to user microphone frames.
"""

import asyncio
import json
import math
import os
import struct
import time
from typing import Set
import aiohttp
from aiohttp import web
from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Path to 3D visualizer frontend directory (supports both local 'visualizer' folder and parent 'Voice-agent-3d')
LOCAL_STATIC = os.path.abspath(os.path.join(os.path.dirname(__file__), "visualizer"))
PARENT_STATIC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Voice-agent-3d"))
STATIC_DIR = LOCAL_STATIC if os.path.exists(LOCAL_STATIC) else PARENT_STATIC


class VisualizerBridge:
    """Zero-overhead WebSocket server for the 3D visualizer with barge-in support."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self.websockets: Set[web.WebSocketResponse] = set()
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.current_state = "idle"
        self.pipeline_task = None
        self.viz_processor = None
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._worker_task: asyncio.Task | None = None
        self._setup_routes()

    def set_pipeline_task(self, task):
        """Links active Pipecat pipeline task for triggering barge-in interruptions."""
        self.pipeline_task = task
        logger.info("Pipeline task linked to VisualizerBridge for barge-in interruptions.")

    def set_viz_processor(self, processor):
        """Links the active pipeline processor for instant upstream/downstream interruption broadcasting."""
        self.viz_processor = processor

    async def trigger_interruption(self):
        """Instantly halts bot speech and transitions the pipeline & visualizer to listening."""
        logger.info("⚡ [Barge-In] Triggering instant interruption on active pipeline!")
        if self.viz_processor:
            try:
                await self.viz_processor.broadcast_interruption()
            except Exception as e:
                logger.warning(f"Error broadcasting interruption from processor: {e}")
        elif self.pipeline_task:
            try:
                await self.pipeline_task.queue_frame(InterruptionFrame())
            except Exception as e:
                logger.warning(f"Failed to queue InterruptionFrame: {e}")
        self.broadcast_sync({"type": "state", "state": "listening"})

    def _setup_routes(self):
        from api_routes import register_api_routes

        # 1. Register REST API endpoints (/api/*)
        register_api_routes(self.app, bridge_instance=self)

        # 2. Register WebSocket Real-time Event Stream (/ws)
        self.app.router.add_get("/ws", self._websocket_handler)

        # 3. Serve 3D Visualizer Frontend
        if os.path.exists(STATIC_DIR):
            async def index_handler(request):
                index_path = os.path.join(STATIC_DIR, "index.html")
                return web.FileResponse(index_path)

            self.app.router.add_get("/", index_handler)
            self.app.router.add_static("/", STATIC_DIR)
            logger.info(f"Serving 3D Visualizer UI from: {STATIC_DIR}")
        else:
            logger.warning(f"Static visualizer folder not found at: {STATIC_DIR}")

    async def _websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=15.0)
        await ws.prepare(request)
        self.websockets.add(ws)
        logger.info(f"3D Visualizer connected from {request.remote}")

        await ws.send_str(
            json.dumps(
                {
                    "type": "state",
                    "state": self.current_state,
                }
            )
        )

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "interrupt":
                            await self.trigger_interruption()
                    except Exception:
                        pass
        finally:
            self.websockets.discard(ws)
            logger.info("3D Visualizer disconnected")

        return ws

    async def _queue_worker(self):
        """Drains send queue in background without touching the audio loop."""
        while True:
            try:
                payload = await self._send_queue.get()
                if self.websockets:
                    to_remove = set()
                    for ws in list(self.websockets):
                        if not ws.closed:
                            try:
                                await ws.send_str(payload)
                            except Exception:
                                to_remove.add(ws)
                        else:
                            to_remove.add(ws)
                    for ws in to_remove:
                        self.websockets.discard(ws)
                self._send_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def broadcast_sync(self, data: dict):
        """Non-blocking fire-and-forget broadcast."""
        if not self.websockets:
            return

        if "state" in data:
            self.current_state = data["state"]

        try:
            payload = json.dumps(data)
            if not self._send_queue.full():
                self._send_queue.put_nowait(payload)
        except Exception:
            pass

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        self._worker_task = asyncio.create_task(self._queue_worker())
        logger.info(
            f"✨ 3D Visualizer Server running at: http://{self.host}:{self.port}"
        )

    async def stop(self):
        if self._worker_task:
            self._worker_task.cancel()
        for ws in list(self.websockets):
            await ws.close()
        self.websockets.clear()
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()


class VisualizerPipelineProcessor(FrameProcessor):
    """Pure pass-through frame processor. Only intercepts state transitions."""

    def __init__(self, bridge: VisualizerBridge):
        super().__init__()
        self.bridge = bridge
        self.bridge.set_viz_processor(self)
        self._last_tts_time = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # 1. State changes only (infrequent, 0ms impact)
        if isinstance(frame, UserStartedSpeakingFrame):
            self.bridge.broadcast_sync({"type": "state", "state": "listening"})

        elif isinstance(frame, UserStoppedSpeakingFrame) or isinstance(frame, LLMFullResponseStartFrame):
            self.bridge.broadcast_sync({"type": "state", "state": "thinking"})

        elif isinstance(frame, TranscriptionFrame):
            self.bridge.broadcast_sync({"type": "state", "state": "thinking"})

        elif isinstance(frame, (BotStartedSpeakingFrame, TTSStartedFrame)):
            self.bridge.broadcast_sync({"type": "state", "state": "speaking"})

        elif isinstance(frame, (BotStoppedSpeakingFrame, TTSStoppedFrame)):
            self.bridge.broadcast_sync({"type": "state", "state": "idle"})

        # 2. Assistant TTS Audio Amplitude (ONLY processed during bot speech, throttled to 20Hz)
        elif isinstance(frame, TTSAudioRawFrame):
            now = time.monotonic()
            if (now - self._last_tts_time) >= 0.05:  # 20 FPS max
                self._last_tts_time = now
                try:
                    raw = frame.audio
                    if raw and len(raw) >= 2:
                        count = len(raw) // 2
                        shorts = struct.unpack(f"<{count}h", raw[: count * 2])
                        rms = math.sqrt(sum(s * s for s in shorts) / count)
                        norm_freq = min(max((rms / 500.0) * 15.0, 0.0), 40.0)
                        if norm_freq > 0.5:
                            self.bridge.broadcast_sync(
                                {"type": "audio_frequency", "frequency": round(norm_freq, 2)}
                            )
                except Exception:
                    pass

        await self.push_frame(frame, direction)
