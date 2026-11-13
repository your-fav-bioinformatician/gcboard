# gcboard — Software Requirements Document

**Version:** 0.1 (draft) · **Date:** November 13, 2026 · **Status:** for review

---

## 1. Introduction

### 1.1 Purpose

This document specifies the requirements for **gcboard**, a terminal application for a group of programmers who practice solving problems without AI. It defines what the system does, how every score is calculated, and the constraints the implementation must respect. It is the reference for building, testing, and settling disputes about scoring.

### 1.2 Scope

gcboard consists of a terminal client that members install and run, and a server that the client talks to. The system will:

- post one Codeforces problem every day automatically and time each member's solve from the moment they reveal it;
- run a project every two weeks, which members build in their own GitHub repository;
- lock members into a timed, paste-free writeup when they submit a project, in which they explain their own code;
- score daily tasks, projects, and writeups automatically, with no peer review and no manual grading;
- show a weekly leaderboard, an all-time leaderboard, and a profile for every member.

Out of scope for v1: LeetCode or any judge other than Codeforces, executing member code, peer review, a graphical or web interface, in-app administration, and notifications outside the app.

### 1.3 Definitions

| Term | Meaning |
| --- | --- |
| Member | A person with a verified Codeforces handle and a gcboard account. |
| Admin | A member listed as an administrator in the server config. |
| Daily task | The single Codeforces problem posted for a given day. |
| Attempt | A member's run at a daily task, beginning when they press start. |
| Project | A two-week build assignment with a written spec. |
| Entry | A member's participation in one project: their repo, submission, and writeup. |
| Writeup | The timed, locked set of written answers a member gives after submitting a project. |
| Snapshot | The server's daily record of an entry's repo state (HEAD commit and lines of code). |
| AC | An accepted Codeforces submission (verdict `OK`). |
| Group time zone | The single time zone all day and week boundaries are calculated in (config value). |
| Provisional | A score that may still change because its task or project window is still open. |

### 1.4 Design principles

1. **The server is the authority.** Every timestamp, score, and rule check happens on the server. The client only displays and collects input.
2. **Fully automatic and deterministic.** No human or peer judges anything. The same inputs always produce the same score, so any score can be recomputed and audited.
3. **Transparent rules.** All formulas and thresholds in section 5 are visible to members inside the app.
4. **Verifiable over self-reported.** Solve times come from Codeforces' own timestamps; project progress comes from server-observed snapshots, not from commit dates.
5. **Terminal-first.** The app must look and work the same in any modern terminal.

---

## 2. Overall description

### 2.1 Product perspective

```
 [gcboard client]  [gcboard client]  [gcboard client]      members' terminals
          \               |               /
           \            HTTPS            /
            └──────── gcboard server ───┘
                      ├─ REST API (FastAPI)
                      ├─ SQLite database
                      └─ Scheduler (APScheduler)
                           ├──> Codeforces API   (problems, submissions, ratings)
                           └──> GitHub API       (repo files, commits)
```

### 2.2 User roles

| Role | Can do |
| --- | --- |
| Member | Log in, view leaderboards and profiles, start daily tasks, register a project repo, submit a project, complete a writeup. |
| Admin | Everything a member can, plus (through the server CLI) create projects, reroll a daily task before anyone starts it, void attempts, ban members, recompute scores, and edit config. |

### 2.3 Operating environment

**Client:** Python 3.11 or newer on Linux, macOS, or Windows 10/11. Must run in Warp, Windows Terminal, iTerm2, macOS Terminal, GNOME Terminal, Konsole, Alacritty, kitty, and WezTerm, at a minimum size of 80 columns by 24 rows.

**Server:** one Python 3.11+ process on any small Linux host with a public HTTPS endpoint (for example behind Caddy). Persistent disk for the SQLite database and backups.

### 2.4 Constraints

| Constraint | Effect |
| --- | --- |
| Codeforces API allows roughly 1 request per 2 seconds | All Codeforces calls go through one shared, rate-limited queue. |
| Codeforces API is sometimes unavailable, especially during contests | Every Codeforces job retries; scores never depend on when verification happens. |
| GitHub API limits (higher with a token) | The server uses its own GitHub token; members never need one. |
| Project repos must be public | The server must read them without member credentials. |
| No member code is executed | Project scoring uses only repo contents, snapshots, timing, and the writeup. |

