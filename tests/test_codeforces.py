import random
from datetime import date, time
from zoneinfo import ZoneInfo

from gcboard.server.codeforces import PENDING, AttemptResult, evaluate_attempt, pick_problem
from gcboard.server.timewin import current_window, week_of


def sub(i, at, verdict, contest=1000, index="A", ms=50):
    s = {"id": i, "creationTimeSeconds": at, "problem": {"contestId": contest, "index": index}, "timeConsumedMillis": ms}
    if verdict:
        s["verdict"] = verdict
    return s


def test_evaluate_first_ac_and_rejections():
    subs = [
        sub(5, 400, "OK", ms=31),
        sub(4, 350, "OK", ms=46),
        sub(3, 300, "WRONG_ANSWER"),
        sub(2, 250, "COMPILATION_ERROR"),
        sub(1, 50, "WRONG_ANSWER"),  # before start: ignored
    ]
    r = evaluate_attempt(subs, 1000, "A", started_at=100, closes_at=1000)
    assert r == AttemptResult(submission_id=4, accepted_at=350, runtime_ms=46, rejected_count=1)


def test_evaluate_ignores_other_contest_and_after_close():
    subs = [sub(1, 200, "OK", contest=1001), sub(2, 1000, "OK")]
    assert evaluate_attempt(subs, 1000, "A", 100, 1000) is None


def test_evaluate_pending_before_ac():
    assert evaluate_attempt([sub(2, 300, "OK"), sub(1, 200, "TESTING")], 1000, "A", 100, 1000) == PENDING
    assert evaluate_attempt([sub(1, 200, None)], 1000, "A", 100, 1000) == PENDING


def problems():
    return [
        {"contestId": 1, "index": "A", "rating": 1000, "tags": []},
        {"contestId": 2, "index": "A", "rating": 1000, "tags": ["interactive"]},
        {"contestId": 3, "index": "A", "rating": 1100, "tags": []},
        {"contestId": 4, "index": "A", "rating": 1300, "tags": []},
        {"contestId": 5, "index": "A", "tags": []},  # unrated
    ]


def test_pick_exact_rating():
    p = pick_problem(problems(), 1000, [100, 200], ["*special", "interactive"], set(), random.Random(1))
    assert (p["contestId"], p["index"]) == (1, "A")


def test_pick_fallback_and_exclusions():
    p = pick_problem(problems(), 1000, [100, 200], ["interactive"], {(1, "A")}, random.Random(1))
    assert p["contestId"] == 3  # ±100
    p = pick_problem(problems(), 1200, [100, 200], [], {(3, "A")}, random.Random(1))
    assert p["contestId"] == 4  # 1300 = +100
    assert pick_problem(problems(), 2000, [100, 200], [], set(), random.Random(1)) is None


def test_task_window_respects_post_time():
    tz = ZoneInfo("Asia/Baghdad")  # UTC+3, no DST
    # 2026-11-13 05:00 local, post time 06:00 -> still yesterday's task.
    now = 1794549600 - 3 * 3600 + 5 * 3600  # placeholder recomputed below
    from datetime import datetime

    now = datetime(2026, 11, 13, 5, 0, tzinfo=tz).timestamp()
    w = current_window(now, tz, time(6, 0))
    assert w.task_date == date(2026, 11, 12)
    assert w.closes_at == datetime(2026, 11, 13, 6, 0, tzinfo=tz).timestamp()
    w = current_window(now + 3600, tz, time(6, 0))
    assert w.task_date == date(2026, 11, 13)


def test_week_bounds():
    from datetime import datetime

    tz = ZoneInfo("UTC")
    wk = week_of(datetime(2026, 11, 15, 23, 59, tzinfo=tz).timestamp(), tz)  # a Sunday
    assert (wk.monday, wk.sunday, wk.iso_week) == (date(2026, 11, 9), date(2026, 11, 15), 46)
    assert wk.ends_at == datetime(2026, 11, 16, tzinfo=tz).timestamp()
