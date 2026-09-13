"""
Voice abstractions for Phase 7.

SpeechToText and TextToSpeech are abstract interfaces.
Implementations can be local (Whisper, Kokoro/Piper) or remote.

Voice is always optional — the app works fully without it.
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional

from app.config import Config

logger = logging.getLogger("voice")


class SpeechToTextProvider(ABC):
    """Abstract STT interface."""

    @abstractmethod
    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> dict:
        """
        Transcribe audio to text.
        Returns {"text": str, "confidence": float} or {"text": "", "error": str}.
        """
        ...

    @property
    @abstractmethod
    def available(self) -> bool:
        ...


class TextToSpeechProvider(ABC):
    """Abstract TTS interface."""

    @abstractmethod
    def synthesize(self, text: str, voice: str = "default") -> dict:
        """
        Synthesize text to audio.
        Returns {"audio_bytes": bytes, "mime_type": str} or {"error": str}.
        """
        ...

    @property
    @abstractmethod
    def available(self) -> bool:
        ...


class DummySTT(SpeechToTextProvider):
    """Fallback STT that always fails gracefully."""

    @property
    def available(self) -> bool:
        return False

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> dict:
        return {"text": "", "error": "Speech-to-text not available. Install whisper for local STT."}


class DummyTTS(TextToSpeechProvider):
    """Fallback TTS that always fails gracefully."""

    @property
    def available(self) -> bool:
        return False

    def synthesize(self, text: str, voice: str = "default") -> dict:
        return {"error": "Text-to-speech not available. Install kokoro or piper for local TTS."}


class WhisperSTT(SpeechToTextProvider):
    """Local CPU-int8 faster-whisper STT (optional)."""

    def __init__(self, model_size: str = "base"):
        self._model = None
        self._model_size = model_size
        self._load_attempted = False
        self._last_error = ""

    def _try_load(self):
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
            logger.info("whisper_stt_loaded model=%s", self._model_size)
        except ImportError:
            self._last_error = "faster-whisper is not installed"
            logger.info("faster_whisper_not_installed")
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("whisper_load_failed error=%s", exc)

    @property
    def available(self) -> bool:
        self._try_load()
        return self._model is not None

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> dict:
        self._try_load()
        if not self._model:
            return {"text": "", "error": "Whisper model not available."}
        tmp_path = None
        try:
            import os
            import tempfile
            suffix = ".webm" if "webm" in mime_type else ".wav"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as audio_file:
                audio_file.write(audio_bytes)
                tmp_path = audio_file.name
            segments, _ = self._model.transcribe(tmp_path, beam_size=1)
            return {"text": "".join(segment.text for segment in segments).strip(), "confidence": 0.8}
        except Exception as exc:
            logger.warning("transcription_failed error=%s", exc)
            return {"text": "", "error": "Transcription failed."}
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

class PiperTTS(TextToSpeechProvider):
    """Local Piper TTS (optional)."""

    def __init__(self):
        self._available = False
        self._try_load_attempted = False

    def _try_load(self):
        if self._try_load_attempted:
            return
        self._try_load_attempted = True
        try:
            import subprocess
            result = subprocess.run(["piper", "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                self._available = True
                logger.info("piper_tts_available")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.info("piper_not_installed")

    @property
    def available(self) -> bool:
        self._try_load()
        return self._available

    def synthesize(self, text: str, voice: str = "default") -> dict:
        self._try_load()
        if not self._available:
            return {"error": "Piper TTS not available."}
        try:
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                out_path = f.name
            proc = subprocess.run(
                ["piper", "--text", text, "--output_file", out_path],
                capture_output=True, timeout=30,
            )
            if proc.returncode != 0:
                return {"error": f"Piper failed: {proc.stderr.decode()[:200]}"}
            with open(out_path, "rb") as f:
                audio_bytes = f.read()
            import os
            os.unlink(out_path)
            return {"audio_bytes": audio_bytes, "mime_type": "audio/wav"}
        except Exception as e:
            return {"error": f"TTS failed: {e}"}


class KokoroTTS(TextToSpeechProvider):
    """Natural local Kokoro neural TTS; model assets load on first use."""

    def __init__(self, voice: str = "am_michael"):
        self._voice = voice
        self._pipeline = None
        self._load_attempted = False

    def _try_load(self):
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            from pykokoro import KokoroPipeline, PipelineConfig
            self._pipeline = KokoroPipeline(PipelineConfig(voice=self._voice))
            logger.info("kokoro_tts_loaded voice=%s", self._voice)
        except Exception as exc:
            logger.warning("kokoro_load_failed error=%s", exc)

    @property
    def available(self) -> bool:
        self._try_load()
        return self._pipeline is not None

    def synthesize(self, text: str, voice: str = "default") -> dict:
        self._try_load()
        if not self._pipeline:
            return {"error": "Kokoro TTS is not available."}
        try:
            import io
            import soundfile as sf
            result = self._pipeline.run(text, voice=self._voice if voice == "default" else voice)
            output = io.BytesIO()
            sf.write(output, result.audio, result.sample_rate, format="WAV")
            return {"audio_bytes": output.getvalue(), "mime_type": "audio/wav"}
        except Exception as exc:
            logger.warning("kokoro_synthesis_failed error=%s", exc)
            return {"error": "Kokoro synthesis failed."}

# Global instances (lazy-loaded)
_stt: Optional[SpeechToTextProvider] = None
_tts: Optional[TextToSpeechProvider] = None


def get_stt() -> SpeechToTextProvider:
    global _stt
    if _stt is None:
        if not Config.VOICE_ENABLED or Config.STT_PROVIDER.lower() != "whisper":
            _stt = DummySTT()
        else:
            _stt = WhisperSTT(Config.WHISPER_MODEL)
    return _stt


def get_tts() -> TextToSpeechProvider:
    global _tts
    if _tts is None:
        if not Config.VOICE_ENABLED:
            _tts = DummyTTS()
        elif Config.TTS_PROVIDER.lower() == "kokoro":
            _tts = KokoroTTS(Config.KOKORO_VOICE)
        elif Config.TTS_PROVIDER.lower() == "piper":
            _tts = PiperTTS()
        else:
            _tts = DummyTTS()
    return _tts