### 2.5 Assumptions

- Each member uses exactly one Codeforces handle for gcboard.
- Members have a GitHub account and can create public repositories.
- The group has one admin who maintains the project schedule.
- Writeups are written in English (the grading heuristics in 5.3 assume English text).

---

## 3. System architecture

### 3.1 Components

| Component | Responsibility |
| --- | --- |
| Client app | Textual TUI: screens, input, local timer display, token storage. |
| API layer | Authentication, request validation, serving data, accepting attempts, submissions, and writeup saves. |
| Codeforces service | Rate-limited wrapper for `problemset.problems`, `user.status`, `user.info`. Daily problem picker and attempt verifier. |
| Repo service | Reads repo files at a given commit through the GitHub API, counts lines of code, extracts identifiers, selects writeup snippets. |
| Scoring module | Pure functions implementing section 5. No I/O. Fully unit-tested. |
| Scheduler | Runs the jobs in section 8. |
| Admin CLI | `gcboard-admin` command on the server for admin operations. |

### 3.2 Technology stack

| Layer | Choice |
| --- | --- |
| Client UI | Python, Textual |
| Client HTTP | httpx |
| Client config paths | platformdirs |
| Server API | FastAPI + Uvicorn |
| Database | SQLite in WAL mode, accessed through SQLModel |
| Scheduling | APScheduler |
| HTTPS | Caddy reverse proxy |
| Packaging | One repository, installed by members with `uv tool install` or `pipx install` from Git |

### 3.3 Repository layout

```
gcboard/
├─ client/
│  ├─ app.py              entry point (command: gcboard)
│  ├─ api.py              HTTP calls, token storage, server clock offset
│  └─ screens/            login, leaderboard, today, profile, project, writeup, rules
├─ server/
│  ├─ main.py             FastAPI app and routes
│  ├─ db.py               tables (section 6)
│  ├─ codeforces.py       API wrapper, picker, verifier
│  ├─ repos.py            GitHub reads, LOC, identifiers, snippets
│  ├─ scoring.py          section 5 as pure functions
│  ├─ jobs.py             section 8
│  └─ admin.py            gcboard-admin CLI
├─ shared/
│  └─ schemas.py          request/response models used by both sides
├─ config.example.toml
└─ pyproject.toml
```

---

## 4. Functional requirements

Requirements use "shall". Each has an ID for reference in tests and issues.

### 4.1 Accounts and authentication

| ID | Requirement |
| --- | --- |
| FR-AUTH-1 | A new user shall register by entering their Codeforces handle. The server shall confirm the handle exists via `user.info`. |
| FR-AUTH-2 | The server shall issue an ownership challenge: a randomly chosen Codeforces problem to which the user must submit a solution that fails with `COMPILATION_ERROR` within 5 minutes. |
| FR-AUTH-3 | When the user confirms, the server shall check `user.status` for a `COMPILATION_ERROR` submission to the challenge problem made after the challenge was issued. If found, the account is created (or logged in) and a token is issued. |
| FR-AUTH-4 | Tokens shall be 32 random bytes, stored on the server only as a SHA-256 hash, and saved by the client in the user config directory with owner-only file permissions. |
| FR-AUTH-5 | A Codeforces handle shall map to at most one account. Logging in again on another device issues an additional token; the admin can revoke all of a member's tokens. |
| FR-AUTH-6 | Admin status shall be granted only by listing the handle in the server config. |

### 4.2 Daily tasks

