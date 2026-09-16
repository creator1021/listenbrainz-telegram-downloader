from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TPE2
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from .matching import Track


class MetadataError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddedMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""


def _replace_id3_text(tags: ID3, frame_id: str, frame: object) -> None:
    tags.delall(frame_id)
    tags.add(frame)


def _id3_text(tags: ID3 | None, frame_id: str) -> str:
    if tags is None:
        return ""
    frame = tags.get(frame_id)
    return str(frame).strip() if frame is not None else ""


def _first_tag_value(audio: object, key: str) -> str:
    values = audio.get(key, [])  # type: ignore[attr-defined]
    if isinstance(values, (list, tuple)):
        return str(values[0]).strip() if values else ""
    return str(values).strip() if values else ""


def read_track_album(path: Path) -> str:
    """Return the embedded album tag, or an empty string when none is present."""
    suffix = path.suffix.casefold()
    if suffix == ".aac":
        return ""
    try:
        if suffix == ".wav":
            return _id3_text(WAVE(path).tags, "TALB")
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return ""
        return _first_tag_value(audio, "album")
    except Exception as exc:
        raise MetadataError(str(exc)) from exc


def read_embedded_metadata(path: Path) -> EmbeddedMetadata:
    """Read the title, artist and album already embedded in an audio file."""
    suffix = path.suffix.casefold()
    if suffix == ".aac":
        return EmbeddedMetadata()
    try:
        if suffix == ".wav":
            tags = WAVE(path).tags
            return EmbeddedMetadata(
                title=_id3_text(tags, "TIT2"),
                artist=_id3_text(tags, "TPE1"),
                album=_id3_text(tags, "TALB"),
            )
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return EmbeddedMetadata()
        return EmbeddedMetadata(
            title=_first_tag_value(audio, "title"),
            artist=_first_tag_value(audio, "artist"),
            album=_first_tag_value(audio, "album"),
        )
    except Exception as exc:
        raise MetadataError(str(exc)) from exc


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return "image/jpeg"


def _picture(data: bytes, mime: str) -> Picture:
    picture = Picture()
    picture.type = 3
    picture.mime = mime
    picture.desc = "Front cover"
    picture.data = data
    return picture


def embed_cover_art_if_missing(path: Path, image_path: Path) -> bool:
    """Embed a Telegram thumbnail as front cover without replacing existing art."""
    data = image_path.read_bytes()
    if not data:
        return False
    mime = _image_mime(data)
    suffix = path.suffix.casefold()
    try:
        if suffix == ".mp3":
            audio = MP3(path)
            if audio.tags is None:
                audio.add_tags()
            if audio.tags.getall("APIC"):
                return False
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
            audio.save()
            return True
        if suffix == ".wav":
            audio = WAVE(path)
            if audio.tags is None:
                audio.add_tags()
            if audio.tags.getall("APIC"):
                return False
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
            audio.save()
            return True
        if suffix == ".flac":
            audio = FLAC(path)
            if audio.pictures:
                return False
            audio.add_picture(_picture(data, mime))
            audio.save()
            return True
        if suffix in {".m4a", ".mp4"}:
            audio = MP4(path)
            if audio.tags is None:
                audio.add_tags()
            if audio.tags.get("covr"):
                return False
            image_format = (
                MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
            )
            audio.tags["covr"] = [MP4Cover(data, imageformat=image_format)]
            audio.save()
            return True
        if suffix in {".ogg", ".opus"}:
            audio = OggOpus(path) if suffix == ".opus" else OggVorbis(path)
            if audio.get("metadata_block_picture"):
                return False
            encoded = base64.b64encode(_picture(data, mime).write()).decode("ascii")
            audio["metadata_block_picture"] = [encoded]
            audio.save()
            return True
        return False
    except Exception as exc:
        raise MetadataError(str(exc)) from exc


def _write_wave(path: Path, track: Track, fallback_album: str) -> str:
    audio = WAVE(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    album = track.album.strip() or _id3_text(tags, "TALB") or fallback_album.strip()
    _replace_id3_text(tags, "TIT2", TIT2(encoding=3, text=[track.title]))
    _replace_id3_text(tags, "TPE1", TPE1(encoding=3, text=[track.artist]))
    if not _id3_text(tags, "TPE2"):
        _replace_id3_text(tags, "TPE2", TPE2(encoding=3, text=[track.artist]))
    if album:
        _replace_id3_text(tags, "TALB", TALB(encoding=3, text=[album]))
    audio.save()
    return album


def write_track_metadata(path: Path, track: Track, fallback_album: str = "") -> str:
    """Write Navidrome-friendly title, artist and album tags in-place.

    Existing artwork and unrelated tags are preserved. Album priority is the
    ListenBrainz release name, the file's existing Telegram album tag, then an
    explicitly configured fallback.
    """
    suffix = path.suffix.casefold()
    if suffix == ".aac":
        raise MetadataError(
            "raw AAC/ADTS does not have reliably supported embedded tags; "
            "prefer MP3, M4A, FLAC, OGG or OPUS"
        )
    try:
        if suffix == ".wav":
            return _write_wave(path, track, fallback_album)

        audio = MutagenFile(path, easy=True)
        if audio is None:
            raise MetadataError("unsupported or unrecognised audio container")
        if audio.tags is None:
            audio.add_tags()
        album = (
            track.album.strip()
            or _first_tag_value(audio, "album")
            or fallback_album.strip()
        )
        audio["title"] = [track.title]
        audio["artist"] = [track.artist]
        if not _first_tag_value(audio, "albumartist"):
            audio["albumartist"] = [track.artist]
        if album:
            audio["album"] = [album]
        audio.save()
        return album
    except MetadataError:
        raise
    except Exception as exc:
        raise MetadataError(str(exc)) from exc
