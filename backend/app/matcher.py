"""Domain-independent keyword matcher used before the semantic LLM layer.

The user supplies one free-text Watch Item. We extract strong identifiers and
supporting keywords from it, then evaluate finalized transcript text. Deepgram
can later use the same terms as recognition hints without changing this API.
"""

import math
import re

from .models import ExtractedWatchTerms, MatchResult


_TOKEN_RE = re.compile(
    r"(?=[a-z0-9]*\d)[a-z0-9]+|[a-z]+(?:'[a-z]+)?",
    re.IGNORECASE,
)
_GATE_RE = re.compile(r"\bgate\s+([a-z]?\d{1,3}[a-z]?)\b", re.IGNORECASE)
_PLATFORM_RE = re.compile(r"\bplatform\s+([a-z]?\d{1,3}[a-z]?)\b", re.IGNORECASE)
_DESTINATION_RE = re.compile(
    r"\bto\s+([a-z][a-z .'-]*?)"
    r"(?=\s+(?:is|has|will|was|now|at|boarding|departing|delayed|cancelled|"
    r"canceled|moved|service)\b|[,!.?]|$)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "a", "about", "an", "and", "at", "for", "from", "i", "i'm", "im", "is",
    "me", "my", "no", "number", "of", "on", "please", "the", "to",
    "wait", "waiting", "when", "with",
}

_TERM_ALIASES = {
    "doctor": "dr",
    "doctors": "dr",
    "cancelled": "canceled",
}


def _normalize_token(token: str) -> str:
    normalized = token.lower().strip(".'")
    return _TERM_ALIASES.get(normalized, normalized)


def _tokens(text: str) -> list[str]:
    return [_normalize_token(token) for token in _TOKEN_RE.findall(text)]


def _normalize_phrase(text: str) -> str:
    return " ".join(_tokens(text))


