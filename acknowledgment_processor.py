"""Real-time conversational acknowledgment and backchannel filler processor.

Intercepts tool execution (FunctionCallInProgressFrame) and immediately emits
a natural spoken acknowledgment (TextFrame) to Cartesia TTS. This masks tool
execution latency and drops perceived conversational delay to near-zero (~180ms).
"""

import random
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    InterruptionFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# Contextual fillers for each live tool (ultra-snappy 1-2 words to prevent blocking speech)
DEFAULT_TOOL_FILLERS: dict[str, list[str]] = {
    "get_current_weather": [
        "Checking...",
        "Checking the weather...",
    ],
    "get_current_time": [
        "Checking the time...",
        "One moment...",
    ],
    "book_appointment": [
        "Scheduling...",
        "Booking that...",
    ],
    "get_booked_appointments": [
        "Checking schedule...",
        "Looking up appointments...",
    ],
    "search_knowledge": [
        "Searching...",
        "Looking that up...",
    ],
}


class ToolAcknowledgmentProcessor(FrameProcessor):
    """Intercepts FunctionCallInProgressFrame and pushes an immediate spoken acknowledgment to TTS."""

    def __init__(self, fillers: dict[str, list[str]] | None = None, enabled: bool = True):
        super().__init__()
        self._fillers = fillers or DEFAULT_TOOL_FILLERS
        self._enabled = enabled
        self._last_invoked_call_id: str | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._enabled:
            return

        # Reset on interruption so stale fillers are never spoken
        if isinstance(frame, InterruptionFrame):
            self._last_invoked_call_id = None

        elif isinstance(frame, FunctionCallInProgressFrame):
            fn_name = getattr(frame, "function_name", None)
            tool_call_id = getattr(frame, "tool_call_id", None)

            # Prevent duplicate acknowledgments for the same tool call id
            if tool_call_id and tool_call_id != self._last_invoked_call_id:
                self._last_invoked_call_id = tool_call_id
                options = self._fillers.get(fn_name)
                if options:
                    ack_text = random.choice(options)
                    logger.info(
                        f"[Acknowledgment] Speaking instant tool filler for '{fn_name}': '{ack_text}'"
                    )
                    # Push TextFrame directly downstream to TTS so speech starts immediately
                    await self.push_frame(TextFrame(text=ack_text), direction)

        await self.push_frame(frame, direction)
