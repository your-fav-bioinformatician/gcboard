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
| **M3 Projects**: schedule, repo registration and verification, snapshots, history factor, project screen | done |
| **M4 Writeups**: identifiers, snippets, locked paste-free editor, rate-checked autosave, grading, project points and finalization | done |
| **M5 Hardening**: backups and restore, retries, client version check, terminal pass, full scoring suite | done, except the manual pass in the nine named terminals ([checklist](docs/terminal-checklist.md)) |

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

**The writeup** starts the moment you submit. For 45 minutes gcboard shows only the
writeup: four questions about your own code, including a 15-line snippet picked from
your submission. You can't leave, quit, or paste, and answers are saved every 10
seconds. Text that appears faster than anyone can type is rolled back. Closing the
app doesn't pause the clock; open it again to continue. Press `ctrl+s` to submit
early once every answer meets its minimum length. At the deadline, your last saved
text is submitted for you. The score and its breakdown appear on the project
screen straight away.

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
| `tab` / `shift+tab` | next / previous answer (Writeup) |
| `ctrl+n` / `ctrl+p` | scroll the code snippet (Writeup; `ctrl+down` / `ctrl+up` also work outside macOS) |
| `ctrl+s` | submit the writeup |
| `q` | quit (not during a writeup) |

## For the admin: run the server

```bash
git clone https://github.com/your-fav-bioinformatician/gcboard && cd gcboard
uv sync --extra server
cp config.example.toml config.toml   # set timezone, daily_post_time, admins
export GCBOARD_GITHUB_TOKEN=github_pat_...   # read-only token for public repos; never sent to clients
uv run gcboard-admin validate-config
uv run gcboard-server --config config.toml --host 127.0.0.1 --port 8000
```

For a real deployment (systemd, HTTPS, backups, restore, upgrades), see
[docs/operations.md](docs/operations.md). The short version: put it behind
HTTPS, for example with Caddy:

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
uv run gcboard-admin writeup HANDLE PROJECT_ID     # read a member's answers (logged in the audit log)
uv run gcboard-admin reroll --reason "duplicate of a contest problem"   # only if nobody started
uv run gcboard-admin void-attempt HANDLE 2026-11-13 --reason "..."
uv run gcboard-admin ban HANDLE --reason "..."     # also: unban, revoke-tokens
uv run gcboard-admin delete-member HANDLE --reason "..." [--yes]   # delete all their data; dry run without --yes
uv run gcboard-admin recompute                     # rebuild every score from raw data
uv run gcboard-admin backup                        # also: backups, restore FILE --yes (server stopped)
uv run gcboard-admin sync-ratings                  # refresh Codeforces ratings now
uv run gcboard-admin audit                         # show the audit log
```

Every action is written to the audit log.

## Development

```bash
uv sync --all-extras
uv run pytest
```

The tests cover:

- every formula and worked example in SRD section 5, plus invariants checked over
  thousands of random inputs. CI requires 100% line and branch coverage of
  `scoring.py` and `textrules.py`.
- the API and scheduled jobs against fake Codeforces and GitHub.
- the Textual client, driven end to end against a live server, with every
  rendered screen checked for non-ASCII glyphs.
- the real client process in pseudo-terminals at 80x24, under several `TERM`
  values, 16, 256 and truecolor modes, and `NO_COLOR`.
- backup and restore round trips.

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
│  ├─ writeups.py    writeup sessions, project scoring and finalization
│  ├─ textrules.py   identifiers, snippets, writeup grading (SRD 5.3)
│  ├─ scoring.py     section 5 as pure functions
│  ├─ jobs.py        scheduled jobs
│  ├─ board.py       leaderboards, streaks, profiles
│  ├─ timewin.py     day and week boundaries in the group time zone
│  ├─ backup.py      online backups, checks, restore
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
- Project points count in the week the project closes (its `closes_at`), so a
  project closing Monday 00:00 counts in the week starting that Monday. They stay
  provisional (`~`) until every writeup has ended and the project is finalized.
- Identifier mentions are case-insensitive, as SRD 5.3 says. A prose word like
  "board" therefore counts as mentioning a `Board` class, and file names
  (`game.py` -> `game`) count too.
- Grounding expects 6 non-snippet identifiers in Q2-Q4 even for small repos, as
  written in 5.3. For tiny repos, snippet selection lowers its 30-line file bar
  step by step, so there is always a snippet.
- "Code-like" words for identifier density are repo identifiers plus snake_case,
  camelCase, `call()`, and `obj.method` tokens.
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
