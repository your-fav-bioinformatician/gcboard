"""Writeup rules from SRD 5.3 as pure functions: identifier extraction,
snippet selection, mention matching, and grading. No AI model, no I/O.

Everything here is deterministic, so a writeup can be regraded at any time
from its stored answers and identifiers and give the same score.
"""

from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import PurePosixPath
from typing import Sequence

# --------------------------------------------------------------------------
# Identifier extraction
# --------------------------------------------------------------------------

_N = r"([A-Za-z_][A-Za-z0-9_]*)"
_JSN = r"([A-Za-z_$][\w$]*)"

_FAMILY_BY_EXT = {
    ".py": "python",
    ".js": "js", ".jsx": "js", ".ts": "js", ".tsx": "js",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "c", ".hpp": "c", ".cc": "c", ".cs": "c", ".java": "c", ".dart": "c",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".swift": "swift",
    ".lua": "lua",
}

_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    family: [re.compile(p, re.M) for p in pats]
    for family, pats in {
        "python": [rf"^[ \t]*(?:async[ \t]+)?def[ \t]+{_N}", rf"^[ \t]*class[ \t]+{_N}"],
        "js": [
            rf"\bfunction\b[ \t]*\*?[ \t]*{_JSN}",
            rf"\bclass[ \t]+{_JSN}",
            rf"\b(?:const|let|var)[ \t]+{_JSN}[ \t]*=",
            # method definitions: name(args) { ... }, optionally async/static/get/set, optional TS return type
            rf"^[ \t]*(?:(?:async|static|public|private|protected|readonly|get|set)[ \t]+)*{_JSN}[ \t]*\([^)\n]*\)[ \t]*(?::[^{{\n]*)?\{{",
        ],
        "go": [rf"^func[ \t]+(?:\([^)]*\)[ \t]*)?{_N}", rf"^[ \t]*type[ \t]+{_N}"],
        "rust": [rf"\bfn[ \t]+{_N}", rf"\b(?:struct|enum|trait)[ \t]+{_N}"],
        "c": [
            # function definitions: return type(s), name, (params), then { or end of line (brace on next line)
            rf"^[ \t]*(?:[\w:<>,*&\[\]]+[ \t]+)+[*&]*{_N}[ \t]*\([^;()]*\)[ \t]*(?:const[ \t]*)?(?:\{{|$)",
            rf"\b(?:class|struct|enum|interface)[ \t]+{_N}",
        ],
        "ruby": [rf"^[ \t]*def[ \t]+(?:self\.)?{_N}", rf"^[ \t]*(?:class|module)[ \t]+{_N}"],
        "php": [rf"\bfunction[ \t]+&?{_N}", rf"\b(?:class|interface|trait)[ \t]+{_N}"],
        "kotlin": [rf"\bfun[ \t]+(?:<[^>]*>[ \t]*)?(?:[\w.]+\.)?{_N}", rf"\b(?:class|object|interface)[ \t]+{_N}"],
        "swift": [rf"\bfunc[ \t]+{_N}", rf"\b(?:class|struct|enum|protocol)[ \t]+{_N}"],
        "lua": [rf"\bfunction[ \t]+(?:[\w.]+[.:])?{_N}", rf"\blocal[ \t]+{_N}[ \t]*=[ \t]*function\b"],
    }.items()
}

KEYWORDS = frozenset(
    """
    and as assert async await break case catch char class const continue def default del delete do double elif
    else enum except export extends false final finally float for foreach from func function global goto if impl
    implements import in instanceof int interface is lambda let long loop match mod module mut namespace new nil
    none not null object or override package pass private protected pub public raise return self short signed
    sizeof static struct super switch template this throw throws trait true try type typedef typeof union unsigned
    use using var virtual void volatile where while with yield string bool boolean byte println printf print
    """.split()
)
DENYLIST = frozenset("main init self this test data value args temp tmp item result run get set new".split())


def _family(path: str) -> str | None:
    return _FAMILY_BY_EXT.get(PurePosixPath(path).suffix.lower())


