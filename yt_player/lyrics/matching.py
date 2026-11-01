"""Pure helpers for turning track metadata into a lyrics query and picking a match."""

import re
from dataclasses import dataclass

from yt_player.lyrics.models import LyricLine

# Words in brackets that describe the upload rather than the song.
_NOISE = re.compile(
    r"\s*[(\[](?:[^)\]]*\b(?:official|lyrics?|audio|video|visuali[sz]er|hd|hq|4k|"
    r"remaster(?:ed)?|mv|m/v|explicit|clean)\b[^)\]]*)[)\]]",
    re.IGNORECASE,
)
_FEATURING = re.compile(
    r"\s*[(\[]\s*(?:feat\.?|ft\.?|featuring)\s[^)\]]*[)\]]|\s+(?:feat\.?|ft\.?|featuring)\s+.*$",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r"\s+[-–—]\s+")
_LRC_STAMP = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_LRC_TAG = re.compile(r"\[[^\]]*\]")

# A different cut of a song (live, radio edit, extended) differs in length,
# and its timestamps would be wrong, so reject candidates outside this window.
DURATION_TOLERANCE_SECONDS = 4.0


@dataclass(frozen=True)
class LyricsQuery:
    title: str
    artist: str | None
    duration_seconds: float | None


def clean_artist(artist: str | None) -> str | None:
    if not artist:
        return None
    # YouTube Music joins several artists with commas or ampersands; the first
    # is what lyrics databases index by.
    first = re.split(r"\s*(?:,|&| x | and )\s*", artist, maxsplit=1)[0]
    first = re.sub(r"\s*-\s*topic$", "", first, flags=re.IGNORECASE)
    first = re.sub(r"vevo$", "", first, flags=re.IGNORECASE).strip()
    return first or None


def build_query(title: str, artist: str | None, duration_seconds: float | None) -> LyricsQuery:
    """Reduce an upload title such as "Artist - Song (Official Video)" to its parts."""
    name = _NOISE.sub("", title)
    name = re.sub(r"\s*\|.*$", "", name).strip()
    by = clean_artist(artist)
    parts = _SEPARATOR.split(name, maxsplit=1)
    if len(parts) == 2:
        left, right = (part.strip() for part in parts)
        # Trust the title's own "Artist - Song" split unless the channel name
        # says the song comes first.
        if by and by.lower() in right.lower() and by.lower() not in left.lower():
            name = left
        else:
            name, by = right, clean_artist(left) or by
    name = _FEATURING.sub("", name).strip() or title.strip()
    return LyricsQuery(title=name, artist=by, duration_seconds=duration_seconds)


def parse_lrc(lrc: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    for raw in lrc.splitlines():
        stamps = _LRC_STAMP.findall(raw)
        text = _LRC_TAG.sub("", raw).strip()
        for minutes, seconds in stamps:
            lines.append(LyricLine(time_seconds=int(minutes) * 60 + float(seconds), text=text))
    lines.sort(key=lambda line: line.time_seconds or 0)
    return lines


def parse_plain(text: str) -> list[LyricLine]:
    return [LyricLine(text=line.strip()) for line in text.strip().splitlines()]


def _normalise(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def score_candidate(query: LyricsQuery, candidate: dict) -> float | None:
    """Higher is better; None means the candidate must not be used."""
    duration = candidate.get("duration")
    if query.duration_seconds:
        # Unknown length cannot be checked, and wrong-cut timestamps are worse
        # than no lyrics.
        if not isinstance(duration, (int, float)):
            return None
        gap = abs(duration - query.duration_seconds)
        if gap > DURATION_TOLERANCE_SECONDS:
            return None
        score = 10 - gap
    else:
        score = 0.0

    if not (candidate.get("syncedLyrics") or candidate.get("plainLyrics") or candidate.get("instrumental")):
        return None
    if candidate.get("syncedLyrics"):
        score += 5

    want_title = _normalise(query.title)
    got_title = _normalise(candidate.get("trackName"))
    if want_title and got_title:
        if want_title == got_title:
            score += 6
        elif want_title in got_title or got_title in want_title:
            score += 3
        else:
            score -= 6

    want_artist = _normalise(query.artist)
    got_artist = _normalise(candidate.get("artistName"))
    if want_artist and got_artist:
        if want_artist in got_artist or got_artist in want_artist:
            score += 4
        else:
            score -= 4
    return score


def pick_best(query: LyricsQuery, candidates: list[dict]) -> dict | None:
    scored = [
        (score, candidate)
        for candidate in candidates
        if isinstance(candidate, dict)
        and (score := score_candidate(query, candidate)) is not None
    ]
    if not scored:
        return None
    best_score, best = max(scored, key=lambda item: item[0])
    # Without a duration to anchor on, insist on a plausible title/artist match.
    return best if best_score > 0 else None
