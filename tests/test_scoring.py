"""Section 5 formulas, including every worked example in 5.6 (NFR-MAINT-1)."""

import pytest

from gcboard.server.scoring import (
    BoardEntry,
    EntryInput,
    Snapshot,
    SolvedAttempt,
    dense_ranks,
    history_factor,
    rank_board,
    round_half_up,
    score_project,
    score_task,
    speed_points,
    streaks,
)


def mmss(s: str) -> int:
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


def test_dense_ranks():
    assert dense_ranks([46, 31, 46]) == [2, 1, 2]
    assert dense_ranks([]) == []


def test_round_half_up():
    assert round_half_up(216.5) == 217
    assert round_half_up(2.5) == 3
    assert round_half_up(2.4999) == 2


# 5.6 daily task worked example
def test_worked_example_daily_task():
    attempts = [
        SolvedAttempt("ali_k", mmss("18:40"), 46, 1),
        SolvedAttempt("noor", mmss("25:00"), 31, 0),
        SolvedAttempt("zaid99", mmss("40:00"), 46, 0),
    ]
    s = score_task(attempts)
    assert (s["ali_k"].base, s["ali_k"].runtime_bonus, s["ali_k"].penalty, s["ali_k"].points) == (100, 6, 5, 101)
    assert (s["noor"].base, s["noor"].runtime_bonus, s["noor"].penalty, s["noor"].points) == (90, 10, 0, 100)
    assert (s["zaid99"].base, s["zaid99"].runtime_bonus, s["zaid99"].penalty, s["zaid99"].points) == (80, 6, 0, 86)


def test_task_base_floor_penalty_cap_and_min_points():
    # 12 solvers with distinct times: rank 9+ floors at 30.
    attempts = [SolvedAttempt(i, 60 * (i + 1), 100 + i, 0) for i in range(12)]
    s = score_task(attempts)
    assert [s[i].base for i in range(9)] == [100, 90, 80, 70, 60, 50, 40, 30, 30]
    assert s[11].runtime_bonus == 0
    # Penalty is capped at 20 and total never drops below 10.
    s = score_task(
        [SolvedAttempt("a", 10, 1, 0)] + [SolvedAttempt(f"x{i}", 100 + i, 50, 10) for i in range(10)]
    )
    assert s["x0"].penalty == 20
    assert s["x9"].points == 30 + 6 - 20  # rank 10 base 30, runtime rank 2
    lone = score_task([SolvedAttempt("a", 10, 1, 0), *[SolvedAttempt(f"y{i}", 20 + i, 99, 4) for i in range(9)]])
    assert lone["y8"].points == max(10, 30 + 6 - 20)


def test_task_ties_share_rank():
    s = score_task([SolvedAttempt("a", 100, 10, 0), SolvedAttempt("b", 100, 10, 0)])
    assert s["a"] == s["b"]
    assert s["a"].points == 110


@pytest.mark.parametrize(
    "rank,expected", [(1, 300), (2, 255), (3, 217), (4, 184), (5, 157), (6, 133), (7, 113), (8, 96), (9, 90), (20, 90)]
)
def test_speed_points_table(rank, expected):
    assert round_half_up(speed_points(rank)) == expected


# 5.6 project worked example
def test_worked_example_project():
    entries = [
        EntryInput("early", submitted_at=100, writeup_score=35, history_factor=1.0),
        EntryInput("noor", submitted_at=200, writeup_score=72, history_factor=1.0),
        EntryInput("c", submitted_at=300, writeup_score=80, history_factor=0.5),
        EntryInput("d", submitted_at=400, writeup_score=40, history_factor=1.0),
    ]
    s = score_project(entries)
    assert s["early"].qualified is False and s["early"].points == 0 and s["early"].submit_rank is None
    assert s["noor"].submit_rank == 1 and s["noor"].points == 216
    assert s["c"].submit_rank == 2 and s["c"].points == round_half_up(255 * 0.8 * 0.5)
    assert s["d"].submit_rank == 3  # exactly at the threshold qualifies


def test_history_all_at_once_is_half():
    snaps = [Snapshot("a", 0), Snapshot("a", 0), Snapshot("a", 0), Snapshot("b", 600)]
    assert history_factor(snaps) == 0.5


def test_history_steady_work_is_full():
    snaps = [Snapshot("a", 0), Snapshot("b", 200), Snapshot("c", 400), Snapshot("d", 600)]
    assert history_factor(snaps) == 1.0


def test_history_dump_multiplier():
    # Four active intervals (1.0), but one adds 80% of the final code.
    snaps = [Snapshot("a", 0), Snapshot("b", 50), Snapshot("c", 100), Snapshot("d", 900), Snapshot("e", 1000)]
    assert history_factor(snaps) == 0.75


def test_history_inactive_intervals_do_not_count():
    # SHA changes but lines do not (e.g. editing a README) -> not active.
    snaps = [Snapshot("a", 0), Snapshot("b", 0), Snapshot("c", 100), Snapshot("c", 100)]
    assert history_factor(snaps) == 0.5  # 0.6 x 0.75 = 0.45 -> floor


def test_rank_board_tiebreakers():
    rows = [
        BoardEntry("late", 200, 2, 1000, reached_at=50),
        BoardEntry("early", 200, 2, 1000, reached_at=40),
        BoardEntry("faster", 200, 2, 900, reached_at=99),
        BoardEntry("more", 200, 3, 5000, reached_at=99),
        BoardEntry("top", 300, 1, 10, reached_at=99),
        BoardEntry("twin", 200, 2, 1000, reached_at=40),
    ]
    ranked = [(r, e.key) for r, e in rank_board(rows)]
    assert ranked == [(1, "top"), (2, "more"), (3, "faster"), (4, "early"), (4, "twin"), (5, "late")]


def test_streaks():
    C, O = True, False  # closed, open
    assert streaks([]) == (0, 0)
    assert streaks([(C, True), (C, True), (C, False), (C, True), (C, True), (C, True)]) == (3, 3)
    # Today's open task not yet solved does not break the streak.
    assert streaks([(C, True), (C, True), (O, False)]) == (2, 2)
    # Solving today's task extends it.
    assert streaks([(C, True), (C, True), (O, True)]) == (3, 3)
    assert streaks([(C, True), (C, True), (C, True), (C, False), (O, True)]) == (1, 3)
