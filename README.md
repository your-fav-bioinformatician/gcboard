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
| **M2 Profiles**: profiles, streaks, weekly wins, all-time board, rating sync, rules screen, admin CLI basics | done |
| **M3 Projects**: schedule, repo registration and verification, snapshots, history factor, project screen | done (submitting freezes the repo; the writeup that follows is M4) |
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

**Projects:** press `p`, then `e` and enter the URL of a new, empty, public GitHub
repo. Commit a file named `.gcboard` containing the code gcboard shows you, push, and
press `v`. The server snapshots your repo every day at midday. Press `s` to submit
when you're done. This freezes your repo at that commit, and you can only submit once.

**Logging in:** enter your Codeforces handle. gcboard picks a random problem; submit
anything that fails with `COMPILATION_ERROR` within 5 minutes, then press `v`.

| Key | Action |
| --- | --- |
| `l` | leaderboard |
| `t` | today's task |
| `p` | the current project |
| `m` | my profile |
| `enter` | open the selected member's profile (leaderboard) |
| `?` | rules |
| `w` | week / all-time (leaderboard) |
| `s` | start today's task, or submit your project |
| `o` | open the problem in your browser |
| `e` / `v` | register / verify your project repo (Project) |
| `r` | refresh |
| `q` | quit |

## For the admin: run the server

```bash
git clone https://github.com/your-fav-bioinformatician/gcboard && cd gcboard
uv sync --extra server
cp config.example.toml config.toml   # set timezone, daily_post_time, admins
export GCBOARD_GITHUB_TOKEN=github_pat_...   # read-only token for public repos; never sent to clients
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
every 2 minutes, finalizes each task at close, and refreshes members' Codeforces
ratings once a day. Codeforces outages delay
verification but never change a score.

### Admin CLI

```bash
uv run gcboard-admin users
uv run gcboard-admin project-create examples/project.example.toml   # also: projects, project-edit
uv run gcboard-admin void-entry HANDLE PROJECT_ID --reason "..."
uv run gcboard-admin reroll --reason "duplicate of a contest problem"   # only if nobody started
uv run gcboard-admin void-attempt HANDLE 2026-11-13 --reason "..."
uv run gcboard-admin ban HANDLE --reason "..."     # also: unban, revoke-tokens
uv run gcboard-admin recompute                     # rebuild every score from raw data
uv run gcboard-admin sync-ratings                  # refresh Codeforces ratings now
uv run gcboard-admin audit                         # show the audit log
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
│  ├─ repos.py       GitHub reads, source files, line counts
│  ├─ projects.py    registration, verification, snapshots, submission
│  ├─ scoring.py     section 5 as pure functions
│  ├─ jobs.py        scheduled jobs
│  ├─ board.py       leaderboards, streaks, profiles
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
- Weekly wins and best weekly finish count only completed weeks (Monday to Sunday
  in the group time zone); a tie for first gives every tied member a win.
- Repos are read as one GitHub tarball per commit, in memory: nothing is unpacked
  to disk or run. A daily snapshot skips the download when HEAD hasn't moved.
- Source files follow SRD 5.3 literally, including the plain substring match on
  excluded words, so `contest.py` is excluded because it contains "test".
- `repo_url` has no database UNIQUE constraint (SRD 6 has one). An unverified
  registration must not lock a repo away from its owner, so the server instead
  refuses any repo already used by a verified entry, in any project.
- Attempts have `voided_at`/`void_reason` columns (not in SRD section 6) to support
  `void-attempt`.
