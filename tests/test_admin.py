from sqlmodel import Session

from gcboard.server import admin
from gcboard.server.db import User, make_engine


def test_ban_is_audited(tmp_path, capsys):
    db = tmp_path / "a.db"
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"[server]\ndatabase = '{db}'\nadmins = ['Boss']\n")  # literal string: Windows paths
    with Session(make_engine(str(db))) as s:
        s.add(User(cf_handle="Boss", joined_at=0, is_admin=True))
        s.add(User(cf_handle="noor", joined_at=0))
        s.commit()

    admin.main(["--config", str(cfg), "ban", "NOOR", "--reason", "shared account"])
    admin.main(["--config", str(cfg), "users"])
    admin.main(["--config", str(cfg), "audit"])
    out = capsys.readouterr().out
    assert "ban NOOR: done" in out
    assert "noor" in out and "banned" in out
    audit_line = out.strip().splitlines()[-1]
    assert "Boss" in audit_line and "ban" in audit_line and "shared account" in audit_line
