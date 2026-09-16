from __future__ import annotations

import asyncio
import logging
import time

from .config import Config
from .database import Database
from .listenbrainz import ListenBrainzClient
from .telegram_source import TelegramSource

LOGGER = logging.getLogger(__name__)


class DownloaderService:
    def __init__(
        self,
        config: Config,
        database: Database,
        listenbrainz: ListenBrainzClient,
        telegram: TelegramSource,
    ) -> None:
        self.config = config
        self.database = database
        self.listenbrainz = listenbrainz
        self.telegram = telegram

    async def run_once(self) -> None:
        if self.config.watch_saved_messages:
            if self.config.dry_run:
                LOGGER.info("DRY_RUN: Saved Messages importing is paused")
            else:
                imported = await self.telegram.import_saved_messages(
                    self.database,
                    self.config.saved_messages_initial_limit,
                    self.config.saved_messages_poll_limit,
                )
                if imported:
                    LOGGER.info(
                        "Imported %d audio file(s) from Saved Messages", imported
                    )

        previous_cursor = self.database.get_meta_int("last_seen_listen_ts")
        if previous_cursor is None:
            since = int(time.time()) - self.config.initial_lookback_hours * 3600
            LOGGER.info(
                "First run: looking back %d hours", self.config.initial_lookback_hours
            )
        else:
            # Re-read one second to avoid dropping entries around a timestamp boundary.
            since = max(0, previous_cursor - 1)

        listens = await self.listenbrainz.fetch_since(since)
        new_listens = sum(self.database.register_listen(track) for track in listens)
        if listens:
            newest = max(track.listened_at for track in listens)
            self.database.set_meta_int(
                "last_seen_listen_ts", max(previous_cursor or 0, newest)
            )
        elif previous_cursor is None:
            self.database.set_meta_int("last_seen_listen_ts", int(time.time()) - 1)
        LOGGER.info("Discovered %d new listen(s)", new_listens)

        expired = self.database.expire_old(self.config.max_retry_hours * 3600)
        if expired:
            LOGGER.info("Expired %d track(s) after the retry window", expired)

        pending = self.database.due_tracks(self.config.max_tracks_per_cycle)
        if not pending:
            LOGGER.info("No tracks are due for search")
            return

        retry_seconds = self.config.retry_interval_minutes * 60
        for item in pending:
            track = item.as_track()
            try:
                candidate = await self.telegram.find(track)
                if candidate is None:
                    self.database.mark_attempt(
                        item.key, "No acceptable audio match", retry_seconds
                    )
                    continue
                if self.config.dry_run:
                    LOGGER.info(
                        "DRY_RUN: would download %s - %s from %s message %s",
                        track.artist,
                        track.title,
                        candidate.channel,
                        candidate.message.id,
                    )
                    self.database.mark_attempt(item.key, "Dry-run match", retry_seconds)
                    continue
                path = await self.telegram.download(track, candidate)
                self.database.mark_downloaded(
                    item.key, str(path), candidate.channel, candidate.message.id
                )
                LOGGER.info("Downloaded %s", path.name)
            except Exception as exc:
                LOGGER.exception(
                    "Failed while processing %s - %s", track.artist, track.title
                )
                self.database.mark_attempt(item.key, str(exc), retry_seconds)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            self.database.set_meta_int("service_heartbeat_ts", int(time.time()))
            try:
                await self.run_once()
            except Exception:
                LOGGER.exception("Synchronization cycle failed")
            finally:
                self.database.set_meta_int("service_heartbeat_ts", int(time.time()))
            remaining = max(
                1, self.config.poll_interval_seconds - (time.monotonic() - started)
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=remaining)
            except TimeoutError:
                pass
