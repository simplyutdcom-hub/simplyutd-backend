"""Broad, typo-tolerant search over the news corpus.

The navbar search box is the only door into the archive, so it has to cope with
whatever a reader types: one word, a few keywords in any order, a quoted phrase,
a publisher, a citation, a scoreline, or a misspelling. A plain regex substring
test does none of that well — it only ever finds the *exact* string, so
``bruno rashford`` misses an article that says "Rashford" in the headline and
"Bruno" three paragraphs later, and a single wrong letter returns nothing.

This module replaces that with a small, explainable search engine:

1. **Parse.** The query is split into clauses: free terms, quoted phrases,
   ``field:value`` scopes, ``-exclusions`` and ``term*`` prefixes. Punctuation
   and money/step markers are normalised so ``€85m``, ``[1]`` and ``“Old
   Trafford”`` behave the way a reader expects.
2. **Score.** Every remaining clause is matched against each searchable field
   with a weight table (a headline hit is worth much more than a passing mention
   in the summary) plus bonuses for covering every clause, for keeping the terms
   close together, and for naming them in the title.
3. **Broaden, in two stages.** If strict matching (every clause matched) finds
   nothing, the engine falls back to any-clause matching, and if that is still
   empty it retries once with bounded edit-distance substitutions. Both
   widenings are reported back to the caller so the UI can say so.

Club relevance deliberately plays no part here — this module knows about text
only. See :mod:`app.services.feed_rank` for the United-first ranking, which is
layered on top as a tiebreaker by the search router.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from pymongo.database import Database

from .. import db as db_module

# --- Field weights ----------------------------------------------------------- #

#: How much a clause hit is worth per field. A headline hit should comfortably
#: beat a body mention, and a tag/source hit is a strong topical signal.
FIELD_WEIGHTS: dict[str, float] = {
    "title": 6.0,
    "tags": 5.0,
    "slug": 4.0,
    "source": 4.0,
    "category": 3.0,
    "author": 3.0,
    "summary": 2.0,
    "url": 1.5,
    "date": 1.2,
    "content": 1.0,
}

#: Where the value of a ``field:`` scope may live in the stored document.
FIELD_SOURCES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "tags": ("tags",),
    "slug": ("slug",),
    "source": ("source",),
    "category": ("category",),
    "author": ("author",),
    "summary": ("summary",),
    "url": ("source_url", "url"),
    # Lets "united 2025" or "year:2024" hit, matching how news is actually cited.
    "date": ("published_at",),
    "content": ("content", "summary"),
}

#: Reader-facing aliases for ``field:`` scopes.
FIELD_ALIASES: dict[str, str] = {
    "title": "title",
    "headline": "title",
    "head": "title",
    "tag": "tags",
    "tags": "tags",
    "topic": "tags",
    "source": "source",
    "publisher": "source",
    "pub": "source",
    "via": "source",
    "cite": "source",
    "citation": "source",
    "ref": "source",
    "category": "category",
    "cat": "category",
    "section": "category",
    "author": "author",
    "by": "author",
    "writer": "author",
    "slug": "slug",
    "url": "url",
    "link": "url",
    "domain": "url",
    "site": "url",
    "summary": "summary",
    "excerpt": "summary",
    "date": "date",
    "year": "date",
    "yr": "date",
    "month": "date",
    "published": "date",
    "content": "content",
    "body": "content",
}

#: Words with no signal of their own. They may still match, but they never
#: *require* a match, so "the old trafford" behaves like "old trafford".
STOPWORDS: frozenset[str] = frozenset(
    """
    a about after all also an and any are as at be been but by can could did do does
    for from get had has have he her him his how i if in into is it its just like me
    more most my no not of on one only or other our out over said she should so some
    such than that the their them then there these they this to too up us very was we
    were what when where which who why will with would you your
    """.split()
)

#: A term hit found only as a word-prefix is discounted (searching "ras" should
#: find "Rashford", but not outrank a document that actually says "ras").
PREFIX_WEIGHT = 0.7
#: A phrase is a much stronger claim than the sum of its words.
PHRASE_BONUS = 2.2
#: Bonuses for matching shape rather than mere presence.
COVERAGE_BONUS = 14.0
TITLE_COVERAGE_BONUS = 10.0
PROXIMITY_BONUS = 9.0
PROXIMITY_WINDOW = 14

#: Bounded fuzzy matching. Shorter words allow fewer edits so "united" cannot
#: drift onto "united-states"-style noise, and nothing at all is attempted for
#: very short terms or terms that share no first letter.
FUZZY_MIN_LENGTH = 5
FUZZY_MAX_EDITS_SHORT = 1
FUZZY_MAX_EDITS_LONG = 2
FUZZY_LONG_LENGTH = 8
FUZZY_SCORE_FACTOR = 0.55

DEFAULT_POOL_SIZE = 2000
DEFAULT_MAX_PER_SOURCE = 4

_WORD_RE = re.compile(r"[a-z0-9]+")
_QUOTES = {'"': '"', "“": "”", "‘": "’", "'": "'"}
_SEPARATORS = frozenset({",", ";", "|", "(", ")", "[", "]", "{", "}"})
#: The boolean words a reader might type out of habit.
_OR_WORDS = frozenset({"or", "any", "either"})


def _fold(text: str) -> str:
    """Lower-case and strip accents, so "Rashford" and "ráshford" collide."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def _phrase_text(text: str) -> str:
    """Normalise to lowercase alphanumerics separated by single spaces."""
    folded = re.sub(r"[^a-z0-9\s]+", " ", _fold(text))
    return re.sub(r"\s+", " ", folded).strip()