def _unique_in_order(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def extract_watch_terms(watch_item: str) -> ExtractedWatchTerms:
    """Extract strict identifiers and supporting terms from arbitrary text.

    Tokens containing digits are identifiers because confusing `47` with `74`
    is materially worse than missing an alert. Remaining content words become
    supporting keywords. A later LLM extractor can replace this implementation
    while returning the same model.
    """

    tokens = _tokens(watch_item)
    identifiers = _unique_in_order(
        [token for token in tokens if any(character.isdigit() for character in token)]
    )
    keywords = _unique_in_order(
        [
            token
            for token in tokens
            if token not in _STOPWORDS
            and token not in identifiers
            and len(token) > 1
        ]
    )
    return ExtractedWatchTerms(identifiers=identifiers, keywords=keywords)


def _detect_event(transcript: str) -> tuple[str, str] | None:
    normalized = _normalize_phrase(transcript)
    gate = _GATE_RE.search(transcript)
    platform = _PLATFORM_RE.search(transcript)

    if gate and any(term in normalized for term in ("moved", "changed", "now at")):
        return "gate_change", f"Gate changed to {gate.group(1).upper()}"
    if platform and any(
        term in normalized for term in ("moved", "changed", "now depart", "now at")
    ):
        return "platform_change", f"Platform changed to {platform.group(1).upper()}"
    if "final call" in normalized or "last call" in normalized:
        return "final_call", "Final call announced"
    if "boarding" in normalized or "now board" in normalized:
        if gate:
            return "boarding", f"Now boarding at gate {gate.group(1).upper()}"
        if platform:
            return "boarding", f"Now boarding at platform {platform.group(1).upper()}"
        return "boarding", "Now boarding"
    if "canceled" in normalized:
        return "cancellation", "Cancelled"
    if "delayed" in normalized or "delay" in normalized:
        return "delay", "Delayed"
    if "ready for pickup" in normalized or "ready for pick up" in normalized:
        return "ready", "Ready for pickup"
    if "is ready" in normalized or "now ready" in normalized:
        return "ready", "Ready"
    if "has been called" in normalized or "now calling" in normalized:
        return "called", "Now being called"
    if any(
        phrase in normalized
        for phrase in ("please report", "please proceed", "please come")
    ):
        return "called", "Please proceed"
    if "now arriving" in normalized or "has arrived" in normalized:
        return "arrival", "Now arriving"
    if any(
        phrase in normalized
        for phrase in (
            "now available for pickup",
            "is available for pickup",
            "now available for collection",
            "is available for collection",
        )
    ):
        return "available", "Now available"

    return None


def _no_match(confidence: float, reason: str) -> MatchResult:
    return MatchResult(match=False, confidence=confidence, reason=reason)


def _is_direct_call(
    terms: ExtractedWatchTerms,
    transcript_tokens: list[str],
) -> bool:
    """Recognize a short name/number call that is itself the relevant event."""

    if not transcript_tokens or len(transcript_tokens) > 5:
        return False

    token_set = set(transcript_tokens)
    if terms.identifiers:
        if not all(identifier in token_set for identifier in terms.identifiers):
            return False
        matched_keywords = sum(keyword in token_set for keyword in terms.keywords)
        # `Order 47` has context; an isolated `47` is also a common pickup call.
        return matched_keywords > 0 or token_set == set(terms.identifiers)

    if not terms.keywords:
        return False
    matched_keywords = sum(keyword in token_set for keyword in terms.keywords)
    return matched_keywords >= math.ceil(len(terms.keywords) * 0.67)


def _destination_conflicts(watch_item: str, transcript: str) -> bool:
    target = _DESTINATION_RE.search(watch_item)
    announced = _DESTINATION_RE.search(transcript)
    if not target or not announced:
        return False
    return _normalize_phrase(target.group(1)) != _normalize_phrase(announced.group(1))


def match_transcript(watch_item: str, transcript: str) -> MatchResult:
    """Evaluate a finalized transcript against an arbitrary free-text Watch Item."""

    if not watch_item.strip():
        return _no_match(1.0, "No watch item is configured.")
    if not transcript.strip():
        return _no_match(1.0, "The transcript is empty.")

    terms = extract_watch_terms(watch_item)
    transcript_token_list = _tokens(transcript)
    transcript_tokens = set(transcript_token_list)
    event = _detect_event(transcript)
    direct_call = event is None and _is_direct_call(terms, transcript_token_list)
    if direct_call:
        event = ("called", "Name or number announced")
    if event is None:
        return _no_match(0.95, "No relevant event was announced.")

    missing_identifiers = [
        identifier for identifier in terms.identifiers if identifier not in transcript_tokens
    ]
    if missing_identifiers:
        return _no_match(
            0.99,
            "Required identifier(s) were not announced: "
            + ", ".join(missing_identifiers),
        )

    if _destination_conflicts(watch_item, transcript):
        return _no_match(0.99, "An announced destination conflicts with the Watch Item.")

    matched_keywords = [
        keyword for keyword in terms.keywords if keyword in transcript_tokens
    ]
    required_keyword_count = 0 if direct_call and terms.identifiers else (
        min(len(terms.keywords), max(1, math.ceil(len(terms.keywords) / 2)))
        if terms.keywords
        else 0
    )
    if len(matched_keywords) < required_keyword_count:
        return _no_match(
            0.90,
            "Too few supporting keywords matched "
            f"({len(matched_keywords)}/{required_keyword_count} required).",
        )

    if not terms.identifiers and not terms.keywords:
        return _no_match(1.0, "The Watch Item contains no usable identifying terms.")

    event_type, detail = event
    keyword_ratio = (
        len(matched_keywords) / len(terms.keywords) if terms.keywords else 1.0
    )
    confidence = min(0.98, 0.88 + (0.10 * keyword_ratio))
    evidence = terms.identifiers + matched_keywords

    return MatchResult(
        match=True,
        confidence=round(confidence, 2),
        event_type=event_type,
        title=watch_item.strip(),
        detail=detail,
        reason=(
            "Required identifiers and enough supporting terms matched: "
            + ", ".join(evidence)
        ),
    )
