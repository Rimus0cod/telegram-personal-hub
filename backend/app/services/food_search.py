"""Catalog search: normalization, transliteration, term building and ranking.

Search is a single PostgreSQL-backed path used by the API.  This module holds
the pure text logic plus the deterministic ranking constants; the router only
serializes results.

Design notes
------------
* Search terms are derived data.  ``food_search_terms`` stores one row per
  (food, language, term type, normalized term) and is rebuilt by imports, the
  translation workflow and ``search reindex``.
* Only approved translations are searchable.  Pending machine translations are
  stored in ``food_translations`` but stay out of the index until reviewed.
* Transliterations are generated variants, never written to ``food_aliases``.
* Canonical identity and nutrient values are never touched by search code.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import (
    CatalogItem,
    CommunityFood,
    FoodAlias,
    FoodBarcode,
    FoodSearchTerm,
    FoodTranslation,
)

TERM_TYPES = ("canonical", "translation", "alias", "transliteration")
APPROVED = "approved"

_APOSTROPHES = "'’‘`´ʹ"
_DASHES = "-–—−‑‐"
_APOSTROPHE_RE = re.compile(r"[" + re.escape(_APOSTROPHES) + "]")
_DASH_RE = re.compile(r"[" + re.escape(_DASHES) + "]")
_NON_WORD_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")
_CYRILLIC_RE = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)

# Search-friendly transliteration tables.  They are not official standards:
# the goal is to match what people type, so ambiguous letters produce variants.
_RU_TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
_UK_TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e",
    "є": "ye", "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ь": "", "ю": "yu", "я": "ya",
}
_TABLES = {"ru": _RU_TABLE, "uk": _UK_TABLE}

# Ranking tiers.  Match quality dominates; term type breaks exact ties; the
# transliteration type is always below original-language matches.
_TIERS: dict[tuple[str, str], int] = {
    ("canonical", "exact"): 100,
    ("translation", "exact"): 90,
    ("alias", "exact"): 80,
    ("transliteration", "exact"): 70,
    ("canonical", "prefix"): 60,
    ("translation", "prefix"): 56,
    ("alias", "prefix"): 52,
    ("transliteration", "prefix"): 45,
    ("canonical", "partial"): 30,
    ("translation", "partial"): 27,
    ("alias", "partial"): 24,
    ("transliteration", "partial"): 20,
}
BARCODE_SCORE = 1000
LANGUAGE_BONUS = 3
MIN_PARTIAL_LENGTH = 2


@dataclass(frozen=True)
class SearchTerm:
    language: str
    term_type: str
    term: str
    normalized: str


@dataclass(frozen=True)
class SearchHit:
    kind: str  # "curated" or "community"
    item: Any
    score: int
    term_length: int


def normalize_text(value: Any) -> str:
    """Casefold, strip diacritics, unify punctuation and collapse whitespace.

    Only characters that do not change the meaning are removed: apostrophes are
    dropped, dashes become spaces, other punctuation separates tokens.  Letters
    and digits are preserved.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.casefold()
    text = text.replace("ё", "е")
    text = _APOSTROPHE_RE.sub("", text)
    text = _DASH_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_WORD_RE.sub(" ", text)
    text = text.replace("_", " ")
    return _SPACES_RE.sub(" ", text).strip()


def has_cyrillic(value: str) -> bool:
    return bool(_CYRILLIC_RE.search(value or ""))


def _transliterate_raw(text: str, table: dict[str, str]) -> str:
    lowered = str(text or "").casefold()
    return "".join(table.get(ch, ch) for ch in lowered)


def transliteration_variants(text: str, language: str) -> list[str]:
    """Return normalized latin variants for a Cyrillic term.

    The primary form follows the language table; the secondary form uses the
    "ia/iu/ie/i" soft-vowel spellings, and a third form simplifies ``kh`` to
    ``h``.  This matches common user input such as ``kartoplia`` for
    ``картопля`` without storing every generated word as an alias.
    """
    if not has_cyrillic(text):
        return []
    table = _TABLES.get(language)
    if table is None:
        return []
    primary = _transliterate_raw(text, table)
    variants = {normalize_text(primary)}
    secondary = (
        primary.replace("ya", "ia").replace("yu", "iu")
        .replace("ye", "ie").replace("yi", "i")
    )
    variants.add(normalize_text(secondary))
    variants.add(normalize_text(primary.replace("kh", "h")))
    return sorted(variant for variant in variants if variant)