def normalise(text: str) -> str:
    """Public helper for the same normalisation used during matching."""
    return _phrase_text(text)


# --- Query parsing ----------------------------------------------------------- #


@dataclass(frozen=True)
class Clause:
    """One unit of the query: a word, a phrase, or a scoped/excluded version."""

    text: str
    phrase: bool = False
    field: str | None = None
    prefix: bool = False
    negate: bool = False
    required: bool = True

    @property
    def label(self) -> str:
        """How the clause was written, for echoing back to the reader."""
        body = f'"{self.text}"' if self.phrase else self.text
        if self.field:
            body = f"{self.field}:{body}"
        return f"-{body}" if self.negate else body


@dataclass(frozen=True)
class ParsedQuery:
    """The query, taken apart."""

    raw: str
    clauses: tuple[Clause, ...]

    @property
    def matched(self) -> tuple[Clause, ...]:
        """Clauses a document may hit (everything that is not excluded)."""
        return tuple(c for c in self.clauses if not c.negate)

    @property
    def strict(self) -> tuple[Clause, ...]:
        """Clauses a document must hit for the query to count as satisfied.

        Stopwords are excluded here, so "the old trafford" demands only "old"
        and "trafford" — the article does not have to contain the definite
        article to be a match.
        """
        return tuple(c for c in self.clauses if not c.negate and c.required)

    @property
    def terms(self) -> tuple[Clause, ...]:
        return tuple(c for c in self.matched if not c.phrase and not c.field)

    @property
    def phrases(self) -> tuple[Clause, ...]:
        return tuple(c for c in self.matched if c.phrase and not c.field)

    @property
    def scoped(self) -> dict[str, tuple[Clause, ...]]:
        fields: dict[str, list[Clause]] = {}
        for clause in self.matched:
            if clause.field:
                fields.setdefault(clause.field, []).append(clause)
        return {name: tuple(items) for name, items in fields.items()}

    @property
    def exclusions(self) -> tuple[Clause, ...]:
        return tuple(c for c in self.clauses if c.negate)

    def is_empty(self) -> bool:
        return not self.matched

    def as_dict(self) -> dict[str, Any]:
        return {
            "terms": [c.text for c in self.terms],
            "phrases": [c.text for c in self.phrases],
            "fields": {name: [c.text for c in items] for name, items in self.scoped.items()},
            "excluded": [c.text for c in self.exclusions],
        }


