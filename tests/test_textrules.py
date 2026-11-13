"""SRD 5.3: identifier extraction, snippets, mentions, and grading."""

import pytest

from gcboard.server.textrules import (
    definitions,
    extract_identifiers,
    grade,
    group_matched,
    is_identifier_token,
    mentioned,
    select_snippet,
    sentences,
    split_identifier,
    words,
)

from .sample_writeup import ANSWERS, FILES, KEYWORD_GROUPS, VAGUE

SRC = {k: v.encode() for k, v in FILES.items()}
IDS = extract_identifiers(SRC)
SNIPPET_IDS = ["Board", "check_winner", "place_mark"]


def test_extract_python_repo():
    assert IDS == [
        "ask_move", "best_move", "Board", "check_winner", "game", "is_full", "minimax", "place_mark",
        "play_turn", "Player", "render", "score_board", "switch_player",
    ]
    # __init__, "ai" (too short), and the denylist never appear.
    assert not {"__init__", "ai", "main", "init"} & set(IDS)


@pytest.mark.parametrize(
    "path,code,expected",
    [
        ("ui.ts", "export function renderBoard(b: Board) {}\nconst cellSize = 3;\nclass View {\n  async drawCell(x: number): void {\n  }\n  if (x) {\n  }\n}\n",
         ["cellSize", "drawCell", "renderBoard", "View"]),  # "ui" is too short
        ("main.go", "func (b *Board) Move(x int) error {\n}\ntype Player struct{}\nfunc main() {}\n", ["Move", "Player"]),
        ("lib.rs", "pub fn score_line(v: &[u8]) -> u32 { 0 }\nstruct Grid;\nenum Cell { X, O }\n", ["Cell", "Grid", "lib", "score_line"]),
        ("engine.cpp", "static int minimax(Board& b, int depth)\n{\n  if (depth == 0) return 0;\n  return evaluate(b);\n}\nclass Engine {};\nint x = foo(3);\n",
         ["Engine", "minimax"]),
        ("Game.java", "public class Game {\n  private boolean isOver(int[] cells) {\n    while (true) {}\n  }\n}\n", ["Game", "isOver"]),
        ("cli.rb", "module Cli\n  def self.parse_args(argv)\n  end\nend\n", ["Cli", "parse_args"]),
        ("README.md", "def not_code(): pass\n", ["README"]),
    ],
)
def test_extract_other_languages(path, code, expected):
    assert extract_identifiers({path: code.encode()}) == sorted(expected, key=str.lower)


def test_definitions_have_line_numbers():
    assert (5, "Board") in definitions("board.py", BOARD := FILES["board.py"])
    assert BOARD.splitlines()[5].startswith("class Board")


def test_mentions():
    assert split_identifier("checkWinner") == ["check", "winner"] == split_identifier("check_winner")
    assert split_identifier("HTTPServer") == ["http", "server"]
    assert mentioned("check_winner", "it calls check_winner() after a move")
    assert mentioned("checkWinner", "the Check  Winner step")  # split form, any case and spacing
    assert mentioned("check_winner", "then check winner runs")
    assert not mentioned("check_winner", "it checks the winner")
    assert not mentioned("render", "the renderer draws it")  # not a prefix match
    assert not mentioned("Board", "keyboard input")


def test_snippet_is_reproducible_and_qualifies():
    a = select_snippet(SRC, IDS, "7:deadbeef")
    assert a == select_snippet(SRC, IDS, "7:deadbeef")
    picks = {(s.path, s.start) for s in (select_snippet(SRC, IDS, f"{i}:sha") for i in range(40))}
    assert len(picks) > 3  # different entries get different snippets
    for i in range(40):
        s = select_snippet(SRC, IDS, f"{i}:sha")
        lines = FILES[s.path].splitlines()[s.start - 1 : s.end]
        assert s.end - s.start + 1 == 15
        assert sum(1 for line in lines if line.strip()) >= 10
        assert len(s.identifiers) >= 2
        assert any(start == s.start - 1 for start, _ in definitions(s.path, FILES[s.path]))


def test_snippet_falls_back_for_tiny_repos():
    s = select_snippet({"t.py": b"x = 1\ny = 2\n"}, [], "1:a")
    assert (s.path, s.start, s.end) == ("t.py", 1, 2)


def test_words_and_sentences():
    assert words("I wrote 3 tests - 100% pass, ok?") == ["I", "wrote", "tests", "pass,", "ok?"]
    assert [len(s) for s in sentences("One two three. Four five!\n\nSix seven")] == [3, 2, 2]


def test_keyword_groups():
    assert group_matched(["undo"], "Undoing a move")  # start of a word
    assert not group_matched(["do"], "undo")
    assert group_matched(["list of moves"], "keep a list  of\nmoves")  # phrase
    assert not group_matched(["stack"], "")


def test_identifier_tokens():
    ids = {"minimax"}
    assert is_identifier_token("minimax,", ids)
    assert is_identifier_token("check_winner()", ids)
    assert is_identifier_token("isFull", ids)
    assert is_identifier_token("board.render()", ids)
    assert not is_identifier_token("Board.", ids) or "board" in ids
    assert not is_identifier_token("well-known", ids)


def test_honest_writeup_scores_full_marks():
    b = grade(ANSWERS, SNIPPET_IDS, IDS, KEYWORD_GROUPS)
    assert b.gate_passed
    assert (b.completeness, b.grounding, b.change_relevance, b.variety, b.identifier_density, b.sentences) == (
        20, 30, 25, 10, 10, 5
    )
    assert b.total == 100


def test_gate():
    at_limit = ["word " * 19 + "end", ANSWERS[1], ANSWERS[2], ANSWERS[3]]  # exactly 20 words: allowed
    assert grade(at_limit, SNIPPET_IDS, IDS, KEYWORD_GROUPS).gate_passed
    short = ["word " * 18 + "end", ANSWERS[1], ANSWERS[2], ANSWERS[3]]  # 19 words
    b = grade(short, SNIPPET_IDS, IDS, KEYWORD_GROUPS)
    assert (b.total, b.gate_reason) == (0, "an answer has fewer than 20 words")
    small = [" ".join(f"w{i}x{j}" for j in range(36)) for i in range(4)]  # 4 x 36 = 144 words
    b = grade(small, [], IDS, KEYWORD_GROUPS)
    assert (b.total, b.gate_reason) == (0, "fewer than 150 words in total")
    copied = [ANSWERS[0], ANSWERS[0] + " Also this.", ANSWERS[2], ANSWERS[3]]
    b = grade(copied, SNIPPET_IDS, IDS, KEYWORD_GROUPS)
    assert (b.total, b.gate_reason) == (0, "answers 1 and 2 are nearly the same")
    # Category scores are still reported, so the member can see what to fix.
    assert b.grounding > 0


def test_listing_identifiers_scores_worse_than_explaining():
    dump = " ".join(IDS * 10)
    listed = [ANSWERS[0], ANSWERS[1] + " " + dump, ANSWERS[2], ANSWERS[3]]
    b = grade(listed, SNIPPET_IDS, IDS, KEYWORD_GROUPS)
    assert b.identifier_ratio > 0.2 and b.identifier_density < 10
    assert b.total < grade(ANSWERS, SNIPPET_IDS, IDS, KEYWORD_GROUPS).total


def test_partial_scores():
    vague = VAGUE
    b = grade(vague, SNIPPET_IDS, IDS, KEYWORD_GROUPS)
    assert b.gate_passed
    assert b.grounding == 0 and b.change_relevance == 0
    assert b.variety < 10  # repeated sentences
    assert b.total < 40  # below the default pass threshold
