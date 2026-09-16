from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import replace

from telethon import TelegramClient

from .config import Config, ConfigError
from .database import Database
from .listenbrainz import ListenBrainzClient
from .metadata import MetadataError, read_track_album, write_track_metadata
from .service import DownloaderService
from .telegram_source import TelegramSource

LOGGER = logging.getLogger(__name__)
OLD_DEFAULT_ALBUM = "ListenBrainz Downloads"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download recent ListenBrainz tracks from Telegram"
    )
    parser.add_argument(
        "command",
        choices=(
            "run",
            "once",
            "auth",
            "channels",
            "status",
            "retag",
            "restore-albums",
            "healthcheck",
        ),
        nargs="?",
        default="run",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=os.getenv("APP_VERSION", "development"),
    )
    return parser


def _client(config: Config) -> TelegramClient:
    config.telegram_session.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(config.telegram_session),
        config.telegram_api_id,
        config.telegram_api_hash,
        flood_sleep_threshold=60,
        auto_reconnect=True,
    )


async def _authenticate(config: Config) -> None:
    client = _client(config)
    try:
        if config.telegram_phone:
            await client.start(phone=config.telegram_phone)
        else:
            await client.start()
        me = await client.get_me()
        label = (
            getattr(me, "username", None) or getattr(me, "first_name", None) or me.id
        )
        print(f"Telegram authentication saved successfully for {label}.")
    finally:
        await client.disconnect()


async def _list_channels(config: Config) -> None:
    client = _client(config)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Authenticate first with the 'auth' command")
        print("ID\tTYPE\tUSERNAME\tNAME")
        async for dialog in client.iter_dialogs():
            if not (dialog.is_channel or dialog.is_group):
                continue
            entity = dialog.entity
            username = getattr(entity, "username", None) or "-"
            kind = "channel" if dialog.is_channel and not dialog.is_group else "group"
            print(f"{dialog.id}\t{kind}\t{username}\t{dialog.name}")
    finally:
        await client.disconnect()


def _build_service(
    config: Config,
) -> tuple[DownloaderService, Database, TelegramSource]:
    database = Database(config.data_dir / "state.sqlite3")
    telegram = TelegramSource(
        _client(config),
        config.telegram_channels,
        config.allowed_extensions,
        config.max_file_size_mb,
        config.search_results_per_query,
        config.match_threshold,
        config.download_dir,
        config.write_metadata,
        config.metadata_fallback_album,
    )
    listenbrainz = ListenBrainzClient(
        config.listenbrainz_base_url,
        config.listenbrainz_username,
        config.listenbrainz_token,
        config.listen_fetch_count,
        config.listen_fetch_max_pages,
    )
    return (
        DownloaderService(config, database, listenbrainz, telegram),
        database,
        telegram,
    )


async def _service_command(config: Config, once: bool) -> None:
    service, database, telegram = _build_service(config)
    try:
        await telegram.connect()
        if once:
            await service.run_once()
            return
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        await service.run_forever(stop)
    finally:
        await telegram.disconnect()
        database.close()


async def _restore_albums(config: Config) -> None:
    """Undo the v1.2 fallback for files whose Telegram original still exists."""
    _, database, telegram = _build_service(config)
    restored = skipped = failed = 0
    connected = False
    try:
        for item in database.downloaded_tracks():
            if not item.path.is_file():
                LOGGER.warning("Downloaded file is missing: %s", item.path)
                skipped += 1
                continue
            try:
                current_album = read_track_album(item.path)
            except MetadataError as exc:
                LOGGER.warning("Could not read metadata from %s: %s", item.path, exc)
                failed += 1
                continue
            if current_album.casefold() != OLD_DEFAULT_ALBUM.casefold():
                skipped += 1
                continue

            album = item.track.album.strip()
            try:
                if not album:
                    if not item.source_channel or item.source_message_id is None:
                        raise RuntimeError(
                            "stored Telegram source reference is missing"
                        )
                    if not connected:
                        await telegram.connect()
                        connected = True
                    album = await telegram.original_album(
                        item.source_channel,
                        item.source_message_id,
                        item.path.suffix,
                    )
                if not album:
                    raise RuntimeError("the original Telegram file has no album tag")
                recovered = replace(item.track, album=album)
                write_track_metadata(item.path, recovered, fallback_album="")
                database.update_album(item.track.key, album)
                LOGGER.info("Restored album %r for %s", album, item.path.name)
                restored += 1
            except Exception as exc:  # noqa: BLE001 - continue recovering other files
                LOGGER.warning("Could not restore album for %s: %s", item.path, exc)
                failed += 1
    finally:
        if connected:
            await telegram.disconnect()
        database.close()
    print(f"restored={restored} unchanged={skipped} failed={failed}")


async def _main(command: str) -> None:
    service_mode = command in {"run", "once", "restore-albums"}
    config = Config.from_env(service=service_mode)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if command == "auth":
        await _authenticate(config)
    elif command == "channels":
        await _list_channels(config)
    elif command == "status":
        database = Database(config.data_dir / "state.sqlite3")
        try:
            counts = database.counts()
            print(" ".join(f"{key}={value}" for key, value in counts.items()))
        finally:
            database.close()
    elif command == "healthcheck":
        database = Database(config.data_dir / "state.sqlite3")
        try:
            heartbeat = database.get_meta_int("service_heartbeat_ts")
        finally:
            database.close()
        if heartbeat is None:
            raise RuntimeError("service heartbeat has not been recorded yet")
        maximum_age = config.healthcheck_max_age_seconds or max(
            300, config.poll_interval_seconds * 3
        )
        age = int(time.time()) - heartbeat
        if age > maximum_age:
            raise RuntimeError(
                f"service heartbeat is stale ({age}s; maximum {maximum_age}s)"
            )
        print(f"healthy heartbeat_age_seconds={age}")
    elif command == "retag":
        database = Database(config.data_dir / "state.sqlite3")
        updated = skipped = failed = 0
        try:
            for item in database.downloaded_tracks():
                if not item.path.is_file():
                    LOGGER.warning("Downloaded file is missing: %s", item.path)
                    skipped += 1
                    continue
                try:
                    write_track_metadata(
                        item.path,
                        item.track,
                        fallback_album=config.metadata_fallback_album,
                    )
                    updated += 1
                except MetadataError as exc:
                    LOGGER.warning("Could not retag %s: %s", item.path, exc)
                    failed += 1
        finally:
            database.close()
        print(f"retagged={updated} missing={skipped} failed={failed}")
    elif command == "restore-albums":
        await _restore_albums(config)
    else:
        await _service_command(config, once=command == "once")


def main() -> int:
    args = _parser().parse_args()
    try:
        asyncio.run(_main(args.command))
    except (ConfigError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
