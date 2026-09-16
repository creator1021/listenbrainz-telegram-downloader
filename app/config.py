from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


def _integer(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def _floating(name: str, default: float, minimum: float = 0) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        part.strip() for part in os.getenv(name, default).split(",") if part.strip()
    )


@dataclass(frozen=True)
class Config:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_phone: str | None
    telegram_channels: tuple[str, ...]
    telegram_session: Path
    listenbrainz_username: str
    listenbrainz_token: str | None
    listenbrainz_base_url: str
    data_dir: Path
    download_dir: Path
    poll_interval_seconds: int
    initial_lookback_hours: int
    listen_fetch_count: int
    listen_fetch_max_pages: int
    search_results_per_query: int
    match_threshold: float
    retry_interval_minutes: int
    max_retry_hours: int
    max_tracks_per_cycle: int
    max_file_size_mb: int
    allowed_extensions: tuple[str, ...]
    write_metadata: bool
    metadata_fallback_album: str
    watch_saved_messages: bool
    saved_messages_initial_limit: int
    saved_messages_poll_limit: int
    healthcheck_max_age_seconds: int
    dry_run: bool
    log_level: str

    @classmethod
    def from_env(cls, *, service: bool = True) -> Config:
        api_id = _integer("TELEGRAM_API_ID", 0)
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        data_dir = Path(os.getenv("DATA_DIR", "/data")).expanduser()
        channels = _csv("TELEGRAM_CHANNELS")
        username = os.getenv("LISTENBRAINZ_USERNAME", "").strip()

        missing: list[str] = []
        if not api_id:
            missing.append("TELEGRAM_API_ID")
        if not api_hash:
            missing.append("TELEGRAM_API_HASH")
        if service and not channels:
            missing.append("TELEGRAM_CHANNELS")
        if service and not username:
            missing.append("LISTENBRAINZ_USERNAME")
        if missing:
            raise ConfigError("Missing required setting(s): " + ", ".join(missing))

        extensions = tuple(
            item if item.startswith(".") else f".{item}"
            for item in _csv("ALLOWED_EXTENSIONS", "mp3,m4a,flac,ogg,opus,wav,aac")
        )
        threshold = _floating("MATCH_THRESHOLD", 76.0, 0)
        if threshold > 100:
            raise ConfigError("MATCH_THRESHOLD cannot be greater than 100")

        return cls(
            telegram_api_id=api_id,
            telegram_api_hash=api_hash,
            telegram_phone=os.getenv("TELEGRAM_PHONE", "").strip() or None,
            telegram_channels=channels,
            telegram_session=Path(
                os.getenv("TELEGRAM_SESSION", str(data_dir / "telegram"))
            ).expanduser(),
            listenbrainz_username=username,
            listenbrainz_token=os.getenv("LISTENBRAINZ_TOKEN", "").strip() or None,
            listenbrainz_base_url=os.getenv(
                "LISTENBRAINZ_BASE_URL", "https://api.listenbrainz.org"
            ).rstrip("/"),
            data_dir=data_dir,
            download_dir=Path(os.getenv("DOWNLOAD_DIR", "/downloads")).expanduser(),
            poll_interval_seconds=_integer("POLL_INTERVAL_SECONDS", 300, 15),
            initial_lookback_hours=_integer("INITIAL_LOOKBACK_HOURS", 6, 1),
            listen_fetch_count=_integer("LISTEN_FETCH_COUNT", 100, 1),
            listen_fetch_max_pages=_integer("LISTEN_FETCH_MAX_PAGES", 10, 1),
            search_results_per_query=_integer("SEARCH_RESULTS_PER_QUERY", 50, 1),
            match_threshold=threshold,
            retry_interval_minutes=_integer("RETRY_INTERVAL_MINUTES", 60, 1),
            max_retry_hours=_integer("MAX_RETRY_HOURS", 168, 1),
            max_tracks_per_cycle=_integer("MAX_TRACKS_PER_CYCLE", 10, 1),
            max_file_size_mb=_integer("MAX_FILE_SIZE_MB", 150, 1),
            allowed_extensions=extensions,
            write_metadata=_boolean("WRITE_METADATA", True),
            metadata_fallback_album=os.getenv("METADATA_FALLBACK_ALBUM", "").strip(),
            watch_saved_messages=_boolean("WATCH_SAVED_MESSAGES", False),
            saved_messages_initial_limit=_integer(
                "SAVED_MESSAGES_INITIAL_LIMIT", 20, 1
            ),
            saved_messages_poll_limit=_integer("SAVED_MESSAGES_POLL_LIMIT", 200, 1),
            healthcheck_max_age_seconds=_integer("HEALTHCHECK_MAX_AGE_SECONDS", 0, 0),
            dry_run=_boolean("DRY_RUN", False),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )
