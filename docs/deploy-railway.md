# Deploying gcboard on Railway

Railway builds the repository's `Dockerfile`, runs one container, and keeps the
SQLite database on a volume. You need nothing else: Railway's domain provides
HTTPS, so Caddy isn't needed.

Railway runs exactly one copy of a service that has a volume. It never runs two
deployments on the same volume at once, which is what SQLite and gcboard's
built-in scheduler need. The cost is a few seconds of downtime on each redeploy.

## 1. Before you start

- Merge the open pull requests into `main`, in order, because Railway deploys
  from a branch. To try a branch first, pick it in step 2 instead of `main`.
- Create a fine-grained GitHub token with read-only access to public
  repositories. The server uses it to read members' project repos.

## 2. Create the service

1. In Railway: **New Project → Deploy from GitHub repo**, then pick
   `your-fav-bioinformatician/gcboard`.
2. Railway finds the `Dockerfile` and starts building.

## 3. Attach a volume

Open the command palette (Cmd/Ctrl+K) → **Add volume**, choose the gcboard
service, and set the mount path to **`/data`**.

The database (`/data/gcboard.db`) and daily backups (`/data/backups/`) go there
automatically. If no volume is attached, or the database is configured somewhere
else, the server refuses to start rather than lose data on the next redeploy.

## 4. Variables

In the service's **Variables** tab, add:

| Variable | Value |
| --- | --- |
| `GCBOARD_CONFIG_TOML` | The whole config file, as in the example below. Railway variables can span several lines. |
| `GCBOARD_GITHUB_TOKEN` | The GitHub token from step 1. |
| `PORT` | `8000` |

A minimal `GCBOARD_CONFIG_TOML`. Every other setting keeps its default from
[config.example.toml](../config.example.toml):

```toml
[general]
timezone = "UTC"            # your group's IANA time zone, e.g. "Asia/Baghdad"
daily_post_time = "00:00"

[server]
admins = ["your_codeforces_handle"]
```

Leave `database` out: it defaults to the volume.

## 5. Settings

In the service's **Settings**:

- **Networking → Generate Domain**, with port `8000`. You get a URL like
  `https://gcboard-production.up.railway.app`.
- **Deploy → Healthcheck path**: `/api/v1/time`.
- Keep **Serverless** (app sleeping) **off**. The server posts tasks, checks
  solves, and takes backups on its own schedule, so it has to stay awake.

Redeploy if Railway doesn't do it by itself. In **Deploy Logs** you should see
`Uvicorn running on http://0.0.0.0:8000`, and shortly after,
`posted task <date>: <problem> (<rating>)`.

## 6. Check it

```bash
curl https://YOUR-APP.up.railway.app/api/v1/time
```

Then install the client and point it at the server. Put this line in the client
config file (`~/.config/gcboard/config.toml` on Linux; see the README for macOS
and Windows):

```toml
server_url = "https://YOUR-APP.up.railway.app"
```

Run `gcboard` and log in with your real Codeforces handle.

## Admin commands

Install the [Railway CLI](https://docs.railway.com/cli), then run `railway login`
and `railway link` in any folder. Admin commands run inside the container:

```bash
railway ssh -- gcboard-admin users
railway ssh -- gcboard-admin audit
railway ssh -- gcboard-admin backups
```

To create a project, copy its files onto the volume, then create it there. The
SSH user is your service's domain:

```bash
scp myproject.toml myproject.md YOUR-APP.up.railway.app@ssh.railway.com:/data/projects/
railway ssh -- gcboard-admin project-create /data/projects/myproject.toml
```

If an admin command says `no config`, check that the service variables are
visible in the SSH session: `railway ssh -- env | grep GCBOARD`.

## Backups and restore

gcboard backs up to `/data/backups/` every day and keeps the newest 14. Copy one
off Railway now and then:

```bash
railway volume files download /backups/gcboard-2026-11-20.db ./gcboard-2026-11-20.db
```

Railway can also back up the whole volume (in the volume's **Backups** tab). That
is the simplest way to roll everything back.

A restore with `gcboard-admin restore` needs the server stopped, but SSH needs a
running container. So:

1. Temporarily set the start command (**Settings → Deploy**) to `sleep infinity`,
   and redeploy.
2. Restore: `railway ssh -- gcboard-admin restore /data/backups/gcboard-2026-11-20.db --yes`.
3. Clear the start command, and redeploy.

## Upgrades

Push to the deployed branch, and Railway rebuilds and redeploys. If a new version
needs database columns the database doesn't have, the server refuses to start and
the deploy logs list the missing columns. Roll back to the previous deployment in
Railway, and keep that version until there's a migration.
