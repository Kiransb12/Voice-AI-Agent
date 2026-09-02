"""Voice Agent entrypoint built with the Pipecat framework and 3D WebGL Visualizer.

Connects Audio transport (Local Mic/Speaker or Daily.co WebRTC), Silero VAD,
Deepgram STT, OpenAI LLM, and Cartesia TTS into an end-to-end voice pipeline with tool calling,
and streams live pipeline states, transcripts, and audio waveforms to the 3D visualizer frontend.
"""

import argparse
import asyncio
import os
import sys
import time
import aiohttp
from dotenv import load_dotenv
from loguru import logger

from dns_resolver import setup_dns_fallback

# Initialize automatic DNS fallback to avoid ISP DNS timeouts on Cartesia and Deepgram
setup_dns_fallback()

import re
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    AudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
    TTSTextFrame,
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
from pipecat.services.tts_service import TextAggregationMode

from acknowledgment_processor import ToolAcknowledgmentProcessor
from prompts import INITIAL_GREETING_INSTRUCTION, SYSTEM_PROMPT
from tools import TOOLS_SCHEMA
from visualizer_bridge import VisualizerBridge, VisualizerPipelineProcessor

# Load environment variables from .env
load_dotenv()


class BotSpeechTracker:
    """Tracks active bot speech dynamically with ZERO hardcoded words, keywords, or language dictionaries.
    Allows full-duplex conversational barge-in identical to headphones by simply verifying whether
    the transcribed audio is an acoustic echo of the bot's own currently spoken sentence.
    """

    def __init__(self):
        self._current_text = ""
        self._recent_text = ""
        self.is_speaking = False

    def record_bot_text(self, text: str):
        if text:
            self._current_text += " " + text
            self._recent_text = self._current_text

    def bot_started_speaking(self):
        self.is_speaking = True

    def bot_stopped_speaking(self):
        self.is_speaking = False
        self._current_text = ""

    def clear_recent(self):
        """Clears the recent buffer when genuine user speech is detected."""
        self._recent_text = ""

    def is_echo(self, transcript: str) -> bool:
        """Determines if the transcript is a speaker acoustic echo of the bot's own voice.
        Zero hardcoded words or lists: pure dynamic string and word-sequence matching.
        """
        candidate = self._current_text if self.is_speaking else self._recent_text
        if not candidate or not transcript:
            return False

        t_words = re.findall(r"\b\w+\b", transcript.lower())
        b_words = re.findall(r"\b\w+\b", candidate.lower())
        if not t_words or not b_words:
            return False

        b_set = set(b_words)

        # Single-word check:
        # If that single word is part of the bot's current speech (e.g. 'time' while bot says 'Checking the time...'),
        # it is an echo of the speaker! If it is NOT in the bot's speech (e.g. 'Stop', 'Wait', 'No'), it is a genuine user barge-in!
        if len(t_words) == 1:
            return t_words[0] in b_set

        # Check if the transcribed phrase is a sequence of words in the bot's active speech
        t_phrase = " ".join(t_words)
        b_text = " ".join(b_words)
        if f" {t_phrase} " in f" {b_text} " or t_phrase == b_text:
            return True

        # Check word overlap against active bot speech
        overlap = sum(1 for w in t_words if w in b_set)
        return (overlap / len(t_words)) >= 0.70


class LexicalEchoFilter(FrameProcessor):
    """Filters out Deepgram transcriptions that match the bot's own voice (laptop speaker bleed),
    allowing genuine human barge-in speech to pass through instantly without false interruptions."""

    def __init__(self, tracker: BotSpeechTracker, enabled: bool = True):
        super().__init__()
        self.tracker = tracker
        self.enabled = enabled

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self.tracker.bot_started_speaking()
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            self.tracker.bot_stopped_speaking()

        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            text = getattr(frame, "text", "").strip()
            words = re.findall(r"\b\w+\b", text.lower())

            # If the bot is speaking, empty interim frames or punctuation must be dropped so they don't trigger false interruptions
            if self.tracker.is_speaking and not words:
                return

            if self.enabled and words:
                if self.tracker.is_echo(text):
                    logger.debug(f"[Lexical Echo Shield] Suppressed speaker self-echo: '{text}'")
                    return  # Drop speaker echo frame! Do not trigger interruption or append to context!
                else:
                    # Genuine user speech detected! Clear recent bot speech buffer
                    self.tracker.clear_recent()

        await self.push_frame(frame, direction)


