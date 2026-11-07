"""What a speech service answers, as bytes on a loopback origin, and how a test reads what it was sent."""

import io
import wave
from http import HTTPStatus

from speech_runtime_client.wire import SPEECH_PATH, ResponseFormat, SpeechRequest, refusal

from tests.support.loopback_origin import Origin, http_response

SPOKEN_WAV = b"RIFF\x00\x00\x00\x00WAVE-spoken-by-the-fake-service"
WAV_MEDIA_TYPE = ResponseFormat.WAV.media_type
AUDIENCE_SCOPE = "speech-service-audience"


def silent_wav(seconds: float = 0.2, rate: int = 8000) -> bytes:
    """A WAV a browser decodes: mono 16-bit silence."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(bytes(int(seconds * rate) * 2))
    return buffer.getvalue()


def chunked_wav(wav: bytes, *, terminated: bool = True) -> bytes:
    head = (
        f"HTTP/1.1 200 OK\r\nContent-Type: {WAV_MEDIA_TYPE}\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    ).encode()
    body = f"{len(wav):x}\r\n".encode() + wav + b"\r\n"
    return head + body + (b"0\r\n\r\n" if terminated else b"")


def refusal_answer(code, param=None, *, retry_after_seconds=None) -> bytes:
    wired = refusal(code, param)
    status = wired.status
    extra = b"" if retry_after_seconds is None else f"Retry-After: {retry_after_seconds}\r\n".encode()
    return http_response(f"{status.value} {status.phrase}", wired.model_dump_json().encode(), extra_header=extra)


def not_chunked_answer() -> bytes:
    return http_response(
        f"{HTTPStatus.OK.value} OK", SPOKEN_WAV, extra_header=f"Content-Type: {WAV_MEDIA_TYPE}\r\n".encode()
    )


def speaking(origin: Origin, answer: bytes) -> None:
    origin.serve(SPEECH_PATH, answer)


def header_of(request, name: str) -> str:
    _, _, headers, _ = request
    return next(value for header, value in headers.items() if header.lower() == name.lower())


def spoken_request(request) -> SpeechRequest:
    """The request parsed with the client's own model, never rebuilt."""
    _, _, _, body = request
    return SpeechRequest.model_validate_json(body)
