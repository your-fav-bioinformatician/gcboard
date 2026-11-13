# gcboard

A terminal leaderboard for a group of programmers who practice solving problems
**without AI**. Every day it posts a Codeforces problem and times each member from
the moment they reveal it. Every two weeks it runs a project, which members build
and then explain in a locked, paste-free writeup. All scoring is automatic and
deterministic.

The full specification is in [docs/SRD.md](docs/SRD.md).

```
> leaderboard --week                          week 46 · ends Sun 23:59
┌────┬──────────────┬────────┬────────┬────────┐
│ #  │ handle       │ pts    │ solved │ streak │
├────┼──────────────┼────────┼────────┼────────┤
│ 1  │ ali_k        │ 512    │ 5      │ 5d     │
│ 2  │ noor         │ ~489   │ 5      │ 12d    │
└────┴──────────────┴────────┴────────┴────────┘
```

## Status

| Milestone | State |
| --- | --- |
| **M1 Core**: server, login challenge, daily task posting, start/verify, task scoring, weekly board, client login/board/today screens | done |
| M2 Profiles: profiles, all-time board, rating sync, rules screen, admin CLI | partly done (all-time board, rules screen, admin CLI basics) |
| M3 Projects: schedule, repo registration, snapshots, history factor | scoring formulas only (`history_factor`, `score_project`) |
| M4 Writeups: snippets, identifiers, locked editor, grading | not started |
| M5 Hardening: backups, terminal compatibility pass | CI runs on Linux, macOS, Windows |

## For members: install the client

```bash
uv tool install git+https://github.com/your-fav-bioinformatician/gcboard
# or
pipx install git+https://github.com/your-fav-bioinformatician/gcboard
```

Point it at your group's server, then run it:

```bash
export GCBOARD_SERVER=https://gcboard.example.org
gcboard
```

You can also put `server_url = "https://..."` in `config.toml` inside your user
config directory (`~/.config/gcboard/` on Linux). Your login token is stored next
to it, readable only by you.

**Logging in:** enter your Codeforces handle. gcboard picks a random problem; submit
anything that fails with `COMPILATION_ERROR` within 5 minutes, then press `v`.

| Key | Action |
| --- | --- |
| `l` | leaderboard |
| `t` | today's task |
| `?` | rules |
| `w` | week / all-time (leaderboard) |
| `s` | start today's task |
| `o` | open the problem in your browser |
| `r` | refresh |
| `q` | quit |

## For the admin: run the server

```bash
git clone https://github.com/your-fav-bioinformatician/gcboard && cd gcboard
uv sync --extra server
cp config.example.toml config.toml   # set timezone, daily_post_time, admins
uv run gcboard-admin validate-config
uv run gcboard-server --config config.toml --host 127.0.0.1 --port 8000
```

Put it behind HTTPS, for example with Caddy:

```
gcboard.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

The server posts the daily task at the configured time, checks Codeforces for ACs
every 2 minutes, and finalizes each task at close. Codeforces outages delay
verification but never change a score.

### Admin CLI

```bash
uv run gcboard-admin users
uv run gcboard-admin reroll --reason "duplicate of a contest problem"   # only if nobody started
uv run gcboard-admin void-attempt HANDLE 2026-11-13 --reason "..."
uv run gcboard-admin ban HANDLE --reason "..."     # also: unban, revoke-tokens
uv run gcboard-admin recompute                     # rebuild every score from raw data
```

Every action is written to the audit log.

## Development

```bash
uv sync --all-extras
uv run pytest
```

The tests cover every formula and worked example in SRD section 5, the Codeforces
picker and verifier, the API and scheduled jobs against a fake Codeforces, and the
Textual client driven end to end against a live server.

### Layout

```
gcboard/
├─ client/       Textual app (entry point: gcboard), HTTP client, screens
├─ server/
│  ├─ main.py        FastAPI app and routes
│  ├─ db.py          tables
│  ├─ codeforces.py  rate-limited API wrapper, picker, verifier
│  ├─ scoring.py     section 5 as pure functions
│  ├─ jobs.py        scheduled jobs
│  ├─ board.py       leaderboards and streaks
│  ├─ timewin.py     day and week boundaries in the group time zone
│  ├─ config.py      config.toml schema
│  └─ admin.py       gcboard-admin CLI
└─ shared/
   └─ schemas.py     request/response models used by both sides
```

This differs slightly from SRD 3.3: `client/`, `server/`, and `shared/` live inside
a `gcboard` package so that installing the client doesn't put top-level packages
named `client` and `server` into site-packages. Members install only the client
dependencies; the server needs the `server` extra.

### Implementation notes

- All server timestamps are Unix epoch seconds; calendar logic lives in `timewin.py`.
- A submission still being judged before a member's first AC holds back task
  finalization for up to an hour after close, so a late verdict isn't missed.
- Attempts have `voided_at`/`void_reason` columns (not in SRD section 6) to support
  `void-attempt`.
