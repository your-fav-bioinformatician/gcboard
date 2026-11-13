# Running the gcboard server

This guide covers deploying, backing up, restoring, and upgrading the server.

## Deploy

On a small Linux host with a public domain name:

```bash
sudo useradd --system --create-home --home-dir /srv/gcboard gcboard
sudo -iu gcboard
git clone https://github.com/your-fav-bioinformatician/gcboard app && cd app
uv sync --extra server
cp config.example.toml /srv/gcboard/config.toml
```

Edit `/srv/gcboard/config.toml`:

- `[general] timezone` and `daily_post_time` for your group.
- `[server] admins` with the admin's Codeforces handle.
- `[server] database = "/srv/gcboard/gcboard.db"`. Backups go to `/srv/gcboard/backups`
  by default, next to the database.

Check the config:

```bash
uv run gcboard-admin --config /srv/gcboard/config.toml validate-config
```

Create a fine-grained GitHub token with read-only access to public repositories.
Put it in `/srv/gcboard/env` as `GCBOARD_GITHUB_TOKEN=github_pat_...`, readable only
by the `gcboard` user (`chmod 600`). The token never leaves the server.

Install the service from [examples/gcboard.service](../examples/gcboard.service):

```bash
sudo cp examples/gcboard.service /etc/systemd/system/
sudo systemctl enable --now gcboard
journalctl -u gcboard -f
```

Put Caddy in front for HTTPS (`/etc/caddy/Caddyfile`):

```
gcboard.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

Members then run `export GCBOARD_SERVER=https://gcboard.example.org`, or put
`server_url` in their client config, and start `gcboard`.

## What runs on its own

All jobs run from the server process. They are idempotent, and anything missed
while the server was down runs on the next start.

| Job | When | Notes |
| --- | --- | --- |
| Post daily task | at `daily_post_time` | Retries every 5 minutes for 6 hours if Codeforces is down, then skips the day. |
| Verify ACs | every 2 minutes | |
| Close daily task | after close | Waits for one successful Codeforces pass. |
| Sync ratings | hourly, for ratings over a day old | |
| Repo snapshots | daily after `snapshot_time` (12:00) | Retries a failed repo after 30 minutes. |
| Auto-submit writeups | every minute | |
| Finalize projects | after close, once every writeup has ended | |
| Back up the database | daily after `backup_time` (03:00) | Keeps the newest `backup_keep` (14). |

## Backups

Backups use SQLite's online backup API, so they are consistent while the server
runs. Each one is checked with `PRAGMA integrity_check` right after it is written.

```bash
uv run gcboard-admin --config /srv/gcboard/config.toml backups   # list
uv run gcboard-admin --config /srv/gcboard/config.toml backup    # take one now
```

Copy the backup folder off the machine too, for example with a nightly `rsync`.
A backup on the same disk doesn't survive that disk.

## Restore

This procedure is exercised by `tests/test_backup.py`.

1. Stop the server: `sudo systemctl stop gcboard`.
2. Pick a backup from `gcboard-admin backups`.
3. Restore it:

   ```bash
   uv run gcboard-admin --config /srv/gcboard/config.toml restore /srv/gcboard/backups/gcboard-2026-11-20.db --yes
   ```

   The backup is checked first. If it isn't an intact gcboard database, nothing
   changes. The current database is kept as `gcboard.db.pre-restore-<timestamp>`,
   so a restore can be undone the same way.
4. Start the server: `sudo systemctl start gcboard`.

The restore is written to the audit log. Anything that happened after the backup
was taken (logins, solves, saved writeup text) is lost. Members may need to log in
again if their token is newer than the backup.

## Deleting a member's data

A member may ask the admin to delete everything gcboard stores about them
(NFR-PRIV-1). First see what that covers:

```bash
uv run gcboard-admin --config /srv/gcboard/config.toml delete-member HANDLE --reason "asked by email"
```

Then delete it:

```bash
uv run gcboard-admin --config /srv/gcboard/config.toml delete-member HANDLE --reason "asked by email" --yes
```

This deletes the member's account, login tokens and challenges, task attempts,
project entries, repo snapshots, and writeups. It is safe to run while the server
is up. Everyone else's scores are recomputed as if the member never took part, so
ranks on past tasks, projects, and weekly boards may shift.

The audit log keeps its entries, but the member's handle in them is replaced with
`deleted member #<id>`. Backups taken before the deletion still contain the data
until they age out, which takes `backup_keep` days (14 by default).

To remove someone from the boards without deleting their data, use `ban` instead.

## Upgrade

1. Take a backup: `gcboard-admin backup`.
2. `git pull && uv sync --extra server`.
3. `sudo systemctl restart gcboard`.

The server checks the database schema at startup. If a new version needs columns
the database doesn't have, it refuses to start and says which ones. Restore the
backup and stay on the old version until there is a migration for it.

If an upgrade changes the API, raise `[server] min_client_version`. Older
clients are then told to run `uv tool upgrade gcboard` (or `pipx upgrade gcboard`).

## When things go wrong

| Situation | What happens |
| --- | --- |
| Codeforces down | Verification and posting retry. Scores use Codeforces' own timestamps, so nothing changes once it's back. |
| GitHub down | Registration and submission show "GitHub is not responding". Snapshots retry. |
| A member's repo goes private | Their entry shows "unreachable" until it's public again, and it can't be submitted until then. |
| Server down during a writeup | The deadline still runs. Reopening the client resumes the session, and expired sessions are auto-submitted at startup. |
| A wrong score | Fix the cause (void an attempt, ban, edit config), then run `gcboard-admin recompute`. It rebuilds every score from raw data. |