def _split_clauses(raw: str) -> list[tuple[str, bool]]:
    """Split the raw query into ``(text, negated)`` pairs, honouring quotes."""
    pairs: list[tuple[str, bool]] = []
    index, length = 0, len(raw)
    while index < length:
        char = raw[index]
        if char.isspace() or char in _SEPARATORS:
            index += 1
            continue
        negated = False
        if char == "-" and index + 1 < length and not raw[index + 1].isspace():
            negated = True
            index += 1
            char = raw[index]
        if char in _QUOTES:
            closing = _QUOTES[char]
            end = raw.find(closing, index + 1)
            if end == -1:
                # Unbalanced quote: treat the remainder as the phrase.
                pairs.append((raw[index + 1 :], negated))
                break
            pairs.append((raw[index + 1 : end], negated))
            index = end + 1
            continue
        end = index
        while end < length and not raw[end].isspace() and raw[end] not in _SEPARATORS:
            # A quoted scope value keeps its spaces: source:"bbc sport".
            if raw[end] == ":" and end + 1 < length and raw[end + 1] in _QUOTES:
                closing = _QUOTES[raw[end + 1]]
                stop = raw.find(closing, end + 2)
                end = length if stop == -1 else stop + 1
                break
            end += 1
        pairs.append((raw[index:end], negated))
        index = end
    return pairs


def _split_scope(token: str) -> tuple[str | None, str]:
    """Split a ``field:value`` token, ignoring anything that is not a scope."""
    head, separator, tail = token.partition(":")
    if not separator or not tail.strip():
        return None, token
    # A URL-ish token ("https://…") is a value, not a scope.
    if token.strip().lower().startswith(("http:", "https:", "www.")):
        return None, token
    field = FIELD_ALIASES.get(head.strip().lower())
    if not field:
        return None, token
    return field, tail


def parse_query(raw: str) -> ParsedQuery:
    """Turn a reader's search string into clauses."""
    clauses: list[Clause] = []
    for text, negated in _split_clauses(raw or ""):
        field, value = _split_scope(text)
        token = value.strip()
        prefix = token.endswith("*") and len(token) > 1
        if prefix:
            token = token[:-1]
        normalised = _phrase_text(token)
        if not normalised:
            continue
        # "4-0" and "man-united" are sequences, not single words.
        as_phrase = " " in normalised
        required = not (not negated and not as_phrase and normalised in STOPWORDS)
        clauses.append(
            Clause(
                text=normalised,
                phrase=as_phrase,
                field=field,
                prefix=prefix,
                negate=negated,
                required=required,
            )
        )
    return ParsedQuery(raw=raw or "", clauses=tuple(clauses))


# --- Document normalisation -------------------------------------------------- #


def _field_value(doc: dict[str, Any], keys: Iterable[str]) -> str:
    parts: list[str] = []
    for key in keys:
        value = doc.get(key)
        if isinstance(value, (list, tuple, set)):
            parts.extend(_phrase_text(item) for item in value)
        elif value:
            parts.append(_phrase_text(value))
    return " ".join(part for part in parts if part)


def document_text(doc: dict[str, Any]) -> dict[str, str]:
    """Every searchable field of ``doc``, normalised for matching."""
    texts = {field: _field_value(doc, keys) for field, keys in FIELD_SOURCES.items()}
    # A date is cited as "2024", "2024-09" or "September 2024"; expose the
    # year/month slices so any of those forms can hit.
    published = doc.get("published_at")
    if hasattr(published, "year"):
        texts["date"] = f"{texts['date']} {published.year} {published.year}-{published.month:02d}"
    return texts


# --- Matching ---------------------------------------------------------------- #


@dataclass
class Match:
    """The outcome of scoring one document against one query."""

    score: float
    matched: tuple[str, ...]
    fields: tuple[str, ...]
    coverage: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "matched": list(self.matched),
            "fields": list(self.fields),
            "coverage": round(self.coverage, 2),
        }


