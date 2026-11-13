import io
import tarfile

import pytest

from gcboard.server.repos import RepoRef, count_lines, is_source_path, parse_repo_url, read_tarball


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/Noor/TicTacToe", RepoRef("noor", "tictactoe")),
        ("github.com/noor/tic-tac-toe.git", RepoRef("noor", "tic-tac-toe")),
        ("https://www.github.com/noor/ttt/", RepoRef("noor", "ttt")),
        ("https://gitlab.com/noor/ttt", None),
        ("https://github.com/noor", None),
        ("https://github.com/noor/ttt/tree/main", None),
        ("https://github.com/noor/..", None),
        ("javascript:alert(1)", None),
    ],
)
def test_parse_repo_url(url, expected):
    assert parse_repo_url(url) == expected


def test_source_paths_follow_srd_rules():
    assert is_source_path("src/game.py")
    assert is_source_path("Board.CPP")
    assert not is_source_path("README.md")
    assert not is_source_path("tests/test_game.py")
    assert not is_source_path("node_modules/x/index.js")
    assert not is_source_path("web/app.min.js")
    assert not is_source_path("build/out.c")
    # Literal substring rule from SRD 5.3: "contest" contains "test".
    assert not is_source_path("src/contest.py")


def test_count_lines_ignores_blank():
    assert count_lines(b"a = 1\n\n   \n\tb = 2\n") == 2
    assert count_lines(b"\xff\xfe broken utf8\n") == 1


def _tar(entries: dict[str, bytes], links: dict[str, str] = {}) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        root = tarfile.TarInfo("noor-ttt-abc1234")
        root.type = tarfile.DIRTYPE
        tar.addfile(root)
        for path, data in entries.items():
            info = tarfile.TarInfo(f"noor-ttt-abc1234/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for path, target in links.items():
            info = tarfile.TarInfo(f"noor-ttt-abc1234/{path}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return buf.getvalue()


def test_read_tarball_in_memory():
    data = _tar(
        {
            ".gcboard": b"gcboard-1234\n",
            "game.py": b"def play():\n    pass\n",
            "README.md": b"# hi\n",
            "big.py": b"x = 1\n" * 40000,  # > 200 KB: not a source file
            "tests/test_game.py": b"assert True\n",
        },
        links={"link.py": "/etc/passwd"},
    )
    files = read_tarball(data, "abc1234")
    assert files.verify_file.strip() == "gcboard-1234"
    assert set(files.sources) == {"game.py"}
    assert files.source_loc == 2
