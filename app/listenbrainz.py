from __future__ import annotations

import asyncio
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .matching import Track

LOGGER = logging.getLogger(__name__)


class ListenBrainzError(RuntimeError):
    pass


class ListenBrainzClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        token: str | None,
        count: int,
        max_pages: int,
    ) -> None:
        self.base_url = base_url
        self.username = username
        self.token = token
        self.count = count
        self.max_pages = max_pages

    def _request_page(self, max_ts: int | None) -> dict:
        params: dict[str, str | int] = {"count": self.count}
        if max_ts is not None:
            params["max_ts"] = max_ts
        url = (
            f"{self.base_url}/1/user/{quote(self.username, safe='')}/listens?"
            f"{urlencode(params)}"
        )
        headers = {
            "Accept": "application/json",
            "User-Agent": "lb-telegram-downloader/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Token {self.token}"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=25) as response:
                return json.load(response)
        except HTTPError as exc:
            detail = exc.read(300).decode("utf-8", errors="replace")
            raise ListenBrainzError(
                f"ListenBrainz returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ListenBrainzError(f"ListenBrainz request failed: {exc}") from exc

    async def fetch_since(self, since_ts: int) -> list[Track]:
        tracks: list[Track] = []
        seen_listens: set[str] = set()
        max_ts: int | None = None

        for page_number in range(self.max_pages):
            document = await asyncio.to_thread(self._request_page, max_ts)
            raw_listens = document.get("payload", {}).get("listens", [])
            if not isinstance(raw_listens, list):
                raise ListenBrainzError("Unexpected ListenBrainz response format")
            if not raw_listens:
                break

            timestamps: list[int] = []
            for item in raw_listens:
                try:
                    listened_at = int(item["listened_at"])
                    metadata = item["track_metadata"]
                    artist = str(metadata["artist_name"]).strip()
                    title = str(metadata["track_name"]).strip()
                except (KeyError, TypeError, ValueError):
                    LOGGER.warning("Skipping a malformed ListenBrainz entry")
                    continue
                timestamps.append(listened_at)
                if listened_at <= since_ts or not artist or not title:
                    continue
                recording_id = str(item.get("recording_msid") or "")
                album = str(metadata.get("release_name") or "").strip()
                track = Track(artist, title, listened_at, recording_id, album)
                if track.listen_key not in seen_listens:
                    tracks.append(track)
                    seen_listens.add(track.listen_key)

            if not timestamps:
                break
            oldest = min(timestamps)
            if oldest <= since_ts or len(raw_listens) < self.count:
                break
            max_ts = oldest
        else:
            LOGGER.warning(
                "Reached LISTEN_FETCH_MAX_PAGES=%s; increase it if listens may be missed",
                self.max_pages,
            )

        return sorted(tracks, key=lambda item: item.listened_at)
