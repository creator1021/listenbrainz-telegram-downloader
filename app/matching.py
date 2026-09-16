from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class Track:
    artist: str
    title: str
    listened_at: int
    recording_id: str = ""
    album: str = ""

    @property
    def key(self) -> str:
        raw = f"{normalize(self.artist)}\0{normalize(self.title)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def listen_key(self) -> str:
        raw = f"{self.listened_at}\0{self.recording_id}\0{self.key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"\b(feat|ft|featuring)\.?\b", " ", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio() * 100


def _tokens(value: str) -> set[str]:
    return set(normalize(value).split())


def _coverage(needle: str, haystack: str) -> float:
    wanted = _tokens(needle)
    if not wanted:
        return 0.0
    return 100 * len(wanted & _tokens(haystack)) / len(wanted)


def _token_set_ratio(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    common = left_tokens & right_tokens
    common_text = " ".join(sorted(common))
    left_text = " ".join(sorted(common | (left_tokens - common)))
    right_text = " ".join(sorted(common | (right_tokens - common)))
    if not common_text:
        return _ratio(left_text, right_text)
    return max(
        _ratio(common_text, left_text),
        _ratio(common_text, right_text),
        _ratio(left_text, right_text),
    )


def score_candidate(
    track: Track,
    searchable_text: str,
    *,
    embedded_title: str = "",
    embedded_artist: str = "",
) -> tuple[float, float, float]:
    """Return overall, title and artist scores on a 0-100 scale."""
    haystack = normalize(searchable_text)
    title_similarity = max(
        _token_set_ratio(track.title, haystack),
        _ratio(normalize(track.title), normalize(embedded_title)),
    )
    artist_similarity = max(
        _token_set_ratio(track.artist, haystack),
        _ratio(normalize(track.artist), normalize(embedded_artist)),
    )
    title_coverage = max(
        _coverage(track.title, haystack), _coverage(track.title, embedded_title)
    )
    artist_coverage = max(
        _coverage(track.artist, haystack), _coverage(track.artist, embedded_artist)
    )

    # Very short titles must occur as a full token; this avoids matching "One"
    # against filenames that merely contain "someone".
    title_norm = normalize(track.title)
    if len(title_norm) <= 4 and title_norm not in _tokens(haystack):
        title_similarity = min(title_similarity, 35.0)
        title_coverage = 0.0

    title_score = 0.60 * title_similarity + 0.40 * title_coverage
    artist_score = 0.60 * artist_similarity + 0.40 * artist_coverage
    overall = 0.68 * title_score + 0.32 * artist_score
    return round(overall, 2), round(title_score, 2), round(artist_score, 2)


def acceptable(score: tuple[float, float, float], threshold: float) -> bool:
    overall, title_score, artist_score = score
    return overall >= threshold and title_score >= 68 and artist_score >= 45


def safe_component(value: str, limit: int = 100) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", value)
    value = " ".join(value.split()).strip(" .")
    return (value or "Unknown")[:limit].rstrip(" .")
