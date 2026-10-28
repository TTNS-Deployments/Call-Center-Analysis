"""Recognise an audio file's real format from its first bytes.

Recordings are often mislabelled (an .m4a phone recording renamed to .mp3, or a web page
saved as .mp3), and Azure Speech then rejects the file with an unhelpful error. Checking the
content at upload time lets the portal say what the file actually is.
"""

from __future__ import annotations

NAMES = {".wav": "WAV", ".mp3": "MP3", ".ogg": "OGG/Opus", ".flac": "FLAC", ".m4a": "M4A (MP4 audio)",
         ".webm": "WebM", ".wma": "WMA", ".aac": "AAC", ".amr": "AMR"}

# Extensions that share a container: an .opus file is Ogg.
SAME_FORMAT = {".opus": ".ogg"}

_ID3 = b"ID3"

# Smaller than this cannot hold a usable recording (about one second of low-bitrate MP3): usually an
# empty, truncated or placeholder file. Azure Speech rejects such files as "audio format not supported".
MIN_AUDIO_BYTES = 2048


def _skip_id3(data: bytes) -> bytes:
    """Strip a leading ID3v2 tag (MP3 or AAC). Its size is 4 bytes of 7 bits each."""
    if data[:3] != _ID3 or len(data) < 10:
        return data
    size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
    return data[10 + size:]


def detect_format(data: bytes) -> str | None:
    """The extension matching the content (".mp3", ".wav", ...), or None when it is not a known audio format."""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data[:4] == b"OggS":
        return ".ogg"
    if data[:4] == b"fLaC":
        return ".flac"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if data[:4] == b"\x30\x26\xb2\x75":  # ASF header
        return ".wma"
    if data[4:8] == b"ftyp":
        return ".m4a"
    if data[:5] == b"#!AMR":
        return ".amr"
    frames = _skip_id3(data)
    if len(frames) >= 2 and frames[0] == 0xFF and frames[1] & 0xE0 == 0xE0:
        # MPEG frame sync. Layer bits 00 = AAC (ADTS); anything else = MPEG audio (MP3).
        return ".aac" if frames[1] & 0x06 == 0 else ".mp3"
    if data[:3] == _ID3 and not frames:
        return ".mp3"  # only the tag was read; the frames follow
    return None


def matches(extension: str, detected: str | None) -> bool:
    return detected is not None and SAME_FORMAT.get(extension, extension) == detected


def describe(detected: str | None) -> str:
    return NAMES.get(detected or "", "not a recognised audio format")