def _keep(name: str) -> bool:
    """Drop short names, keywords, the denylist, and dunders like __init__."""
    core = name.strip("_").lower()
    return len(core) >= 3 and not name.startswith("__") and core not in KEYWORDS and core not in DENYLIST


def definitions(path: str, text: str) -> list[tuple[int, str]]:
    """(0-based line, name) of every definition the patterns find, kept or not."""
    family = _family(path)
    if family is None:
        return []
    out = []
    for pat in _PATTERNS[family]:
        for m in pat.finditer(text):
            out.append((text.count("\n", 0, m.start(1)), m.group(1)))
    return sorted(set(out))


def extract_identifiers(sources: dict[str, bytes]) -> list[str]:
    """Repo identifiers: names defined in source files plus source file names
    without extensions, minus short names, keywords, and the denylist.
    Deduplicated case-insensitively; sorted for determinism."""
    found: dict[str, str] = {}
    for path in sorted(sources):
        text = sources[path].decode("utf-8", errors="replace")
        names = [n for _, n in definitions(path, text)] + [PurePosixPath(path).stem]
        for n in names:
            if _keep(n):
                found.setdefault(n.lower(), n)
    return sorted(found.values(), key=str.lower)


# --------------------------------------------------------------------------
# Mentions
# --------------------------------------------------------------------------


def split_identifier(name: str) -> list[str]:
    """check_winner / checkWinner / CheckWinner -> ["check", "winner"]."""
    return [p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)]


_pattern_cache: dict[str, re.Pattern[str]] = {}


