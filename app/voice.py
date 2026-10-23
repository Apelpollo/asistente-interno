"""Voz: audio -> texto (Whisper local) -> agente -> texto -> audio (Azure AI Speech).

- Voz a texto: faster-whisper corre en CPU dentro del contenedor (sin costo, el audio no sale de la máquina).
  Detecta el idioma; el asistente atiende en español e inglés.
- Texto a voz: Azure AI Speech por REST (voces neuronales es-MX / en-US).
  Si no hay AZURE_SPEECH_KEY, el endpoint de voz responde solo con texto.
"""
import io
import re
from functools import lru_cache
from xml.sax.saxutils import escape

import av
import httpx
import numpy as np
from faster_whisper import WhisperModel

from app.config import settings

IDIOMAS = ("es", "en")
VOCES = {"es": ("es-MX", "es-MX-DaliaNeural"), "en": ("en-US", "en-US-JennyNeural")}
MAX_CHARS_TTS = 1500  # el plan gratuito de Azure tiene cuota mensual de caracteres


@lru_cache(maxsize=1)
def _modelo() -> WhisperModel:
    # Se carga en la primera petición (la primera vez también lo descarga: ~460 MB para "small")
    return WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")


def decodificar(audio: bytes, sampling_rate: int = 16000) -> np.ndarray:
    """Cualquier formato (mp4, m4a, mp3, ogg, wav...) -> audio mono a 16 kHz, float32 en [-1, 1].

    Se hace aquí con PyAV en lugar de usar el decodificador de faster-whisper, porque ese
    usa un parámetro que PyAV quitó en versiones recientes y truena con 'metadata_errors'.
    """
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    partes = []
    try:
        with av.open(io.BytesIO(audio), mode="r") as contenedor:
            for frame in contenedor.decode(audio=0):
                partes += [f.to_ndarray() for f in resampler.resample(frame)]
            partes += [f.to_ndarray() for f in resampler.resample(None)]  # vacía lo que quedó en el buffer
    except (av.error.FFmpegError, IndexError) as e:
        raise ValueError(f"No se pudo leer el audio: {e}") from e
    if not partes:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(partes, axis=1).reshape(-1).astype(np.float32) / 32768.0


def transcribir(audio: bytes) -> dict:
    """Audio (wav, mp3, m4a, mp4, ogg...) -> texto + idioma detectado."""
    audio = decodificar(audio)
    segmentos, info = _modelo().transcribe(audio, vad_filter=True, beam_size=5)
    texto = " ".join(s.text.strip() for s in segmentos).strip()
    idioma = info.language
    if idioma not in IDIOMAS:
        # En audios cortos Whisper a veces confunde el idioma: se repite forzando español
        segmentos, _ = _modelo().transcribe(audio, language="es", vad_filter=True, beam_size=5)
        texto = " ".join(s.text.strip() for s in segmentos).strip()
        idioma = "es"
    return {"texto": texto, "idioma": idioma, "confianza_idioma": round(info.language_probability, 2)}


def limpiar_para_voz(texto: str) -> str:
    """Quita lo que no se debe leer en voz alta: citas [1], markdown, etiquetas como 'Evidencia:'."""
    texto = re.sub(r"\[\d+\]", "", texto)
    texto = re.sub(r"[*_#`>|]", "", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:MAX_CHARS_TTS]


def tts_disponible() -> bool:
    return bool(settings.azure_speech_key and settings.azure_speech_region)


def sintetizar(texto: str, idioma: str = "es") -> bytes:
    """Texto -> MP3 con Azure AI Speech (REST, sin SDK)."""
    lang, voz = VOCES.get(idioma, VOCES["es"])
    ssml = (f"<speak version='1.0' xml:lang='{lang}'><voice name='{voz}'>"
            f"{escape(limpiar_para_voz(texto))}</voice></speak>")
    r = httpx.post(
        f"https://{settings.azure_speech_region}.tts.speech.microsoft.com/cognitiveservices/v1",
        headers={
            "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "asistente-interno",
        },
        content=ssml.encode("utf-8"),
        timeout=30,
    )
    r.raise_for_status()
    return r.content
