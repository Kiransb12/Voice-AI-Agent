"""Voice Agent entrypoint built with the Pipecat framework.

Connects Audio transport (Local Mic/Speaker or Daily.co WebRTC), Silero VAD,
Deepgram STT, OpenAI LLM, and Cartesia TTS into an end-to-end voice pipeline with tool calling.
"""

import argparse
import asyncio
import os
import sys
import aiohttp
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    AudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService

from prompts import INITIAL_GREETING_INSTRUCTION, SYSTEM_PROMPT
from tools import TOOLS_SCHEMA

# Load environment variables from .env
load_dotenv()


class LocalAcousticEchoSuppressor(FrameProcessor):
    """Prevents local speaker playback from feeding back into the microphone.

    When running with PC speakers and a built-in microphone without headphones,
    this processor mutes microphone frames while the bot is speaking to avoid
    self-triggering loops and false interruptions.
    """

    def __init__(self, enabled: bool = True):
        super().__init__()
        self._enabled = enabled
        self._bot_speaking = False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        # Drop mic input frames while the bot is speaking if echo suppression is active
        if (
            self._enabled
            and self._bot_speaking
            and isinstance(frame, (AudioRawFrame, InputAudioRawFrame))
        ):
            return

        await self.push_frame(frame, direction)


def check_api_keys():
    """Verify that required API keys are configured."""
    required_keys = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "DEEPGRAM_API_KEY": os.getenv("DEEPGRAM_API_KEY"),
        "CARTESIA_API_KEY": os.getenv("CARTESIA_API_KEY"),
    }
    missing = [k for k, v in required_keys.items() if not v or "your_" in v]
    if missing:
        logger.warning(
            f"Missing or placeholder API keys detected: {', '.join(missing)}. "
            "Please configure your real credentials in .env"
        )