def _mention_pattern(name: str) -> re.Pattern[str]:
    if name not in _pattern_cache:
        alts = [r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"]
        parts = split_identifier(name)
        if len(parts) >= 2:
            alts.append(r"\b" + r"\s+".join(map(re.escape, parts)) + r"\b")
        _pattern_cache[name] = re.compile("|".join(alts), re.I)
    return _pattern_cache[name]


def mentioned(name: str, text: str) -> bool:
    """Verbatim, or as its split words in a row ("check winner")."""
    return _mention_pattern(name).search(text) is not None


def mentions(names: Sequence[str], text: str) -> list[str]:
    return [n for n in names if mentioned(n, text)]


# --------------------------------------------------------------------------
# Snippet selection
# --------------------------------------------------------------------------

SNIPPET_LINES = 15
SNIPPET_MIN_NONBLANK = 10
SNIPPET_MIN_IDENTIFIERS = 2
SNIPPET_MIN_FILE_LINES = 30


@dataclass(frozen=True)
class Snippet:
    path: str
    start: int  # 1-based, inclusive
    end: int  # 1-based, inclusive
    text: str
    identifiers: list[str]  # repo identifiers appearing in the snippet


def _nonblank(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if line.strip())


def select_snippet(sources: dict[str, bytes], identifiers: Sequence[str], seed: str) -> Snippet:
    """SRD 5.3. Pick a file with >= 30 non-blank lines, weighted by its
    non-blank line count, then a 15-line window that starts at a definition
    and has >= 10 non-blank lines and >= 2 repo identifiers. If no window
    qualifies, any window with >= 10 non-blank lines.

    For tiny repos (no file reaches 30 lines) the size bar is lowered to
    10 non-blank lines, then to any file, so a snippet always exists.
    `seed` must be "{entry_id}:{frozen_sha}" so the choice is reproducible.
    """
    if not sources:
        raise ValueError("no source files")
    rng = random.Random(seed)
    texts = {p: sources[p].decode("utf-8", errors="replace") for p in sorted(sources)}
    lines = {p: t.splitlines() for p, t in texts.items()}
    counts = {p: _nonblank(ls) for p, ls in lines.items()}
    for bar in (SNIPPET_MIN_FILE_LINES, SNIPPET_MIN_NONBLANK, 1):
        files = [p for p in texts if counts[p] >= bar]
        if files:
            break
    else:
        files = list(texts)
    path = rng.choices(files, weights=[max(1, counts[p]) for p in files])[0]
    file_lines = lines[path]

    def window(i: int) -> list[str]:
        return file_lines[i : i + SNIPPET_LINES]

    def ids_in(i: int) -> list[str]:
        return mentions_verbatim(identifiers, "\n".join(window(i)))

    # Windows are always a full 15 lines, unless the file itself is shorter.
    last_start = max(0, len(file_lines) - SNIPPET_LINES)
    starts = sorted({line for line, _ in definitions(path, texts[path]) if line <= last_start})
    good = [
        i for i in starts
        if _nonblank(window(i)) >= SNIPPET_MIN_NONBLANK and len(ids_in(i)) >= SNIPPET_MIN_IDENTIFIERS
    ]
    if not good:
        good = [i for i in range(last_start + 1) if _nonblank(window(i)) >= SNIPPET_MIN_NONBLANK]
    if not good:
        good = [0]
    i = rng.choice(good)
    w = window(i)
    return Snippet(path=path, start=i + 1, end=i + max(1, len(w)), text="\n".join(w), identifiers=ids_in(i))


def mentions_verbatim(names: Sequence[str], text: str) -> list[str]:
    """Identifiers that literally appear in code (no split-word matching)."""
    return [n for n in names if re.search(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"(?![A-Za-z0-9_])", text)]


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------

_STRIP = "\"'`.,;:!?()[]{}<>*-"


def words(text: str) -> list[str]:
    """SRD 5.3: a word is a whitespace-separated token with at least one letter."""
    return [t for t in text.split() if any(c.isalpha() for c in t)]


def _norm(token: str) -> str:
    return token.strip(_STRIP).lower()


def _word_set(text: str) -> set[str]:
    return {_norm(w) for w in words(text)} - {""}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _ttr(text: str, window: int) -> float:
    ws = [_norm(w) for w in words(text)[:window]]
    return len(set(ws)) / len(ws) if ws else 0.0


_CODEY = re.compile(r"[a-z][A-Z]|_[A-Za-z]|[A-Za-z]_|\(\)$|^[A-Za-z_]+\.[A-Za-z_]+\(?\)?$")


def is_identifier_token(token: str, repo_identifiers_lower: set[str]) -> bool:
    """A word that is a repo identifier, or looks like code
    (snake_case, camelCase, call(), obj.method)."""
    core = token.strip("\"'`.,;:!?[]{}<>")
    return core.strip("()").lower() in repo_identifiers_lower or bool(_CODEY.search(core))


def sentences(text: str) -> list[list[str]]:
    """Split on . ! ? followed by whitespace, and on blank lines."""
    out = []
    for para in re.split(r"\n[ \t]*\n", text):
        for s in re.split(r"(?<=[.!?])\s+", para.strip()):
            ws = words(s)
            if ws:
                out.append(ws)
    return out


@dataclass(frozen=True)
class GradingRules:
    min_words: tuple[int, int, int, int] = (80, 100, 60, 60)
    gate_min_words: int = 20
    gate_min_total: int = 150
    gate_max_jaccard: float = 0.8
    snippet_ids_target: int = 3
    other_ids_target: int = 6
    ttr_window: int = 100
    ttr_target: float = 0.55
    density_full: float = 0.20
    density_zero: float = 0.45
    sentence_min: int = 6
    sentence_max: int = 35
    sentence_hard_max: int = 80


@dataclass
class Breakdown:
    """Writeup score and how it was reached; stored and shown to the member."""

    total: float = 0.0
    gate_passed: bool = True
    gate_reason: str | None = None
    completeness: float = 0.0
    grounding: float = 0.0
    change_relevance: float = 0.0
    variety: float = 0.0
    identifier_density: float = 0.0
    sentences: float = 0.0
    word_counts: list[int] = field(default_factory=list)
    snippet_ids_mentioned: list[str] = field(default_factory=list)
    other_ids_mentioned: list[str] = field(default_factory=list)
    keyword_groups_matched: list[bool] = field(default_factory=list)
    identifier_ratio: float = 0.0
    avg_ttr: float = 0.0
    avg_sentence_words: float = 0.0
    longest_sentence_words: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def group_matched(group: Sequence[str], text: str) -> bool:
    """A keyword group matches if any term appears at the start of a word
    ("undo" matches "undoing") or as a phrase ("list of moves")."""
    for term in group:
        parts = term.lower().split()
        if parts and re.search(r"(?<![A-Za-z0-9])" + r"\s+".join(map(re.escape, parts)), text, re.I):
            return True
    return False


def _r(x: float) -> float:
    return round(x, 2)


def grade(
    answers: Sequence[str],
    snippet_identifiers: Sequence[str],
    repo_identifiers: Sequence[str],
    keyword_groups: Sequence[Sequence[str]],
    rules: GradingRules = GradingRules(),
) -> Breakdown:
    """Score a writeup 0-100 (SRD 5.3)."""
    if len(answers) != 4:
        raise ValueError("a writeup has exactly four answers")
    b = Breakdown()
    counts = [len(words(a)) for a in answers]
    b.word_counts = counts

    # Gate
    sets = [_word_set(a) for a in answers]
    if any(c < rules.gate_min_words for c in counts):
        b.gate_passed, b.gate_reason = False, f"an answer has fewer than {rules.gate_min_words} words"
    elif sum(counts) < rules.gate_min_total:
        b.gate_passed, b.gate_reason = False, f"fewer than {rules.gate_min_total} words in total"
    else:
        for (i, a), (j, c) in combinations(enumerate(sets), 2):
            if jaccard(a, c) > rules.gate_max_jaccard:
                b.gate_passed, b.gate_reason = False, f"answers {i + 1} and {j + 1} are nearly the same"
                break

    # Completeness (20)
    b.completeness = _r(20 * sum(min(1, c / m) for c, m in zip(counts, rules.min_words)) / 4)

    # Grounding (30)
    snippet_set = {s.lower() for s in snippet_identifiers}
    b.snippet_ids_mentioned = mentions(list(snippet_identifiers), answers[0])
    others = [i for i in repo_identifiers if i.lower() not in snippet_set]
    later = "\n".join(answers[1:])
    b.other_ids_mentioned = mentions(others, later)
    snippet_div = rules.snippet_ids_target if len(snippet_identifiers) >= rules.snippet_ids_target else max(
        1, len(snippet_identifiers)
    )
    b.grounding = _r(
        15 * min(1, len(b.snippet_ids_mentioned) / snippet_div)
        + 15 * min(1, len(b.other_ids_mentioned) / rules.other_ids_target)
    )

    # Change relevance (25)
    b.keyword_groups_matched = [group_matched(g, answers[2]) for g in keyword_groups]
    if keyword_groups:
        b.change_relevance = _r(25 * sum(b.keyword_groups_matched) / len(keyword_groups))

    # Writing quality (25)
    b.avg_ttr = _r(sum(_ttr(a, rules.ttr_window) for a in answers) / 4)
    b.variety = _r(10 * min(1, b.avg_ttr / rules.ttr_target))

    all_words = [w for a in answers for w in words(a)]
    ids_lower = {i.lower() for i in repo_identifiers}
    id_tokens = sum(1 for w in all_words if is_identifier_token(w, ids_lower))
    b.identifier_ratio = _r(id_tokens / len(all_words)) if all_words else 0.0
    ratio = id_tokens / len(all_words) if all_words else 1.0
    if ratio <= rules.density_full:
        b.identifier_density = 10.0
    elif ratio >= rules.density_zero:
        b.identifier_density = 0.0
    else:
        b.identifier_density = _r(10 * (rules.density_zero - ratio) / (rules.density_zero - rules.density_full))

    sents = [s for a in answers for s in sentences(a)]
    if sents:
        b.avg_sentence_words = _r(sum(map(len, sents)) / len(sents))
        b.longest_sentence_words = max(map(len, sents))
        ok = rules.sentence_min <= b.avg_sentence_words <= rules.sentence_max
        b.sentences = 5.0 if ok and b.longest_sentence_words <= rules.sentence_hard_max else 0.0

    if b.gate_passed:
        b.total = round(
            b.completeness + b.grounding + b.change_relevance + b.variety + b.identifier_density + b.sentences, 1
        )
    return b