def _contains_sequence(tokens: list[str], phrase: list[str]) -> bool:
    """True when ``phrase`` appears in ``tokens`` as a contiguous run."""
    size = len(phrase)
    if not size or size > len(tokens):
        return False
    if size == 1:
        return phrase[0] in tokens
    return any(tokens[i : i + size] == phrase for i in range(len(tokens) - size + 1))


def _clause_field_score(text: str, tokens: list[str], clause: Clause, weight: float) -> float:
    """Score one clause against one normalised field."""
    if not text:
        return 0.0
    if clause.phrase:
        if _contains_sequence(tokens, clause.text.split()):
            return weight * PHRASE_BONUS
        return 0.0
    if clause.prefix:
        hits = sum(1 for word in tokens if word.startswith(clause.text))
        return weight * (1.0 + 0.3 * math.log1p(hits)) if hits else 0.0
    exact = sum(1 for word in tokens if word == clause.text)
    if exact:
        return weight * (1.0 + 0.3 * math.log1p(exact))
    # Broaden within the term: "rashford" should still find "rashfords", and
    # "ras*"-style partial words should find something rather than nothing.
    if len(clause.text) >= 3:
        partial = sum(1 for word in tokens if word.startswith(clause.text))
        if partial:
            return weight * PREFIX_WEIGHT
    return 0.0


def _clause_hits(texts: dict[str, str], tokens: dict[str, list[str]], clause: Clause) -> float:
    """Total weight of one clause across the fields it is allowed to match."""
    fields = (clause.field,) if clause.field else tuple(FIELD_WEIGHTS)
    total = 0.0
    for field in fields:
        total += _clause_field_score(
            texts.get(field, ""), tokens.get(field, []), clause, FIELD_WEIGHTS[field]
        )
    return total


def _min_window(tokens: list[str], terms: list[str], window: int) -> bool:
    """True when every term appears inside a window of ``window`` tokens."""
    wanted = set(terms)
    positions = sorted(
        (index, token) for index, token in enumerate(tokens) if token in wanted
    )
    if len(wanted) < 2:
        return False
    counts: dict[str, int] = {}
    have = 0
    left = 0
    best = math.inf
    for _, (index, token) in enumerate(positions):
        counts[token] = counts.get(token, 0) + 1
        if counts[token] == 1:
            have += 1
        while have == len(wanted):
            best = min(best, index - positions[left][0])
            left_token = positions[left][1]
            counts[left_token] -= 1
            if counts[left_token] == 0:
                have -= 1
            left += 1
    return best <= window


def score_document(
    doc: dict[str, Any],
    parsed: ParsedQuery,
    *,
    require_all: bool = True,
    factor: float = 1.0,
    boost: Callable[[dict[str, Any]], float] | None = None,
) -> Match | None:
    """Score ``doc`` against ``parsed``, or ``None`` when it does not match."""
    if parsed.is_empty():
        return None
    texts = document_text(doc)
    tokens = {field: text.split() for field, text in texts.items()}

    for clause in parsed.exclusions:
        if _clause_hits(texts, tokens, clause) > 0:
            return None

    required = parsed.matched
    strict = {id(clause) for clause in parsed.strict}
    matched: list[str] = []
    strict_hits = 0
    fields: set[str] = set()
    score = 0.0
    for clause in required:
        hit = _clause_hits(texts, tokens, clause)
        if hit <= 0:
            continue
        score += hit
        matched.append(clause.label)
        if id(clause) in strict:
            strict_hits += 1
        for field in (clause.field,) if clause.field else FIELD_WEIGHTS:
            if _clause_field_score(
                texts.get(field, ""), tokens.get(field, []), clause, FIELD_WEIGHTS[field]
            ) > 0:
                fields.add(field)

    if not matched:
        return None
    coverage = strict_hits / len(parsed.strict) if parsed.strict else 1.0
    if require_all and strict_hits < len(parsed.strict):
        return None

    score += COVERAGE_BONUS * coverage
    free_terms = [c.text for c in parsed.strict if not c.phrase and not c.field]
    if len(free_terms) > 1 and coverage >= 1.0:
        title_tokens = tokens["title"]
        if all(term in title_tokens for term in free_terms):
            score += TITLE_COVERAGE_BONUS
        combined = f"{texts['title']} {texts['summary']} {texts['content']}".split()
        if _min_window(combined, free_terms, PROXIMITY_WINDOW):
            score += PROXIMITY_BONUS

    score *= factor
    if boost:
        score += boost(doc)
    return Match(score=score, matched=tuple(matched), fields=tuple(sorted(fields)), coverage=coverage)