async def run_local_voice_agent(allow_interruptions: bool = False):
    """Runs the voice agent locally using your PC's microphone and speaker."""
    check_api_keys()

    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )

    logger.info("Starting local voice agent using your microphone and speakers...")

    async with aiohttp.ClientSession() as session:
        # 1. Local Transport (Microphone + Speaker + Tuned Silero VAD)
        vad_params = VADParams(
            confidence=0.7,
            start_secs=0.2,
            stop_secs=0.7,  # 700ms pause tolerance prevents cutting off user mid-thought
            min_volume=0.6,
        )
        vad_analyzer = SileroVADAnalyzer(params=vad_params)

        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_enabled=True,
                vad_analyzer=vad_analyzer,
            )
        )

        # Echo suppressor prevents speaker audio from looping back into STT
        echo_suppressor = LocalAcousticEchoSuppressor(enabled=True)

        # 2. Speech-to-Text (Deepgram Nova-2)
        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""),
        )

        # 3. Text-to-Speech (Cartesia Sonic low-latency voice)
        voice_id = os.getenv(
            "CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"
        )
        tts = CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY", ""),
            settings=CartesiaTTSService.Settings(voice=voice_id),
        )

        # 4. LLM & Context Management (OpenAI GPT-4o-mini with Live Tools)
        model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm = OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            settings=OpenAILLMService.Settings(model=model_name),
        )

        # Initialize conversation messages context
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        context = LLMContext(messages=messages, tools=TOOLS_SCHEMA)
        context_aggregator = LLMContextAggregatorPair(context)

        # 5. Build Pipeline with Echo Suppressor
        pipeline = Pipeline(
            [
                transport.input(),
                echo_suppressor,
                stt,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=allow_interruptions,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        # Trigger initial greeting
        await task.queue_frames(
            [
                LLMMessagesAppendFrame(
                    messages=[{"role": "system", "content": INITIAL_GREETING_INSTRUCTION}],
                    run_llm=True,
                )
            ]
        )

        # 6. Run Pipeline
        runner = PipelineRunner()
        logger.info("Local voice agent is active! Speak into your microphone...")
        await runner.run(task)


async def run_daily_voice_agent(room_url: str, token: str | None = None):
    """Initializes and runs the Daily WebRTC voice pipeline."""
    try:
        from pipecat.transports.services.daily import DailyParams, DailyTransport
    except ImportError:
        logger.error(
            "daily-python is not installed or not supported on this platform.\n"
            "Note: daily-python requires Linux (or WSL2 on Windows) / macOS.\n"
            "To test on Windows directly, run in local mode: python main.py --mode local"
        )
        sys.exit(1)

    check_api_keys()
    logger.info(f"Connecting to Daily WebRTC Room: {room_url}")

    async with aiohttp.ClientSession() as session:
        # 1. Transport setup (Daily WebRTC + Silero Voice Activity Detection)
        vad_params = VADParams(
            confidence=0.7,
            start_secs=0.2,
            stop_secs=0.7,
            min_volume=0.6,
        )
        transport = DailyTransport(
            room_url=room_url,
            token=token,
            bot_name="Pipecat Voice Assistant",
            params=DailyParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                camera_out_enabled=False,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(params=vad_params),
                transcription_enabled=False,
            ),
        )

        # 2. Speech-to-Text (Deepgram Nova-2)
        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""),
        )

        # 3. Text-to-Speech (Cartesia Sonic low-latency voice)
        voice_id = os.getenv(
            "CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"
        )
        tts = CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY", ""),
            settings=CartesiaTTSService.Settings(voice=voice_id),
        )

        # 4. LLM & Context Management (OpenAI GPT-4o-mini with Live Tools)
        model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm = OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            settings=OpenAILLMService.Settings(model=model_name),
        )

        # Initialize conversation messages context
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        context = LLMContext(messages=messages, tools=TOOLS_SCHEMA)
        context_aggregator = LLMContextAggregatorPair(context)

        # 5. Build Pipeline
        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                context_aggregator.user(),
                llm,
                tts,
                transport.output(),
                context_aggregator.assistant(),
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        # 6. Event Handlers
        @transport.event_handler("on_first_participant_joined")
        async def on_first_participant_joined(transport, participant):
            logger.info(f"First participant joined: {participant['id']}")
            await task.queue_frames(
                [
                    LLMMessagesAppendFrame(
                        messages=[{"role": "system", "content": INITIAL_GREETING_INSTRUCTION}],
                        run_llm=True,
                    )
                ]
            )

        @transport.event_handler("on_participant_left")
        async def on_participant_left(transport, participant, reason):
            logger.info(f"Participant left: {participant['id']}, reason: {reason}")
            await task.cancel()

        # 7. Run Pipeline
        runner = PipelineRunner()
        logger.info("Voice agent pipeline running. Waiting for participant...")
        await runner.run(task)


def main():
    """CLI Argument parsing and runner."""
    parser = argparse.ArgumentParser(description="Pipecat Real-Time Voice Agent")
    parser.add_argument(
        "--mode",
        choices=["local", "daily"],
        default="local" if sys.platform == "win32" else "daily",
        help="Transport mode: 'local' (mic/speaker) or 'daily' (WebRTC room). Defaults to 'local' on Windows.",
    )
    parser.add_argument(
        "--allow-interruptions",
        action="store_true",
        default=False,
        help="Enable mid-sentence speech interruptions (recommended when using headphones). Default: False for local speakers.",
    )
    parser.add_argument(
        "-u",
        "--url",
        type=str,
        default=os.getenv("DAILY_SAMPLE_ROOM_URL"),
        help="Daily WebRTC Room URL (e.g. https://your-domain.daily.co/room-name)",
    )
    parser.add_argument(
        "-t",
        "--token",
        type=str,
        default=os.getenv("DAILY_TOKEN"),
        help="Daily Meeting Token (optional)",
    )

    args = parser.parse_args()

    try:
        if args.mode == "local":
            asyncio.run(run_local_voice_agent(allow_interruptions=args.allow_interruptions))
        else:
            if not args.url:
                logger.error(
                    "Room URL is required for Daily mode! Pass via --url/-u or set DAILY_SAMPLE_ROOM_URL in .env"
                )
                sys.exit(1)
            asyncio.run(run_daily_voice_agent(room_url=args.url, token=args.token))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Voice agent stopped.")


if __name__ == "__main__":
    main()