def _language_matches(term_language: str, requested: str) -> bool:
    term = (term_language or "und").lower()
    primary = requested.split("-")[0].lower()
    return term == primary


def _term_variants(base: SearchTerm) -> list[SearchTerm]:
    variants: list[SearchTerm] = []
    languages = (
        [base.language] if base.language in _TABLES
        else ["ru", "uk"] if base.language in {"und", ""} else []
    )
    for language in languages:
        for variant in transliteration_variants(base.term, language):
            variants.append(SearchTerm(
                language=language, term_type="transliteration",
                term=variant, normalized=variant,
            ))
    return variants


def build_search_terms(
    *,
    names: Iterable[str],
    translations: Iterable[tuple[str, str, str]],
    aliases: Iterable[tuple[str, str]],
) -> list[SearchTerm]:
    """Build deduplicated search terms for one food.

    ``translations`` is ``(language, name, review_status)``; only approved
    translations are indexed.  ``aliases`` is ``(language, alias)``.
    """
    terms: list[SearchTerm] = []
    seen: set[tuple[str, str, str]] = set()

    def add(language: str, term_type: str, term: str) -> None:
        term = str(term or "").strip()
        normalized = normalize_text(term)
        if not term or not normalized:
            return
        key = ((language or "und").lower(), term_type, normalized)
        if key in seen:
            return
        seen.add(key)
        terms.append(SearchTerm(
            language=key[0], term_type=term_type, term=term, normalized=normalized,
        ))

    for name in names:
        add("und", "canonical", name)
    for language, name, review_status in translations:
        if (review_status or "").lower() != APPROVED:
            continue
        add(language, "translation", name)
    for language, alias in aliases:
        add(language or "und", "alias", alias)

    for base in list(terms):
        for variant in _term_variants(base):
            add(variant.language, variant.term_type, variant.term)
    return terms


def terms_for_food(food: CatalogItem) -> list[SearchTerm]:
    """Build terms from a fully loaded CatalogItem (translations + aliases)."""
    return build_search_terms(
        names=[food.name, food.name_original or ""],
        translations=[
            (row.language, row.name, row.review_status)
            for row in food.translations
        ],
        aliases=[(row.language, row.alias) for row in food.aliases],
    )


def rebuild_search_terms(session: Session, food: CatalogItem) -> int:
    """Replace the stored search terms of one food; returns the new count."""
    session.query(FoodSearchTerm).filter_by(food_id=food.id).delete(
        synchronize_session=False
    )
    terms = terms_for_food(food)
    for term in terms:
        session.add(FoodSearchTerm(
            food_id=food.id, language=term.language,
            term_type=term.term_type, term=term.term[:200],
            normalized=term.normalized[:200],
        ))
    return len(terms)


def rebuild_search_terms_for_food_id(session: Session, food_id: int) -> int:
    """Rebuild one food from freshly queried translations and aliases."""
    food = session.get(CatalogItem, food_id)
    if food is None:
        return 0
    session.query(FoodSearchTerm).filter_by(food_id=food_id).delete(
        synchronize_session=False
    )
    translations = session.query(FoodTranslation).filter_by(food_id=food_id).all()
    aliases = session.query(FoodAlias).filter_by(food_id=food_id).all()
    terms = build_search_terms(
        names=[food.name, food.name_original or ""],
        translations=[(row.language, row.name, row.review_status)
                      for row in translations],
        aliases=[(row.language, row.alias) for row in aliases],
    )
    for term in terms:
        session.add(FoodSearchTerm(
            food_id=food_id, language=term.language,
            term_type=term.term_type, term=term.term[:200],
            normalized=term.normalized[:200],
        ))
    return len(terms)


def rebuild_all_search_terms(session: Session) -> int:
    total = 0
    for food_id in (row[0] for row in session.query(CatalogItem.id)):
        total += rebuild_search_terms_for_food_id(session, food_id)
    return total


def query_variants(query: str, language: str) -> list[tuple[str, str]]:
    """Return ``(normalized_variant, language)`` pairs for a raw query."""
    base = normalize_text(query)
    if not base:
        return []
    variants: list[tuple[str, str]] = [(base, language)]
    raw = str(query or "").casefold().replace("ё", "е")
    languages = [language.split("-")[0]] if language.split("-")[0] in _TABLES else ["ru", "uk"]
    for variant_language in languages:
        for variant in transliteration_variants(raw, variant_language):
            if variant and variant != base:
                variants.append((variant, variant_language))
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for variant, variant_language in variants:
        if variant in seen:
            continue
        seen.add(variant)
        deduped.append((variant, variant_language))
    return deduped