| ID | Requirement |
| --- | --- |
| FR-TASK-1 | The server shall post one daily task automatically at the configured post time in the group time zone. The task stays open until the next post time. |
| FR-TASK-2 | The task's target rating shall follow the weekday schedule in config (default Mon 800, Tue 900, Wed 1000, Thu 1100, Fri 1200, Sat 1300, Sun 1400). |
| FR-TASK-3 | The picker shall choose randomly among problems in `problemset.problems` that have exactly the target rating and meet all of: not tagged `*special` or `interactive`; never used as a past daily task; no submission of any kind (accepted or not) by any active member, checked via `user.status` for every member at pick time. |
| FR-TASK-4 | If no problem qualifies at the target rating, the picker shall try ±100 rating, then ±200, and log which rating was used. |
| FR-TASK-5 | Before a member starts, the client shall show only the date, the rating, and "problem hidden". The server shall not return the problem name, ID, or URL to that member. |
| FR-TASK-6 | When a member presses start (after a confirmation prompt), the server shall create an attempt with `started_at` set to server time and return the problem details. A member can start each task only once. Attempts cannot be paused or restarted. |
| FR-TASK-7 | An attempt is solved by the member's first AC on exactly that problem (same `contestId` and `index`) with `creationTimeSeconds` ≥ `started_at` and \< task close time. Submissions to the same problem under a different contest ID do not count. |
| FR-TASK-8 | The server shall record for a solved attempt: AC time, solve duration (AC time − `started_at`), `timeConsumedMillis` of the AC, and the number of rejected submissions on that problem between `started_at` and the AC, excluding `COMPILATION_ERROR`. |
| FR-TASK-9 | Task points shall be calculated per section 5.1, shown as provisional while the task is open, and finalized when it closes. |
| FR-TASK-10 | The client shall show a running timer anchored to the server's `started_at`, corrected for clock differences between client and server. |
| FR-TASK-11 | The client shall offer a key to open the problem URL in the system browser. |

### 4.3 Projects

| ID | Requirement |
| --- | --- |
| FR-PROJ-1 | A project shall have a title, a Markdown spec, an open time, a close time (default 14 days after open), a change question, and change-question keyword groups (section 5.3). The admin creates projects in advance with the admin CLI. |
| FR-PROJ-2 | Projects shall open and close automatically at their configured times. At most one project is open at a time. |
| FR-PROJ-3 | While a project is open, a member shall register one public GitHub repository URL for it. A repository may not be reused from a previous project. |
| FR-PROJ-4 | On registration the server shall issue a verification code. The member commits a file named `.gcboard` containing that code to the repo root, then confirms. The server reads the file at HEAD to prove the member controls the repo. |
| FR-PROJ-5 | At registration the server shall take a snapshot. If the repo already contains more than the configured limit of source lines (default 50 non-blank lines), registration shall be rejected with the message "start from an empty repo". |
| FR-PROJ-6 | The server shall snapshot every registered entry once a day during the project window, recording HEAD commit SHA and source lines of code (section 5.4 defines "source"). |
| FR-PROJ-7 | A member shall submit their entry before close time. Submission shall: record `submitted_at` in server time, freeze the current HEAD SHA, take a final snapshot, extract identifiers and select the writeup snippet from the frozen SHA, and immediately start the writeup session (4.4). |
| FR-PROJ-8 | Submission shall be rejected if the frozen SHA contains no source files. Each member can submit once per project; there is no resubmission. |
| FR-PROJ-9 | After submission the server shall work only from data captured at submission time, so later force-pushes or repo deletion do not affect scoring. |
| FR-PROJ-10 | Project points shall be calculated per section 5.2. Points are provisional until the project closes and all writeups have ended, then finalized. |

### 4.4 Writeup session

