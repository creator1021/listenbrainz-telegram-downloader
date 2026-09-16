from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from telethon import TelegramClient

from .database import Database
from .matching import Track, acceptable, safe_component, score_candidate
from .metadata import (
    MetadataError,
    embed_cover_art_if_missing,
    read_embedded_metadata,
    read_track_album,
    write_track_metadata,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class Candidate:
    channel: str
    message: Any
    extension: str
    score: float
    title_score: float
    artist_score: float
    size: int


def _channel_reference(value: str) -> str | int:
    value = value.strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value


class TelegramSource:
    def __init__(
        self,
        client: TelegramClient,
        channel_refs: tuple[str, ...],
        allowed_extensions: tuple[str, ...],
        max_file_size_mb: int,
        search_limit: int,
        threshold: float,
        download_dir: Path,
        write_metadata: bool = True,
        metadata_fallback_album: str = "",
    ) -> None:
        self.client = client
        self.channel_refs = channel_refs
        self.allowed_extensions = {item.casefold() for item in allowed_extensions}
        self.max_bytes = max_file_size_mb * 1024 * 1024
        self.search_limit = search_limit
        self.threshold = threshold
        self.download_dir = download_dir
        self.write_metadata = write_metadata
        self.metadata_fallback_album = metadata_fallback_album
        self.channels: list[tuple[str, Any]] = []

    async def connect(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError(
                "Telegram is not authenticated. Run: docker compose run --rm song-fetcher auth"
            )

        # A numeric channel ID is not sufficient on its own for Telegram API
        # calls: channels and megagroups also require an access hash. Telethon
        # normally stores that hash in its session entity cache, but a fresh or
        # partially populated session can miss it and attempt a lookup with an
        # empty hash. In Telethon 1.45 that lookup may surface as an internal
        # ``IndexError: list index out of range``. Loading the account's live
        # dialogs gives us complete InputPeer objects and avoids that fragile
        # fallback path.
        numeric_refs = {
            reference
            for raw_ref in self.channel_refs
            if isinstance((reference := _channel_reference(raw_ref)), int)
        }
        dialog_entities: dict[int, Any] = {}
        if numeric_refs:
            try:
                async for dialog in self.client.iter_dialogs():
                    if dialog.id in numeric_refs:
                        dialog_entities[dialog.id] = dialog.input_entity
                        if len(dialog_entities) == len(numeric_refs):
                            break
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Could not load Telegram dialogs for channel resolution: %s", exc
                )

        for raw_ref in self.channel_refs:
            reference = _channel_reference(raw_ref)
            try:
                if isinstance(reference, int):
                    entity = dialog_entities.get(reference)
                    if entity is None:
                        raise ValueError(
                            "not present in this Telegram account's joined dialogs"
                        )
                else:
                    entity = await self.client.get_input_entity(reference)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Skipping Telegram channel %r because it cannot be resolved: "
                    "%s. Run the 'channels' command with this same Docker deployment "
                    "and copy the exact ID it prints.",
                    raw_ref,
                    exc,
                )
                continue
            self.channels.append((raw_ref, entity))
        if not self.channels:
            raise RuntimeError(
                "None of the configured Telegram channels could be resolved. "
                "Run the 'channels' command with this same Docker deployment and "
                "copy one or more exact IDs into TELEGRAM_CHANNELS."
            )
        LOGGER.info(
            "Resolved %d Telegram channels in configured priority order",
            len(self.channels),
        )

    async def disconnect(self) -> None:
        await self.client.disconnect()

    def _audio_details(self, message: Any) -> tuple[str, int, str, str, str] | None:
        file = getattr(message, "file", None)
        if file is None:
            return None
        name = str(getattr(file, "name", "") or "")
        mime = str(getattr(file, "mime_type", "") or "").casefold()
        extension = str(getattr(file, "ext", "") or Path(name).suffix or "").casefold()
        if not extension and mime:
            extension = mimetypes.guess_extension(mime) or ""
        if extension and not extension.startswith("."):
            extension = f".{extension}"
        is_audio = bool(getattr(message, "audio", None)) or mime.startswith("audio/")
        if not is_audio and extension not in self.allowed_extensions:
            return None
        if extension not in self.allowed_extensions:
            return None
        size = int(getattr(file, "size", 0) or 0)
        if size and size > self.max_bytes:
            return None
        title = str(getattr(file, "title", "") or "")
        performer = str(getattr(file, "performer", "") or "")
        return extension, size, name, title, performer

    async def find(self, track: Track) -> Candidate | None:
        queries = []
        for query in (f"{track.artist} {track.title}", track.title):
            query = " ".join(query.split())
            if query and query.casefold() not in {item.casefold() for item in queries}:
                queries.append(query)

        for channel_name, entity in self.channels:
            candidates: dict[int, Candidate] = {}
            for query in queries:
                try:
                    async for message in self.client.iter_messages(
                        entity, search=query, limit=self.search_limit
                    ):
                        if message.id in candidates:
                            continue
                        details = self._audio_details(message)
                        if details is None:
                            continue
                        extension, size, name, embedded_title, embedded_artist = details
                        searchable = " ".join(
                            part
                            for part in (
                                name,
                                embedded_title,
                                embedded_artist,
                                str(getattr(message, "raw_text", "") or ""),
                            )
                            if part
                        )
                        scores = score_candidate(
                            track,
                            searchable,
                            embedded_title=embedded_title,
                            embedded_artist=embedded_artist,
                        )
                        if not acceptable(scores, self.threshold):
                            continue
                        candidates[message.id] = Candidate(
                            channel_name,
                            message,
                            extension,
                            scores[0],
                            scores[1],
                            scores[2],
                            size,
                        )
                # One inaccessible/rate-limited channel should not prevent the
                # configured fallback channels from being searched.
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning(
                        "Telegram search failed in %s: %s", channel_name, exc
                    )
            if candidates:
                best = max(
                    candidates.values(), key=lambda item: (item.score, item.size)
                )
                LOGGER.info(
                    "Matched %s - %s in %s (score %.1f)",
                    track.artist,
                    track.title,
                    channel_name,
                    best.score,
                )
                return best
            LOGGER.info(
                "No acceptable match for %s - %s in %s",
                track.artist,
                track.title,
                channel_name,
            )
        return None

    async def download(self, track: Track, candidate: Candidate) -> Path:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        partial_dir = self.download_dir / ".partial"
        partial_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{safe_component(track.artist)} - {safe_component(track.title)}"
            f"{candidate.extension}"
        )
        destination = self.download_dir / filename
        if destination.exists() and destination.stat().st_size > 0:
            self._write_metadata(destination, track)
            LOGGER.info("File already exists; adopting %s", destination.name)
            return destination

        temporary = partial_dir / f"{uuid4().hex}{candidate.extension}"
        try:
            returned_path = await self.client.download_media(
                candidate.message, file=str(temporary)
            )
            actual = Path(returned_path) if returned_path else temporary
            if not actual.exists() or actual.stat().st_size == 0:
                raise RuntimeError("Telegram returned an empty download")
            self._write_metadata(actual, track)
            os.replace(actual, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    @staticmethod
    def _saved_message_track(
        path: Path,
        message: Any,
        filename: str,
        telegram_title: str,
        telegram_artist: str,
    ) -> Track:
        embedded = read_embedded_metadata(path)
        stem = Path(filename).stem.strip()
        filename_artist = ""
        filename_title = stem
        if " - " in stem:
            filename_artist, filename_title = (
                part.strip() for part in stem.split(" - ", 1)
            )
        artist = (
            embedded.artist.strip()
            or telegram_artist.strip()
            or filename_artist
            or "Unknown Artist"
        )
        title = (
            embedded.title.strip()
            or telegram_title.strip()
            or filename_title
            or f"Telegram audio {message.id}"
        )
        message_date = getattr(message, "date", None)
        listened_at = int(message_date.timestamp()) if message_date else 0
        return Track(artist, title, listened_at, album=embedded.album.strip())

    async def _embed_message_thumbnail(self, message: Any, audio_path: Path) -> None:
        document = getattr(message, "document", None)
        if not getattr(document, "thumbs", None):
            return
        cover_path = audio_path.parent / f"cover-{uuid4().hex}.jpg"
        actual = cover_path
        try:
            returned_path = await self.client.download_media(
                message,
                file=str(cover_path),
                thumb=-1,
            )
            actual = Path(returned_path) if returned_path else cover_path
            if (
                actual.is_file()
                and actual.stat().st_size
                and embed_cover_art_if_missing(audio_path, actual)
            ):
                LOGGER.info("Embedded Telegram cover art for %s", audio_path.name)
        except Exception as exc:  # noqa: BLE001 - cover failure must not lose audio
            LOGGER.warning("Could not embed Telegram cover art: %s", exc)
        finally:
            if actual.exists():
                actual.unlink()
            if actual != cover_path and cover_path.exists():
                cover_path.unlink()

    async def _download_saved_message(
        self,
        message: Any,
        details: tuple[str, int, str, str, str],
    ) -> tuple[Path, Track]:
        extension, _, filename, telegram_title, telegram_artist = details
        self.download_dir.mkdir(parents=True, exist_ok=True)
        partial_dir = self.download_dir / ".partial"
        partial_dir.mkdir(parents=True, exist_ok=True)
        temporary = partial_dir / f"saved-{uuid4().hex}{extension}"
        actual = temporary
        try:
            returned_path = await self.client.download_media(
                message, file=str(temporary)
            )
            actual = Path(returned_path) if returned_path else temporary
            if not actual.is_file() or actual.stat().st_size == 0:
                raise RuntimeError("Telegram returned an empty Saved Messages download")

            track = self._saved_message_track(
                actual,
                message,
                filename,
                telegram_title,
                telegram_artist,
            )
            destination = self.download_dir / (
                f"{safe_component(track.artist)} - {safe_component(track.title)}"
                f"{extension}"
            )
            if destination.exists() and destination.stat().st_size > 0:
                LOGGER.info(
                    "Saved Messages audio already exists; keeping %s", destination.name
                )
                return destination, track

            if self.write_metadata:
                self._write_metadata(actual, track)
                await self._embed_message_thumbnail(message, actual)
            os.replace(actual, destination)
            return destination, track
        finally:
            if actual.exists():
                actual.unlink()
            if temporary != actual and temporary.exists():
                temporary.unlink()

    async def import_saved_messages(
        self,
        database: Database,
        initial_limit: int,
        poll_limit: int = 200,
    ) -> int:
        """Import new audio forwards from the account's Saved Messages chat."""
        cursor = database.get_meta_int("last_seen_saved_message_id")
        limit = initial_limit if cursor is None else poll_limit
        recent = await self.client.get_messages("me", limit=limit)
        messages = sorted(
            (message for message in recent if cursor is None or message.id > cursor),
            key=lambda item: item.id,
        )
        if cursor is None:
            LOGGER.info(
                "First Saved Messages scan: inspecting the latest %d message(s)",
                len(messages),
            )
        LOGGER.info(
            "Saved Messages poll: cursor=%s inspected=%d new=%d",
            cursor if cursor is not None else "not-set",
            len(recent),
            len(messages),
        )

        imported = 0
        for message in messages:
            details = self._audio_details(message)
            try:
                if details is not None:
                    path, track = await self._download_saved_message(message, details)
                    imported += 1
                    LOGGER.info(
                        "Imported Saved Messages audio %s - %s as %s",
                        track.artist,
                        track.title,
                        path.name,
                    )
                else:
                    LOGGER.info(
                        "Ignored Saved Messages message %s: not an allowed audio file",
                        message.id,
                    )
                database.set_meta_int("last_seen_saved_message_id", int(message.id))
            except Exception:
                LOGGER.exception(
                    "Failed to import Saved Messages message %s; will retry",
                    message.id,
                )
                break
        return imported

    async def original_album(self, channel: str, message_id: int, suffix: str) -> str:
        """Download a temporary original and read its embedded album tag."""
        entity = next(
            (entity for configured, entity in self.channels if configured == channel),
            None,
        )
        if entity is None:
            raise RuntimeError(
                f"source channel {channel!r} is not in the resolved TELEGRAM_CHANNELS"
            )
        message = await self.client.get_messages(entity, ids=message_id)
        if message is None:
            raise RuntimeError("the original Telegram message is no longer available")

        partial_dir = self.download_dir / ".partial"
        partial_dir.mkdir(parents=True, exist_ok=True)
        temporary = partial_dir / f"metadata-{uuid4().hex}{suffix}"
        actual = temporary
        try:
            returned_path = await self.client.download_media(
                message, file=str(temporary)
            )
            actual = Path(returned_path) if returned_path else temporary
            if not actual.is_file() or actual.stat().st_size == 0:
                raise RuntimeError("Telegram returned an empty download")
            return read_track_album(actual)
        finally:
            if actual.exists():
                actual.unlink()
            if temporary != actual and temporary.exists():
                temporary.unlink()

    def _write_metadata(self, path: Path, track: Track) -> None:
        if not self.write_metadata:
            return
        try:
            album = write_track_metadata(
                path, track, fallback_album=self.metadata_fallback_album
            )
            LOGGER.info(
                "Wrote metadata for %s: title=%r artist=%r album=%r",
                path.name,
                track.title,
                track.artist,
                album,
            )
        except MetadataError as exc:
            LOGGER.warning("Could not write metadata for %s: %s", path.name, exc)