def score_term(
    term_type: str, normalized: str, variant: str,
    term_language: str, requested_language: str,
) -> tuple[int, int] | None:
    """Return ``(score, term_length)`` for one term/variant pair."""
    if normalized == variant:
        match = "exact"
    elif normalized.startswith(variant):
        match = "prefix"
    elif len(variant) >= MIN_PARTIAL_LENGTH and variant in normalized:
        match = "partial"
    else:
        return None
    tier = _TIERS.get((term_type, match))
    if tier is None:
        return None
    score = tier
    if term_type != "transliteration" and _language_matches(
        term_language, requested_language
    ):
        score += LANGUAGE_BONUS
    return score, len(normalized)


def looks_like_barcode(query: str) -> str | None:
    digits = "".join(ch for ch in str(query or "") if ch.isdigit())
    if 8 <= len(digits) <= 14 and digits == "".join(str(query or "").split()):
        return digits
    return None


def search_catalog(
    session: Session, query: str, *, language: str = "ru", limit: int = 50,
) -> list[SearchHit]:
    """Search curated and community foods; returns ranked hits."""
    variants = query_variants(query, language)
    if not variants:
        return []

    best: dict[tuple[str, int], tuple[int, int]] = {}
    barcode = looks_like_barcode(query)
    if barcode:
        for row in session.query(FoodBarcode).filter(FoodBarcode.barcode == barcode):
            best[("curated", row.food_id)] = (BARCODE_SCORE, 0)
        community = (
            session.query(CommunityFood)
            .filter(CommunityFood.barcode == barcode)
            .first()
        )
        if community is not None:
            best[("community", community.id)] = (BARCODE_SCORE, 0)

    candidate_limit = max(200, limit * 10)
    for variant, _variant_language in variants:
        prefix = f"{variant}%"
        predicates = [FoodSearchTerm.normalized == variant,
                      FoodSearchTerm.normalized.like(prefix)]
        if len(variant) >= MIN_PARTIAL_LENGTH:
            predicates.append(FoodSearchTerm.normalized.like(f"%{variant}%"))
        rows = (
            session.query(
                FoodSearchTerm.food_id, FoodSearchTerm.term_type,
                FoodSearchTerm.language, FoodSearchTerm.normalized,
            )
            .filter(or_(*predicates))
            .limit(candidate_limit)
            .all()
        )
        for food_id, term_type, term_language, normalized in rows:
            scored = score_term(
                term_type, normalized, variant, term_language, language
            )
            if scored is None:
                continue
            key = ("curated", food_id)
            if key not in best or scored[0] > best[key][0]:
                best[key] = scored

        # Community foods use the precomputed normalized_name column.
        community_predicates = [CommunityFood.normalized_name == variant,
                                CommunityFood.normalized_name.like(prefix)]
        if len(variant) >= MIN_PARTIAL_LENGTH:
            community_predicates.append(
                CommunityFood.normalized_name.like(f"%{variant}%")
            )
        community_rows = (
            session.query(CommunityFood.id, CommunityFood.normalized_name)
            .filter(or_(*community_predicates))
            .limit(candidate_limit)
            .all()
        )
        for community_id, normalized_name in community_rows:
            scored = score_term(
                "canonical", normalized_name or "", variant, "und", language
            )
            if scored is None:
                continue
            key = ("community", community_id)
            if key not in best or scored[0] > best[key][0]:
                best[key] = scored

    if not best:
        return []

    curated_ids = [key[1] for key in best if key[0] == "curated"]
    community_ids = [key[1] for key in best if key[0] == "community"]
    curated = {}
    if curated_ids:
        curated = {
            row.id: row
            for row in session.query(CatalogItem)
            .filter(CatalogItem.id.in_(curated_ids), CatalogItem.is_active.is_(True))
            .all()
        }
    community = {}
    if community_ids:
        community = {
            row.id: row
            for row in session.query(CommunityFood)
            .filter(CommunityFood.id.in_(community_ids))
            .all()
        }

    hits: list[SearchHit] = []
    for (kind, item_id), (score, term_length) in best.items():
        item = curated.get(item_id) if kind == "curated" else community.get(item_id)
        if item is None:
            continue
        hits.append(SearchHit(
            kind=kind, item=item, score=score, term_length=term_length,
        ))
    hits.sort(key=lambda hit: (-hit.score, hit.term_length, hit.item.id))
    return hits[:limit]
