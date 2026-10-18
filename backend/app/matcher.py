"""Domain-independent keyword matcher used before the semantic LLM layer.

The user supplies one free-text Watch Item. We extract strong identifiers and
supporting keywords from it, then evaluate finalized transcript text. Deepgram
can later use the same terms as recognition hints without changing this API.
"""

import math
import re

from .models import ExtractedWatchTerms, MatchResult, StructuredWatchIntent


_TOKEN_RE = re.compile(
    r"(?=[a-z0-9]*\d)[a-z0-9]+|[a-z]+(?:'[a-z]+)?",
    re.IGNORECASE,
)
_GATE_RE = re.compile(r"\bgate\s+([a-z]?\d{1,3}[a-z]?)\b", re.IGNORECASE)
_PLATFORM_RE = re.compile(r"\bplatform\s+([a-z]?\d{1,3}[a-z]?)\b", re.IGNORECASE)
_DESTINATION_RE = re.compile(
    r"\bto\s+([a-z][a-z .'-]*?)"
    r"(?=\s+(?:is|has|will|was|now|at|from|boarding|departing|delayed|cancelled|"
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


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_phrase(phrase)
    return bool(normalized_phrase) and f" {normalized_phrase} " in f" {normalized_text} "


def _intent_identifier_groups(
    intent: StructuredWatchIntent,
    *,
    required_only: bool = False,
) -> list[tuple[str, list[str]]]:
    return [
        (identifier.value, [identifier.value, *identifier.aliases])
        for identifier in intent.identifiers
        if not required_only or identifier.required
    ]


def _intent_event(
    intent: StructuredWatchIntent,
    normalized_transcript: str,
) -> tuple[str, str] | None:
    for event in intent.interesting_events:
        if _contains_phrase(normalized_transcript, event):
            event_type = _normalize_phrase(event).replace(" ", "_")
            return event_type, event.strip().capitalize()
    return None


def _no_match(confidence: float, reason: str) -> MatchResult:
    return MatchResult(match=False, confidence=confidence, reason=reason)


def _is_direct_call(
    identifier_groups: list[tuple[str, list[str]]],
    keywords: list[str],
    transcript_tokens: list[str],
    normalized_transcript: str,
) -> bool:
    """Recognize a short name/number call that is itself the relevant event."""

    if not transcript_tokens or len(transcript_tokens) > 5:
        return False

    if identifier_groups:
        if not all(
            any(_contains_phrase(normalized_transcript, alias) for alias in aliases)
            for _, aliases in identifier_groups
        ):
            return False
        matched_keywords = sum(
            _contains_phrase(normalized_transcript, keyword) for keyword in keywords
        )
        # `Order 47` has context; an isolated `47` is also a common pickup call.
        identifier_alias_token_sets = {
            frozenset(_tokens(alias))
            for _, aliases in identifier_groups
            for alias in aliases
        }
        return matched_keywords > 0 or frozenset(transcript_tokens) in identifier_alias_token_sets

    if not keywords:
        return False
    matched_keywords = sum(
        _contains_phrase(normalized_transcript, keyword) for keyword in keywords
    )
    return matched_keywords >= math.ceil(len(keywords) * 0.67)


def _is_name_call(
    intent: StructuredWatchIntent,
    transcript_tokens: list[str],
    normalized_transcript: str,
) -> bool:
    """Recognize an extracted name target alone or with a calling cue."""

    if not transcript_tokens:
        return False

    calling_cues = {"call", "called", "calling", "page", "paged", "paging"}
    negative_phrases = (
        "not here",
        "isnt here",
        "wasnt here",
        "talked to",
        "spoke with",
        "meeting with",
        "meeting about",
        "yesterday",
        "tomorrow",
    )
    if any(_contains_phrase(normalized_transcript, phrase) for phrase in negative_phrases):
        return False

    for target in intent.speech_targets:
        if _normalize_phrase(target.kind) != "name call":
            continue
        for term in target.terms:
            if not _contains_phrase(normalized_transcript, term):
                continue
            term_tokens = frozenset(_tokens(term))
            transcript_token_set = frozenset(transcript_tokens)
            if transcript_token_set == term_tokens:
                return True
            normalized_term = re.escape(_normalize_phrase(term))
            natural_call_patterns = (
                rf"\bis {normalized_term} here\b",
                rf"\b{normalized_term} is here\b",
                rf"\bwhere is {normalized_term}\b",
                rf"\blooking for {normalized_term}\b",
                rf"\b{normalized_term} please\b",
                rf"\b(?:can|could|would|will) {normalized_term}\b",
                rf"\b{normalized_term} (?:come|report|proceed|needed)\b",
            )
            if any(
                re.search(pattern, normalized_transcript)
                for pattern in natural_call_patterns
            ):
                return True
            if len(transcript_tokens) <= 12 and calling_cues & transcript_token_set:
                return True
    return False


def _destination_conflicts(watch_item: str, transcript: str) -> bool:
    target = _DESTINATION_RE.search(watch_item)
    announced = _DESTINATION_RE.search(transcript)
    if not target or not announced:
        return False
    return _normalize_phrase(target.group(1)) != _normalize_phrase(announced.group(1))


def match_transcript(
    watch_item: str,
    transcript: str,
    intent: StructuredWatchIntent | None = None,
) -> MatchResult:
    """Evaluate a finalized transcript against an arbitrary free-text Watch Item."""

    if not watch_item.strip():
        return _no_match(1.0, "No watch item is configured.")
    if not transcript.strip():
        return _no_match(1.0, "The transcript is empty.")
    if intent is not None and intent.sound_targets and not intent.speech_targets:
        return _no_match(
            1.0,
            "This Watch Item only uses environmental sound detection.",
        )

    terms = extract_watch_terms(watch_item)
    identifier_groups = (
        _intent_identifier_groups(intent)
        if intent is not None
        else [(identifier, [identifier]) for identifier in terms.identifiers]
    )
    required_identifier_groups = (
        _intent_identifier_groups(intent, required_only=True)
        if intent is not None
        else identifier_groups
    )
    keywords = intent.context_terms if intent is not None else terms.keywords
    transcript_token_list = _tokens(transcript)
    normalized_transcript = " ".join(transcript_token_list)
    event = _detect_event(transcript)
    if event is None and intent is not None:
        event = _intent_event(intent, normalized_transcript)
    name_call = intent is not None and _is_name_call(
        intent,
        transcript_token_list,
        normalized_transcript,
    )
    direct_call = name_call or (
        event is None
        and _is_direct_call(
            identifier_groups,
            keywords,
            transcript_token_list,
            normalized_transcript,
        )
    )
    if direct_call:
        event = ("called", "Name or number announced")
    if event is None:
        return _no_match(0.95, "No relevant event was announced.")

    missing_identifiers = [
        value
        for value, aliases in required_identifier_groups
        if not any(_contains_phrase(normalized_transcript, alias) for alias in aliases)
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
        keyword
        for keyword in keywords
        if _contains_phrase(normalized_transcript, keyword)
    ]
    required_keyword_count = 0 if direct_call and identifier_groups else (
        min(len(keywords), max(1, math.ceil(len(keywords) / 2)))
        if keywords
        else 0
    )
    if len(matched_keywords) < required_keyword_count:
        return _no_match(
            0.90,
            "Too few supporting keywords matched "
            f"({len(matched_keywords)}/{required_keyword_count} required).",
        )

    if not identifier_groups and not keywords:
        return _no_match(1.0, "The Watch Item contains no usable identifying terms.")

    event_type, detail = event
    keyword_ratio = (
        len(matched_keywords) / len(keywords) if keywords else 1.0
    )
    confidence = min(0.98, 0.88 + (0.10 * keyword_ratio))
    evidence = [value for value, _ in identifier_groups] + matched_keywords

    return MatchResult(
        match=True,
        confidence=round(confidence, 2),
        event_type=event_type,
        title=watch_item.strip(),
        detail=detail,
        reason=(
            "Identifying terms and enough supporting terms matched: "
            + ", ".join(evidence)
        ),
    )
