"""Suggest YouTube candidates for a MusicBrainz recording, never auto-import.

Title/artist/length heuristics are deliberately suggestions, NOT proof.
User chooses a video and acknowledges download rights before ingest.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from music_bot.catalog import is_variant, normalize
from music_bot.youtube import VideoCandidate

NOISE = frozenset({
    "official", "video", "audio", "lyrics", "lyric", "hd", "4k",
    "visualizer", "music", "mv", "remastered", "remaster", "hq",
    "musicvideo", "version", "full",
})


@dataclass(frozen=True)
class SourceSuggestion:
    video: VideoCandidate
    score: float
    warnings: tuple[str, ...]

    def public(self) -> dict:
        return {
            **self.video.public(),
            "match_score": self.score,
            "warnings": list(self.warnings),
            "match_confirmed": False,
        }


def words(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w]+", normalize(text))
            if len(token) >= 2 and token not in NOISE}


def match_score(recording: dict, video: VideoCandidate) -> SourceSuggestion | None:
    title = recording.get("title", "")
    artist = recording.get("artist", "")
    if not isinstance(title, str) or not isinstance(artist, str):
        return None
    title_terms = words(title)
    artist_terms = words(artist)
    video_terms = words(video.title)
    credit_terms = video_terms | words(video.channel)
    if not title_terms:
        return None
    coverage = len(title_terms & video_terms) / len(title_terms)
    if coverage < 0.55:
        return None
    artist_coverage = (len(artist_terms & credit_terms) / len(artist_terms)
                       if artist_terms else 0.0)
    length_ms = recording.get("duration_ms")
    warnings: list[str] = []
    if isinstance(length_ms, int) and length_ms > 0 and video.duration_sec:
        deviation = abs(length_ms / 1000.0 - video.duration_sec)
        duration_score = max(0.0, 1.0 - deviation / 75.0)
        if deviation > 40:
            warnings.append("Длительность заметно отличается")
    else:
        duration_score = 0.40
        warnings.append("Не удалось сравнить длительность")

    score = coverage * 0.66 + artist_coverage * 0.24 + duration_score * 0.10
    if is_variant(video.title) and not is_variant(title):
        score -= 0.22
        warnings.append("Возможно ремикс, кавер или live")
    if not artist_coverage:
        warnings.append("Не совпал исполнитель")
    return SourceSuggestion(video, round(max(0.0, min(1.0, score)), 3),
                            tuple(warnings))


def suggest_sources(recording: dict, videos: list[VideoCandidate],
                    limit: int = 6) -> list[SourceSuggestion]:
    if not 1 <= limit <= 8:
        raise ValueError("Invalid suggestion count")
    suggestions = [
        suggestion for video in videos
        if (suggestion := match_score(recording, video)) is not None
    ]
    suggestions.sort(key=lambda item: (-item.score, item.video.video_id))
    return suggestions[:limit]
