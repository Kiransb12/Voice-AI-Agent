"""Voice Agent entrypoint built with the Pipecat framework and 3D WebGL Visualizer.

Connects Audio transport (Local Mic/Speaker or Daily.co WebRTC), Silero VAD,
Deepgram STT, OpenAI LLM, and Cartesia TTS into an end-to-end voice pipeline with tool calling,
and streams live pipeline states, transcripts, and audio waveforms to the 3D visualizer frontend.
"""

import argparse
import asyncio
import os
import sys
import aiohttp
from dotenv import load_dotenv
from loguru import logger

from dns_resolver import setup_dns_fallback

# Initialize automatic DNS fallback to avoid ISP DNS timeouts on Cartesia and Deepgram
setup_dns_fallback()

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    AudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterruptionFrame,
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
from visualizer_bridge import VisualizerBridge, VisualizerPipelineProcessor

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
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
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


async def run_local_voice_agent(
    allow_interruptions: bool = False,
    bridge: VisualizerBridge | None = None,
):
    """Runs the voice agent locally using your PC's microphone, speaker, and 3D visualizer."""
    check_api_keys()

    from pipecat.transports.local.audio import (
        LocalAudioTransport,
        LocalAudioTransportParams,
    )

    logger.info("Starting local voice agent using your microphone and speakers...")

    async with aiohttp.ClientSession() as session:
        # 1. Local Transport (Microphone + Speaker + Ultra-snappy Silero VAD)
        vad_params = VADParams(
            confidence=0.65,
            start_secs=0.08,  # Instant start detection (80ms)
            stop_secs=0.40,   # Snappy turn stop (400ms instead of 700ms)
            min_volume=0.55,
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

        # Visualizer processor forwards frames to the 3D WebGL frontend
        viz_processor = VisualizerPipelineProcessor(bridge) if bridge else None

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

        # Configure barge-in strategies
        user_params = None
        if allow_interruptions:
            from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
            from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
                TranscriptionUserTurnStartStrategy,
            )
            from pipecat.turns.user_turn_strategies import UserTurnStrategies

            user_params = LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(
                    start=[TranscriptionUserTurnStartStrategy(use_interim=True)]
                )
            )

        context_aggregator = LLMContextAggregatorPair(
            context,
            user_params=user_params,
        )

        # 5. Build Pipeline with Echo Suppressor and 3D Visualizer Forwarder
        pipeline_processors = [
            transport.input(),
            echo_suppressor,
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]

        if viz_processor:
            pipeline_processors.append(viz_processor)

        pipeline = Pipeline(pipeline_processors)

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=allow_interruptions,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        # Link pipeline task to visualizer bridge for interactive/REST barge-in
        if bridge:
            bridge.set_pipeline_task(task)

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


async def run_daily_voice_agent(
    room_url: str,
    token: str | None = None,
    bridge: VisualizerBridge | None = None,
):
    """Initializes and runs the Daily WebRTC voice pipeline with 3D visualizer."""
    try:
        from pipecat.transports.services.daily import DailyParams, DailyTransport
    except ImportError:
        logger.warning(
            "daily-python WebRTC transport is only supported on Linux (or WSL2 on Windows), macOS, and Cloud Docker.\n"
            "On native Windows, use Local Mode: python main.py --mode local"
        )
        return

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

        viz_processor = VisualizerPipelineProcessor(bridge) if bridge else None

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
        pipeline_processors = [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]

        if viz_processor:
            pipeline_processors.append(viz_processor)

        pipeline = Pipeline(pipeline_processors)

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        if bridge:
            bridge.set_pipeline_task(task)

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


async def main_async(args):
    """Asynchronous main orchestrator running the visualizer bridge, REST APIs, and voice pipeline."""
    bridge = None
    if not args.no_visualizer or args.mode == "server":
        bridge = VisualizerBridge(port=args.visualizer_port)
        await bridge.start()
        print(f"\n{'='*70}")
        print(f" 🌐 SERVER & 3D VISUALIZER LIVE AT: http://localhost:{args.visualizer_port}")
        print(f" 📡 REST API: http://localhost:{args.visualizer_port}/api/status")
        print(f" 🚀 WEBRTC SESSION API: POST http://localhost:{args.visualizer_port}/api/webrtc/session")
        print(f"{'='*70}\n")

    try:
        if args.mode == "local" or args.mode == "server":
            # Runs the full voice pipeline (Mic/Speaker/VAD/STT/LLM/TTS) while serving all REST APIs & 3D Visualizer
            await run_local_voice_agent(
                allow_interruptions=args.allow_interruptions,
                bridge=bridge,
            )
        else:
            if not args.url:
                logger.error(
                    "Room URL is required for Daily mode! Pass via --url/-u or set DAILY_SAMPLE_ROOM_URL in .env"
                )
                sys.exit(1)
            await run_daily_voice_agent(
                room_url=args.url,
                token=args.token,
                bridge=bridge,
            )
    finally:
        if bridge:
            await bridge.stop()


def main():
    """CLI Argument parsing and runner."""
    parser = argparse.ArgumentParser(description="Pipecat Real-Time Voice Agent with 3D Visualizer")
    parser.add_argument(
        "--mode",
        choices=["local", "server", "daily"],
        default="local" if sys.platform == "win32" else "server",
        help="Transport mode: 'local' (mic/speaker), 'server' (Cloud REST & WebRTC host), or 'daily' (WebRTC room). Defaults to 'local' on Windows.",
    )
    parser.add_argument(
        "--allow-interruptions",
        action="store_true",
        default=False,
        help="Enable mid-sentence speech interruptions (recommended when using headphones). Default: False for local speakers.",
    )
    parser.add_argument(
        "--no-visualizer",
        action="store_true",
        default=False,
        help="Disable the 3D WebGL visualizer web server.",
    )
    parser.add_argument(
        "--visualizer-port",
        type=int,
        default=8765,
        help="Port for the 3D visualizer web server (default: 8765).",
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
        asyncio.run(main_async(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Voice agent stopped.")


if __name__ == "__main__":
    main()