def match_score(doc: dict[str, Any], query: str) -> float:
    """Convenience wrapper: the raw match score for a query string."""
    parsed = parse_query(query)
    match = score_document(doc, parsed, require_all=False)
    return match.score if match else 0.0


# --- Fuzzy fallback ---------------------------------------------------------- #


def _edit_distance(left: str, right: str, limit: int) -> int:
    """Levenshtein distance, abandoning as soon as ``limit`` is exceeded."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (char_left != char_right),
                )
            )
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _edit_limit(term: str) -> int:
    if len(term) < FUZZY_MIN_LENGTH:
        return 0
    return FUZZY_MAX_EDITS_LONG if len(term) >= FUZZY_LONG_LENGTH else FUZZY_MAX_EDITS_SHORT


def vocabulary(pool: Iterable[dict[str, Any]]) -> set[str]:
    """The words a fuzzy correction is allowed to land on."""
    words: set[str] = set()
    for doc in pool:
        for field in ("title", "tags", "source", "category", "summary"):
            words.update(_field_value(doc, FIELD_SOURCES[field]).split())
    return words


def _closest_word(term: str, words: set[str]) -> str | None:
    limit = _edit_limit(term)
    if not limit:
        return None
    best: tuple[int, int, str] | None = None
    for word in words:
        if not word or word[0] != term[0]:
            continue
        if abs(len(word) - len(term)) > limit:
            continue
        distance = _edit_distance(term, word, limit)
        if distance > limit:
            continue
        candidate = (distance, -len(word), word)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else None


def relax_to_fuzzy(parsed: ParsedQuery, words: set[str]) -> tuple[ParsedQuery, bool]:
    """Replace each free term with its closest corpus word, if one exists."""
    changed = False
    clauses: list[Clause] = []
    for clause in parsed.clauses:
        if clause.negate or clause.phrase or clause.field:
            clauses.append(clause)
            continue
        replacement = _closest_word(clause.text, words)
        if replacement and replacement != clause.text:
            clauses.append(Clause(text=replacement, prefix=clause.prefix))
            changed = True
        else:
            clauses.append(clause)
    return ParsedQuery(raw=parsed.raw, clauses=tuple(clauses)), changed


# --- Candidate prefilter ----------------------------------------------------- #


def _regex_for(clause: Clause) -> str:
    parts = [re.escape(part) for part in clause.text.split() if part]
    if not parts:
        return ""
    if clause.phrase and len(parts) > 1:
        return r"\s+".join(parts)
    return parts[0]


def prefilter(parsed: ParsedQuery) -> dict[str, Any]:
    """A coarse Mongo criteria dict for ``parsed``.

    Deliberately **broad**: every clause contributes an OR branch across the
    fields it could match, so a document that only satisfies part of the query
    still reaches the scorer. Ranking is where strictness lives, because doing
    it in Mongo would lose exactly the multi-word matches this engine exists to
    find.
    """
    branches: list[dict[str, Any]] = []
    for clause in parsed.matched:
        pattern = _regex_for(clause)
        if not pattern:
            continue
        fields = (clause.field,) if clause.field else tuple(FIELD_WEIGHTS)
        for field in fields:
            for key in FIELD_SOURCES.get(field, (field,)):
                branches.append({key: {"$regex": pattern, "$options": "i"}})

    excludes: list[dict[str, Any]] = []
    for clause in parsed.exclusions:
        pattern = _regex_for(clause)
        if not pattern:
            continue
        fields = (clause.field,) if clause.field else ("title", "summary", "source", "tags")
        excludes.append({"$or": [{key: {"$regex": pattern, "$options": "i"}} for key in fields]})

    criteria: dict[str, Any] = {}
    if branches:
        criteria["$or"] = branches
    if excludes:
        criteria["$nor"] = excludes
    return criteria


# --- Search ------------------------------------------------------------------ #


@dataclass
class SearchOutcome:
    """Ranked page plus an explanation of how hard the engine had to look."""

    rows: list[tuple[dict[str, Any], Match]]
    total: int
    parsed: ParsedQuery
    broadened: bool = False
    fuzzy: bool = False

    def as_meta(self) -> dict[str, Any]:
        return {
            "parsed": self.parsed.as_dict(),
            "broadened": self.broadened,
            "fuzzy": self.fuzzy,
        }


def _load_pool(
    database: Database,
    *,
    pool_size: int,
    status: str = "Published",
    category: str | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    criteria: dict[str, Any] = {}
    if status and status.lower() != "all":
        criteria["status"] = status
    if category and category.lower() not in {"all", ""}:
        criteria["category"] = category
    if source:
        criteria["source"] = source
    cursor = (
        database[db_module.NEWS]
        .find(criteria, {"_id": 0})
        .sort("published_at", -1)
        .limit(max(1, pool_size))
    )
    return list(cursor)


def _collect(
    pool: list[dict[str, Any]],
    parsed: ParsedQuery,
    *,
    require_all: bool,
    factor: float = 1.0,
    boost: Callable[[dict[str, Any]], float] | None = None,
) -> list[tuple[dict[str, Any], Match]]:
    rows: list[tuple[dict[str, Any], Match]] = []
    for doc in pool:
        match = score_document(doc, parsed, require_all=require_all, factor=factor, boost=boost)
        if match:
            rows.append((doc, match))
    rows.sort(
        key=lambda row: (
            -row[1].score,
            -len(row[1].matched),
            str(row[0].get("published_at") or ""),
        )
    )
    return rows


def _diversify(
    rows: list[tuple[dict[str, Any], Match]], *, max_per_source: int
) -> list[tuple[dict[str, Any], Match]]:
    """Stop one prolific wire owning a whole page of results."""
    if max_per_source <= 0:
        return rows
    seen: dict[str, int] = {}
    kept: list[tuple[dict[str, Any], Match]] = []
    overflow: list[tuple[dict[str, Any], Match]] = []
    for doc, match in rows:
        key = str(doc.get("source") or "unknown").lower()
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > max_per_source:
            overflow.append((doc, match))
        else:
            kept.append((doc, match))
    return kept + overflow


def search(
    database: Database,
    raw: str,
    *,
    limit: int = 20,
    skip: int = 0,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
    status: str = "Published",
    boost: Callable[[dict[str, Any]], float] | None = None,
) -> SearchOutcome:
    """Search the corpus, widening the interpretation only when needed."""
    parsed = parse_query(raw)
    if parsed.is_empty():
        return SearchOutcome(rows=[], total=0, parsed=parsed)

    pool = _load_pool(database, pool_size=pool_size, status=status)

    rows = _collect(pool, parsed, require_all=True, boost=boost)
    broadened = False
    fuzzy = False
    if not rows:
        rows = _collect(pool, parsed, require_all=False, boost=boost)
        broadened = bool(rows)
    if not rows:
        relaxed, changed = relax_to_fuzzy(parsed, vocabulary(pool))
        if changed:
            rows = _collect(
                pool, relaxed, require_all=False, factor=FUZZY_SCORE_FACTOR, boost=boost
            )
            fuzzy = bool(rows)

    rows = _diversify(rows, max_per_source=max_per_source)
    total = len(rows)
    page = rows[skip : skip + limit]
    return SearchOutcome(rows=page, total=total, parsed=parsed, broadened=broadened, fuzzy=fuzzy)
