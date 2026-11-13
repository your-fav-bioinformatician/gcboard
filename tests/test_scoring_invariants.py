"""NFR-MAINT-1 / design principle 2: properties every score must have,
checked over many seeded random inputs, plus the last grading edge cases."""

import random

import pytest

from gcboard.server.scoring import (
    BoardEntry,
    EntryInput,
    Snapshot,
    SolvedAttempt,
    history_factor,
    rank_board,
    score_project,
    score_task,
    streaks,
)
from gcboard.server.textrules import grade, select_snippet

from .sample_writeup import ANSWERS, KEYWORD_GROUPS
from .test_textrules import IDS, SNIPPET_IDS

TRIALS = 300


def random_attempts(rng: random.Random, n: int) -> list[SolvedAttempt]:
    return [
        SolvedAttempt(f"m{i}", rng.randint(30, 5000), rng.choice([15, 31, 46, 62, 93, 108]), rng.randint(0, 6))
        for i in range(n)
    ]


def test_task_points_bounds_order_and_determinism():
    rng = random.Random(1)
    for _ in range(TRIALS):
        attempts = random_attempts(rng, rng.randint(1, 15))
        scores = score_task(attempts)
        for a in attempts:
            assert 10 <= scores[a.key].points <= 110
        shuffled = attempts[:]
        rng.shuffle(shuffled)
        assert score_task(shuffled) == scores  # input order never matters
        for a in attempts:
            for b in attempts:
                # Faster (or equal) on every count never scores less.
                if a.solve_seconds <= b.solve_seconds and a.runtime_ms <= b.runtime_ms and a.rejected <= b.rejected:
                    assert scores[a.key].points >= scores[b.key].points


def test_more_rejections_never_help():
    rng = random.Random(2)
    for _ in range(TRIALS):
        attempts = random_attempts(rng, rng.randint(1, 10))
        target = rng.choice(attempts)
        worse = [SolvedAttempt(a.key, a.solve_seconds, a.runtime_ms, a.rejected + (a is target)) for a in attempts]
        assert score_task(worse)[target.key].points <= score_task(attempts)[target.key].points


def test_project_points_properties():
    rng = random.Random(3)
    for _ in range(TRIALS):
        entries = [
            EntryInput(f"m{i}", rng.randint(0, 10**6), rng.uniform(0, 100), rng.choice([0.5, 0.6, 0.75, 0.8, 1.0]))
            for i in range(rng.randint(1, 12))
        ]
        scores = score_project(entries)
        for e in entries:
            s = scores[e.key]
            assert s.qualified == (e.writeup_score >= 40)
            assert (s.points == 0) if not s.qualified else (0 <= s.points <= 300)
        for a in entries:
            for b in entries:
                if scores[a.key].qualified and scores[b.key].qualified and a.submitted_at < b.submitted_at:
                    assert scores[a.key].speed_points >= scores[b.key].speed_points
        # An unqualified late or early entry never changes anyone else's points.
        extra = EntryInput("x", rng.randint(0, 10**6), rng.uniform(0, 39.9), 1.0)
        with_extra = score_project([*entries, extra])
        assert all(with_extra[e.key] == scores[e.key] for e in entries)


def test_history_factor_bounds():
    rng = random.Random(4)
    for _ in range(TRIALS):
        snaps, loc = [], 0
        for i in range(rng.randint(1, 16)):
            if rng.random() < 0.7:
                loc = max(0, loc + rng.randint(-50, 400))
                snaps.append(Snapshot(f"s{i}", loc))
            else:
                snaps.append(snaps[-1] if snaps else Snapshot("s0", 0))
        f = history_factor(snaps)
        assert 0.5 <= f <= 1.0
        assert history_factor([*snaps, snaps[-1]]) == f  # an unchanged snapshot adds nothing


def test_board_ranks_are_dense_and_order_free():
    rng = random.Random(5)
    for _ in range(TRIALS):
        rows = [
            BoardEntry(f"m{i}", rng.choice([0, 86, 100, 101, 210]), rng.randint(0, 3), rng.choice([600, 900]),
                       rng.choice([10, 20]))
            for i in range(rng.randint(1, 12))
        ]
        ranked = rank_board(rows)
        ranks = [r for r, _ in ranked]
        assert ranks[0] == 1 and all(b - a in (0, 1) for a, b in zip(ranks, ranks[1:]))
        assert [e.points for _, e in ranked] == sorted((e.points for e in rows), reverse=True)
        shuffled = rows[:]
        rng.shuffle(shuffled)
        assert {e.key: r for r, e in rank_board(shuffled)} == {e.key: r for r, e in ranked}


def test_streak_properties():
    rng = random.Random(6)
    for _ in range(TRIALS):
        states = [(True, rng.random() < 0.7) for _ in range(rng.randint(0, 30))]
        if states and rng.random() < 0.5:
            states[-1] = (False, rng.random() < 0.5)  # today's task, still open
        current, best = streaks(states)
        assert 0 <= current <= best <= sum(solved for _, solved in states)


def test_grading_properties():
    rng = random.Random(7)
    sentences = [s + "." for a in ANSWERS for s in a.split(". ") if s]
    for _ in range(100):
        answers = [" ".join(rng.sample(sentences, rng.randint(1, 8))) for _ in range(4)]
        b = grade(answers, SNIPPET_IDS, IDS, KEYWORD_GROUPS)
        assert 0 <= b.total <= 100
        assert grade(answers, list(reversed(SNIPPET_IDS)), list(reversed(IDS)), KEYWORD_GROUPS).total == b.total
        if b.gate_passed:
            parts = b.completeness + b.grounding + b.change_relevance + b.variety + b.identifier_density + b.sentences
            assert b.total == round(parts, 1)
        else:
            assert b.total == 0


# -- edge cases ----------------------------------------------------------------


def test_grade_edge_cases():
    with pytest.raises(ValueError, match="exactly four"):
        grade(ANSWERS[:3], SNIPPET_IDS, IDS, KEYWORD_GROUPS)
    assert grade(ANSWERS, SNIPPET_IDS, IDS, []).change_relevance == 0  # no keyword groups
    b = grade(["", "", "", ""], [], [], KEYWORD_GROUPS)
    assert (b.total, b.sentences, b.avg_sentence_words) == (0, 0, 0)
    assert grade(["... !!! ???"] * 4, [], [], []).sentences == 0  # punctuation is not a sentence
    codey = [" ".join(["check_winner()"] * 30 + ["word"] * 30)] * 4
    assert grade(codey, SNIPPET_IDS, IDS, KEYWORD_GROUPS).identifier_density == 0  # 50% code-like
    assert grade(ANSWERS, SNIPPET_IDS, IDS, KEYWORD_GROUPS).to_dict()["total"] == 100


def test_snippet_edge_cases():
    with pytest.raises(ValueError, match="no source files"):
        select_snippet({}, [], "1:a")
    s = select_snippet({"a.py": b"\n\n\n", "b.py": b""}, [], "1:a")  # only empty files
    assert s.path in ("a.py", "b.py") and s.start == 1