| ID | Requirement |
| --- | --- |
| FR-WRITE-1 | The writeup shall consist of four questions, revealed only when the session starts: **Q1 snippet** — "This is {path}, lines {a}–{b} of your submission. Explain what it does, why you wrote it this way, and how it connects to the rest of the project." **Q2 design** — "Explain how you designed the project: its structure, the main parts, and the algorithms you used." **Q3 change** — the project's admin-written change question (for example "How would you add an undo move?"). **Q4 bug** — "Describe a bug or design problem you hit while building this, and how you found and fixed it." |
| FR-WRITE-2 | The session shall last the configured time limit (default 45 minutes) measured from `started_at` in server time. |
| FR-WRITE-3 | While a session is open, the client shall show the writeup screen and nothing else. Quit shortcuts (`ctrl+q`, `ctrl+c`) shall be disabled on that screen. |
| FR-WRITE-4 | The writeup editor shall reject pasted text (bracketed paste events and the editor's own paste action). |
| FR-WRITE-5 | The client shall autosave all four answers to the server every 10 seconds and on every submit. |
| FR-WRITE-6 | The server shall reject a save whose total text grew faster than the configured maximum input rate (default 25 characters per second since the last accepted save, plus 50 characters of allowance). On rejection the client shall restore the last accepted text and show a warning. This blocks pasting in terminals that send pasted text as fast keystrokes. The server shall not store any typing history beyond the latest accepted text. |
| FR-WRITE-7 | Closing the app, losing connection, or killing the process shall not pause the session. On the next launch, the client shall open the writeup screen directly, restoring the last accepted text. |
| FR-WRITE-8 | The member may submit early once the minimum lengths in 5.3 are met (checked by the client and again by the server). |
| FR-WRITE-9 | At the deadline the server shall automatically submit the last accepted text. |
| FR-WRITE-10 | The writeup shall be graded per section 5.3 immediately on submission, and the member shall see their writeup score and its category breakdown. |
| FR-WRITE-11 | Each answer is limited to 10,000 characters. |

### 4.5 Leaderboards

| ID | Requirement |
| --- | --- |
| FR-BOARD-1 | The weekly leaderboard covers Monday 00:00 to Sunday 23:59:59 in the group time zone. |
| FR-BOARD-2 | Weekly points are the sum of a member's task points for tasks dated that week, plus project points for projects that closed that week. |
| FR-BOARD-3 | Rows show rank, handle, points, tasks solved this week, and current streak. Provisional points are marked with `~`. |
| FR-BOARD-4 | Ties are broken by section 5.5. |
| FR-BOARD-5 | An all-time leaderboard shall also be available, with the same columns and tiebreakers over all history. |
| FR-BOARD-6 | Banned members are hidden from leaderboards. |

### 4.6 Profiles

| ID | Requirement |
| --- | --- |
| FR-PROF-1 | Every member has a profile showing: handle, Codeforces rating and rank (synced daily), join date, current and best streak, tasks solved this week and all time, average and fastest solve time, weekly wins and best weekly finish, and project history (title, submission rank, writeup score, history factor, points). |
| FR-PROF-2 | A streak is the number of consecutive daily tasks the member solved, ending with the most recent closed task or today's task if already solved. |
| FR-PROF-3 | Profiles are visible to all members. Writeup answers are visible only to their author and the admin. |

### 4.7 Administration

| ID | Requirement |
| --- | --- |
| FR-ADMIN-1 | The admin CLI shall support: create and edit projects; reroll today's task (only if no member has started it); void an attempt or entry with a reason; ban and unban members; revoke tokens; recompute all scores; validate config. |
| FR-ADMIN-2 | Every admin action shall be written to an audit log with time, admin, action, and reason. |
| FR-ADMIN-3 | Recompute shall rebuild every score from stored raw data and produce identical results when nothing has changed. |

---

## 5. Scoring specification

All constants below are config values; the defaults are shown. Ranks are dense among eligible members (equal values share a rank).

### 5.1 Daily task points

For each solved attempt:

```
solve_rank     = rank by solve duration among members who solved this task (shortest = 1)
base           = max(30, 100 − 10 × (solve_rank − 1))

runtime_rank   = rank by timeConsumedMillis of the AC among solvers (lowest = 1)
runtime_bonus  = 10 if runtime_rank = 1, 6 if 2, 3 if 3, else 0

penalty        = min(20, 5 × rejected_submissions)

task_points    = max(10, base + runtime_bonus − penalty)
```

Unsolved or not-started tasks score 0. There is no penalty for skipping a day other than losing the streak.

### 5.2 Project points

An entry is **qualified** if its writeup score is at least the pass threshold (default 40). Unqualified entries score 0 and are excluded from submission ranking, so they do not push anyone else down.

```
submit_rank      = rank by submitted_at among qualified entries (earliest = 1)
speed_points     = max(90, 300 × 0.85^(submit_rank − 1))
writeup_factor   = writeup_score / 100
project_points   = round(speed_points × writeup_factor × history_factor)
```

Speed points by rank: 1 → 300, 2 → 255, 3 → 217, 4 → 184, 5 → 157, 6 → 133, 7 → 113, 8 → 96, 9+ → 90.

Because points are multiplied, submitting early only pays off if the member can also explain their code and showed steady progress.

### 5.3 Writeup grading

All checks are plain text rules. No AI model is used.

**Text processing.** A *word* is a whitespace-separated token containing at least one letter. Matching is case-insensitive.

**Identifier extraction** (done at submission from the frozen SHA):

- Source files are those with an allowed extension (`.py .js .ts .jsx .tsx .java .c .h .cpp .hpp .cc .cs .go .rs .rb .php .kt .swift .lua .dart`), excluding paths containing `test`, `vendor`, `node_modules`, `dist`, `build`, or `.min.`, and files over 200 KB.
- Repo identifiers are names defined in source files, found by regex patterns per language family: Python `def`/`class`; JS/TS `function`, `class`, `const/let/var name =`, and method definitions; Go `func`/`type`; Rust `fn`/`struct`/`enum`/`trait`; C-family function definitions and `class`/`struct`/`enum`. Source file names without extensions are also included.
- Identifiers shorter than 3 characters, language keywords, and a denylist of generic names (`main init self this test data value args temp tmp item result run get set new`) are excluded.
- An identifier counts as mentioned if it appears verbatim, or if its split form appears as consecutive words (`check_winner` or `checkWinner` matches "check winner").

**Snippet selection.** Choose a source file with at least 30 non-blank lines, weighted by its non-blank line count. In it, choose a 15-line window that starts at a definition and contains at least 10 non-blank lines and at least 2 repo identifiers. Use a random generator seeded with the entry ID and frozen SHA so the choice is reproducible. If no window qualifies, use any 15-line window with at least 10 non-blank lines. *Snippet identifiers* are the repo identifiers appearing in the snippet.

**Minimum lengths:** Q1 80 words, Q2 100 words, Q3 60 words, Q4 60 words.

**Gate.** The writeup score is 0 if any answer has fewer than 20 words, if the total is under 150 words, or if any two answers have word-set Jaccard similarity above 0.8 (copying one answer into another).

**Score (0–100)** is the sum of four categories:

| Category | Max | Rule |
| --- | --- | --- |
| Completeness | 20 | 20 × the average over the four answers of min(1, words ÷ minimum). |
| Grounding | 30 | 15 × min(1, distinct snippet identifiers mentioned in Q1 ÷ 3) + 15 × min(1, distinct non-snippet repo identifiers mentioned in Q2–Q4 ÷ 6). If the snippet has fewer than 3 identifiers, the first divisor is that number (minimum 1). |
| Change relevance | 25 | 25 × (keyword groups matched in Q3 ÷ total keyword groups). The admin defines groups per project; a group is matched if any of its terms appears at the start of a word or as a phrase. |
| Writing quality | 25 | **Variety (10):** 10 × min(1, average type-token ratio of each answer's first 100 words ÷ 0.55). **Identifier density (10):** 10 if identifier tokens are ≤ 20% of all words, 0 if ≥ 45%, linear between. **Sentences (5):** 5 if average sentence length is 6–35 words and no sentence exceeds 80 words, otherwise 0. |

Identifier density is there so that listing names from the code without explaining them scores worse than writing sentences that use them.

Example keyword groups for a tic-tac-toe undo question:

```toml
change_keyword_groups = [
  ["undo"],
  ["stack", "history", "list of moves", "previous move"],
  ["pop", "remove", "revert", "restore"],
  ["board", "grid"],
]
```

### 5.4 Commit history factor

"Source lines" means non-blank lines in source files as defined in 5.3. Snapshots are taken at registration, daily during the window, and at submission. An **active interval** is a pair of consecutive snapshots where the HEAD SHA changed and source lines changed.

```
history_factor = min(1.0, 0.4 + 0.2 × active_intervals)

if the largest single-interval increase in source lines > 70% of final source lines:
    history_factor = history_factor × 0.75

history_factor = max(0.5, history_factor)
```

So a project pushed all at once on submission day scores 0.5, and work spread over three or more days with no single huge dump scores 1.0.

### 5.5 Leaderboard tiebreakers

When points are equal, rank by, in order:

1. more daily tasks solved in the period;
2. lower total solve duration over those tasks;
3. whoever reached the tied point total first.

### 5.6 Worked examples

**Daily task.** Rating 1000. Three members solve it.

| Member | Solve time | Runtime | Rejected | Base | Bonus | Penalty | Points |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ali_k | 18:40 | 46 ms | 1 | 100 | +6 | −5 | **101** |
| noor | 25:00 | 31 ms | 0 | 90 | +10 | 0 | **100** |
| zaid99 | 40:00 | 46 ms | 0 | 80 | +6 | 0 | **86** |

**Project.** Four entries. The earliest submitter's writeup scores 35, below the threshold, so they score 0 and drop out of the ranking. noor submitted second overall, so she ranks 1st among qualified entries: 300 speed points × 0.72 writeup × 1.0 history = **216**.

---

## 6. Data model

```
users            id, cf_handle (unique), cf_rating, cf_rank, is_admin, banned,
                 joined_at, rating_synced_at

tokens           id, user_id, token_hash, created_at, revoked_at

auth_challenges  id, cf_handle, contest_id, problem_index, issued_at, expires_at, used_at

daily_tasks      id, task_date (unique), contest_id, problem_index, name, rating,
                 posted_at, closes_at, finalized_at

task_attempts    id, user_id, task_id, started_at, accepted_at, submission_id,
                 runtime_ms, rejected_count, solve_seconds, points, provisional
                 UNIQUE (user_id, task_id)

projects         id, title, spec_md, opens_at, closes_at, change_question,
                 change_keyword_groups (JSON), finalized_at

project_entries  id, project_id, user_id, repo_url, verify_code, verified_at,
                 registered_at, submitted_at, frozen_sha, history_factor,
                 submit_rank, speed_points, points, status
                 UNIQUE (project_id, user_id), UNIQUE (repo_url)

repo_snapshots   id, entry_id, taken_at, kind (registration|daily|submission),
                 head_sha, source_loc

writeups         id, entry_id (unique), snippet_path, snippet_start, snippet_end,
                 snippet_text, snippet_identifiers (JSON), repo_identifiers (JSON),
                 questions (JSON), answers (JSON), started_at, deadline,
                 last_saved_at, submitted_at, auto_submitted, score,
                 breakdown (JSON)

audit_log        id, at, admin_id, action, target, reason
```

---

## 7. Server API

All endpoints are under `/api/v1`, use JSON, and except `/auth/*` and `/time` require `Authorization: Bearer <token>`.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/time` | Server time, used by the client to correct its clock. |
| POST | `/auth/challenge` | Body `{cf_handle}`. Returns challenge problem URL and expiry. |
| POST | `/auth/verify` | Body `{cf_handle}`. Checks the challenge; returns `{token}`. |
| GET | `/me` | Current member and any open writeup session (so the client can lock immediately). |
| GET | `/leaderboard?period=week\|all` | Ranked rows with provisional flags. |
| GET | `/users/{handle}` | Profile data. |
| GET | `/tasks/today` | Date, rating, close time, and the member's attempt. Problem details only if started. |
| POST | `/tasks/today/start` | Creates the attempt; returns problem details and `started_at`. |
| GET | `/projects/current` | Open project spec, window, and the member's entry status. |
| POST | `/projects/{id}/register` | Body `{repo_url}`. Returns verification code. |
| POST | `/projects/{id}/register/verify` | Checks the `.gcboard` file and size limit. |
| POST | `/projects/{id}/submit` | Freezes the entry and starts the writeup. Returns questions, snippet, deadline. |
| GET | `/writeups/current` | Open session: questions, snippet, deadline, last accepted answers. |
| PUT | `/writeups/current/draft` | Body `{answers: [q1, q2, q3, q4]}`. Rate-checked save (FR-WRITE-6). `409` returns last accepted text. |
| POST | `/writeups/current/submit` | Final submit; returns score and breakdown. |
| GET | `/rules` | Current scoring config, for the rules screen. |

Errors use standard status codes with body `{error, message}`. Each token is rate-limited (default 60 requests per minute).

---

## 8. Scheduled jobs

All jobs are idempotent, and missed runs execute once on server restart.

| Job | Schedule | Action |
| --- | --- | --- |
| post_daily_task | daily at post time | Pick per FR-TASK-3/4 and create the task. On Codeforces failure retry every 5 minutes for up to 6 hours, then skip the day and log it. |
| verify_attempts | every 2 minutes | For started, unsolved attempts on the open task, fetch recent `user.status` and record ACs per FR-TASK-7/8. Recalculate provisional points. |
| close_daily_task | at close time | Run one final successful verification pass, then finalize task points and streaks. |
| sweep_writeups | every minute | Auto-submit and grade sessions past their deadline. |
| open_close_projects | every minute | Open and close projects at their configured times. |
| snapshot_repos | daily at midday | Snapshot every verified entry of the open project. |
| finalize_project | after close and once all writeups have ended | Compute history factors, ranks, and points; mark final. |
| sync_ratings | daily | Update Codeforces rating and rank via `user.info` (batched). |
| backup_db | daily | Online SQLite backup; keep 14 copies. |

---

## 9. Client interface

### 9.1 Visual rules

- Monospace, terminal look: dark background, green accent, gray secondary text.
- Plain ASCII and box-drawing characters only. No emoji or icon fonts.
- Fully usable by keyboard. Every screen shows its keys in the footer.

### 9.2 Screens

| Screen | Contents |
| --- | --- |
| Login | Handle input → challenge problem and a 5-minute countdown → verify. |
| Leaderboard (home) | Week / all-time toggle; table of rank, handle, points, solved, streak. Enter opens a profile. |
| Today | Before start: date, rating, close time, "problem hidden", start key with confirmation. After start: problem name, open-in-browser key, live timer, status (waiting for AC, or solved with time and provisional points). |
| Profile | Data from FR-PROF-1. |
| Project | Spec, window, entry status, register and verify flow, list of snapshots, submit key with a confirmation that warns it starts the 45-minute locked writeup. |
| Writeup | Locked screen: countdown, snippet panel, four answer tabs with per-answer word counts against minimums, submit key. No way to leave until submitted or the deadline passes. |
| Rules | Every formula and current config value from section 5. |

### 9.3 Keys

| Key | Action |
| --- | --- |
| `l` | Leaderboard |
| `t` | Today's task |
| `p` | Project |
| `m` | My profile |
| `?` | Rules |
| `r` | Refresh |
| `w` | Toggle week / all-time (leaderboard) |
| `s` | Start task (Today) |
| `o` | Open problem in browser (Today) |
| `tab` / `shift+tab` | Next / previous answer (Writeup) |
| `ctrl+s` | Submit writeup |
| `q` | Quit (disabled during a writeup) |

### 9.4 Leaderboard mockup

```
> leaderboard --week                          week 46 · ends Sun 23:59
┌────┬──────────────┬────────┬────────┬────────┐
│ #  │ handle       │ pts    │ solved │ streak │
├────┼──────────────┼────────┼────────┼────────┤
│ 1  │ ali_k        │ 512    │ 5      │ 5d     │
│ 2  │ noor         │ ~489   │ 5      │ 12d    │
│ 3  │ zaid99       │ 301    │ 3      │ 0d     │
└────┴──────────────┴────────┴────────┴────────┘
 l board  t today  p project  m me  ? rules  w all-time  q quit
```

---

## 10. Non-functional requirements

| ID | Requirement |
| --- | --- |
| NFR-COMPAT-1 | The client shall work in every terminal listed in 2.3 at 80×24 or larger, and show a "please enlarge your terminal" notice below that. |
| NFR-COMPAT-2 | The client shall remain readable with 256 and 16 colors, and shall honor the `NO_COLOR` environment variable. |
| NFR-INSTALL-1 | Members shall install with one command (`uv tool install git+<repo>` or `pipx install git+<repo>`) and launch with `gcboard`. |
| NFR-INSTALL-2 | The client shall check the server's minimum supported client version and tell the member to update when needed. |
| NFR-PERF-1 | Screens shall load in under 1 second on a normal connection. Open screens refresh every 30 seconds. |
| NFR-SEC-1 | All traffic shall use HTTPS. Tokens are stored hashed. The GitHub token never leaves the server. |
| NFR-SEC-2 | The server shall never run member code or follow instructions found in repo contents. |
| NFR-SEC-3 | All inputs shall be validated and size-limited (handles, URLs, answers). |
| NFR-REL-1 | Codeforces or GitHub outages shall delay verification or snapshots but never change a score, since scores use source timestamps and frozen data. |
| NFR-REL-2 | Daily backups, 14 retained; a restore procedure shall be documented and tested. |
| NFR-MAINT-1 | The scoring module shall be pure functions with unit tests covering every formula and every worked example in 5.6. |
| NFR-MAINT-2 | All tunable values shall live in `config.toml`. |
| NFR-PRIV-1 | The server stores only Codeforces handles, repo URLs, snapshots, and writeups. A member may ask the admin to delete their data. |

---

## 11. Failure handling and edge cases

| Situation | Behavior |
| --- | --- |
| Codeforces down at post time | Retry for 6 hours; then the day has no task and no one loses their streak for it. |
| Codeforces down at task close | Finalization waits for one successful verification pass, so ACs made before close still count. |
| Member solves on a different handle or a mirror contest | Not counted (FR-TASK-7). |
| Member starts a task minutes before close | Allowed; they have only the remaining time. |
| Admin rerolls after someone started | Not allowed (FR-ADMIN-1). |
| Repo made private or deleted after submission | No effect; scoring uses data captured at submission. |
| Repo made private or deleted before submission | Snapshot fails; the entry is marked unreachable and cannot be submitted until fixed. |
| Force-push rewrites history during the window | Snapshots keep the server's observations, so earlier progress still counts and a dump still shows as one large interval. |
| App closed or crashes during a writeup | Session continues; reopening resumes it (FR-WRITE-7). |
| Member never returns to the writeup | Auto-submitted at the deadline with the last saved text. |
| Network drops during a writeup | Client keeps the text locally and retries saves; the server deadline still applies. |
| Clock on member's machine is wrong | Timers use server time via `/time` offset. |
| Member joins mid-week | They appear on the board once they score. |

---

## 12. Known limitations

These are accepted trade-offs of a fully automatic system with no human review:

- **Nothing stops typing answers from another device.** A member could read AI output on a phone and type it in. The random snippet, code-specific questions, and time limit make this slow and awkward but not impossible.
- **Writeup grading measures grounding and effort, not true understanding.** The rules check that answers are long enough, refer to the member's real code, address the change question, and read like sentences. A determined member could satisfy them without fully understanding their code.
- **Commit history can be staged.** Someone could push AI-generated code in small pieces over several days. The history factor rewards steady progress but cannot prove who wrote it.
- **Identifier extraction is regex-based** and may miss definitions in unusual code styles or less common languages.
- **English is assumed** for writeup heuristics.

---

## 13. Milestones

| Milestone | Delivers |
| --- | --- |
| M1 — Core | Server skeleton, login challenge, daily task posting, start/verify, task scoring, weekly leaderboard, client screens: login, leaderboard, today. |
| M2 — Profiles | Profiles, streaks, all-time board, rating sync, rules screen, admin CLI basics. |
| M3 — Projects | Project schedule, repo registration and verification, snapshots, history factor, project screen. |
| M4 — Writeups | Snippet selection, identifier extraction, writeup screen and lock, rate-checked autosave, grading, project scoring and finalization. |
| M5 — Hardening | Backups, retries, client version check, terminal compatibility pass across 2.3, full scoring test suite. |

---

## 14. Configuration defaults and open questions

```toml
[general]
timezone = "TBD"                 # group time zone (IANA name)
daily_post_time = "00:00"

[tasks]
rating_by_weekday = [800, 900, 1000, 1100, 1200, 1300, 1400]  # Mon..Sun
rating_fallback_steps = [100, 200]
excluded_tags = ["*special", "interactive"]
base_max = 100
base_step = 10
base_min = 30
runtime_bonus = [10, 6, 3]
penalty_per_reject = 5
penalty_cap = 20
min_task_points = 10

[projects]
length_days = 14
max_registration_loc = 50
speed_max = 300
speed_decay = 0.85
speed_min = 90
pass_threshold = 40

[writeup]
time_limit_minutes = 45
min_words = [80, 100, 60, 60]
autosave_seconds = 10
max_chars_per_second = 25
burst_allowance_chars = 50
max_answer_chars = 10000

[history]
base = 0.4
per_active_interval = 0.2
dump_threshold = 0.70
dump_multiplier = 0.75
floor = 0.5
```

Open questions to settle before M1:

1. Which time zone and post time suit the group best?
2. Should projects outweigh daily tasks more or less than the defaults? (Two weeks of perfect tasks is about 1,500 points; a top project is 300.)
3. Should project languages be restricted to the ones the identifier extractor handles best?
4. Where will the server be hosted, and who besides the admin can access it?