class BotSpeechRecorder(FrameProcessor):
    """Passively records spoken assistant text from the pipeline so the echo filter knows what words the bot is saying."""

    def __init__(self, tracker: BotSpeechTracker):
        super().__init__()
        self.tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (TextFrame, TTSTextFrame)):
            text = getattr(frame, "text", "")
            if text:
                self.tracker.record_bot_text(text)
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            self.tracker.bot_stopped_speaking()

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
    allow_interruptions: bool = True,
    headphones: bool = False,
    enable_fillers: bool = True,
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
            start_secs=0.06,  # Instant start detection (60ms)
            stop_secs=0.28,   # Snappy turn stop (280ms instead of 400ms)
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

        # Echo Tracker & Lexical Shield:
        # Prevents laptop speaker bleed from interrupting the bot while allowing real human voice barge-in!
        bot_tracker = BotSpeechTracker()
        echo_filter = LexicalEchoFilter(bot_tracker, enabled=not headphones)
        bot_recorder = BotSpeechRecorder(bot_tracker)

        if not headphones:
            logger.info(
                "[Audio Mode] Lexical Echo Shield ACTIVE: Laptop speaker echo is filtered automatically. Voice barge-in is enabled!"
            )
        else:
            logger.info(
                "[Audio Mode] Headphone Mode ACTIVE: Mic 100% open for full-duplex voice barge-in."
            )

        # Visualizer processor forwards frames to the 3D WebGL frontend
        viz_processor = VisualizerPipelineProcessor(bridge) if bridge else None

        # 2. Speech-to-Text (Deepgram Nova-2 with Fast 120ms Endpointing)
        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            settings=DeepgramSTTService.Settings(
                model="nova-2-general",
                language="en",
                endpointing=120,       # 120ms endpointing (shaves ~180ms off STT finalization)
                interim_results=True,  # Real-time interim tokens for instant barge-in
                smart_format=True,
            ),
        )

        # 3. Text-to-Speech (Cartesia Sonic with Token Streaming Mode)
        voice_id = os.getenv(
            "CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"
        )
        tts = CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY", ""),
            settings=CartesiaTTSService.Settings(voice=voice_id),
            text_aggregation_mode=TextAggregationMode.TOKEN,  # Synthesize token-by-token (shaves ~170ms off sentence aggregation)
        )

        # 4. LLM & Context Management (OpenAI GPT-4o-mini with Tuned Temperature & Token Cap)
        model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm = OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            settings=OpenAILLMService.Settings(
                model=model_name,
                temperature=0.6,
                max_tokens=150,  # Fast streaming first-token generation
            ),
        )

        # Initialize conversation messages context
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        context = LLMContext(messages=messages, tools=TOOLS_SCHEMA)

        # Configure ultra-fast barge-in and turn-taking strategies
        user_params = None
        if allow_interruptions:
            from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
            from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
                TranscriptionUserTurnStartStrategy,
            )
            from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
                SpeechTimeoutUserTurnStopStrategy,
            )
            from pipecat.turns.user_turn_strategies import UserTurnStrategies

            user_params = LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(
                    start=[TranscriptionUserTurnStartStrategy(use_interim=True)],
                    stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.28)],  # Instant 280ms stop bypasses ONNX CPU model
                )
            )

        context_aggregator = LLMContextAggregatorPair(
            context,
            user_params=user_params,
        )

        # Real-time spoken acknowledgment processor for live tools
        ack_processor = ToolAcknowledgmentProcessor(enabled=enable_fillers)

        # 5. Build Pipeline with Lexical Echo Shield and 3D Visualizer Forwarder
        pipeline_processors = [
            transport.input(),
            stt,
            echo_filter,               # Drops self-echo from Deepgram before context_aggregator sees it!
            context_aggregator.user(),
            llm,
            ack_processor,
            bot_recorder,              # Records bot speech text for echo tracking
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

        # 2. Speech-to-Text (Deepgram Nova-2 with Fast 120ms Endpointing)
        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            settings=DeepgramSTTService.Settings(
                model="nova-2-general",
                language="en",
                endpointing=120,
                interim_results=True,
                smart_format=True,
            ),
        )

        # 3. Text-to-Speech (Cartesia Sonic with Token Streaming Mode)
        voice_id = os.getenv(
            "CARTESIA_VOICE_ID", "79a125e8-cd45-4c13-8a67-188112f4dd22"
        )
        tts = CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY", ""),
            settings=CartesiaTTSService.Settings(voice=voice_id),
            text_aggregation_mode=TextAggregationMode.TOKEN,
        )

        # 4. LLM & Context Management (OpenAI GPT-4o-mini with Tuned Settings)
        model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm = OpenAILLMService(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            settings=OpenAILLMService.Settings(
                model=model_name,
                temperature=0.6,
                max_tokens=150,
            ),
        )

        # Initialize conversation messages context
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        context = LLMContext(messages=messages, tools=TOOLS_SCHEMA)

        from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
        from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
            TranscriptionUserTurnStartStrategy,
        )
        from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
            SpeechTimeoutUserTurnStopStrategy,
        )
        from pipecat.turns.user_turn_strategies import UserTurnStrategies

        user_params = LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[TranscriptionUserTurnStartStrategy(use_interim=True)],
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.28)],
            )
        )

        context_aggregator = LLMContextAggregatorPair(context, user_params=user_params)

        # Real-time spoken acknowledgment processor for live tools
        ack_processor = ToolAcknowledgmentProcessor()

        # 5. Build Pipeline
        pipeline_processors = [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            ack_processor,
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
        print(f" SERVER & 3D VISUALIZER LIVE AT: http://localhost:{args.visualizer_port}")
        print(f" REST API: http://localhost:{args.visualizer_port}/api/status")
        print(f" WEBRTC SESSION API: POST http://localhost:{args.visualizer_port}/api/webrtc/session")
        print(f"{'='*70}\n")

    try:
        if args.mode == "local" or args.mode == "server":
            # Runs the full voice pipeline (Mic/Speaker/VAD/STT/LLM/TTS) while serving all REST APIs & 3D Visualizer
            await run_local_voice_agent(
                allow_interruptions=getattr(args, "allow_interruptions", True),
                headphones=getattr(args, "headphones", False),
                enable_fillers=not getattr(args, "no_fillers", False),
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
        "--no-interruptions",
        action="store_true",
        default=False,
        help="Disable barge-in speech interruptions.",
    )
    parser.add_argument(
        "--allow-interruptions",
        action="store_true",
        default=True,
        help="Enable mid-sentence speech interruptions (Enabled by default).",
    )
    parser.add_argument(
        "--headphones",
        "--headset",
        dest="headphones",
        action="store_true",
        default=False,
        help="Enable if wearing headphones/earphones. Keeps mic 100%% open for full-duplex voice barge-in. (Default: False, enables Speaker Echo Shield).",
    )
    parser.add_argument(
        "--no-fillers",
        action="store_true",
        default=False,
        help="Disable spoken tool fillers for immediate direct answers without conversational acknowledgment.",
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
    args.allow_interruptions = not args.no_interruptions

    try:
        asyncio.run(main_async(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Voice agent stopped.")


if __name__ == "__main__":
    main()
