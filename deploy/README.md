# Deploying AMCrawler on Unraid

AMCrawler runs as one Docker container on the NAS, behind the existing swag
reverse proxy, published through the existing Cloudflare Tunnel and protected by
Cloudflare Access (Google login). Same pattern as the other self-built bots:
image built by compose, `proxy` network, data under `/mnt/user/appdata/<app>/data`,
`restart: unless-stopped`.

Placeholders used below: the site is `viaggi.<your-domain>` (for example
`viaggi.example.com`); the Owner is `owner@example.com`; the maintainer is
`maintainer@example.com`.

Prerequisites: swag (config in `/mnt/user/appdata/swag`) and cloudflared already
running on the Docker network `proxy`; Cloudflare Zero Trust Access already in use.

## 1. Create a private GitHub repository and push

From the development PC, in the repo folder:

```bash
git remote add origin git@github.com:<your-user>/amcrawler.git
```

```bash
git push -u origin main
```

## 2. Clone on the NAS

SSH into the NAS. For a private repo, add a read-only deploy key to the
repository first.

```bash
mkdir -p /mnt/user/appdata/amcrawler
```

```bash
git clone git@github.com:<your-user>/amcrawler.git /mnt/user/appdata/amcrawler/app
```

## 3. Create the data folder

The container runs as uid 99 / gid 100 (Unraid's `nobody:users`):

```bash
mkdir -p /mnt/user/appdata/amcrawler/data
```

```bash
chown 99:100 /mnt/user/appdata/amcrawler/data
```

Keep SQLite on a cache-only (exclusive) appdata share. If `/mnt/user/appdata`
is not exclusive (it goes through Unraid's FUSE `shfs` layer), mount
`/mnt/cache/appdata/amcrawler/data` instead of the `/mnt/user/...` path in
`docker-compose.yml` (and create and `chown` that folder): SQLite in WAL mode is
unreliable over FUSE and can corrupt or lock the database.

## 4. Create `.env`

```bash
cd /mnt/user/appdata/amcrawler/app
```

```bash
cp .env.example .env
```

Edit `.env` and fill in every value (`AMC_PUBLIC_URL`, `SMTP_*`, `DIGEST_*`).
`CF_ACCESS_TEAM_DOMAIN` and `CF_ACCESS_AUD` come from step 7. Leave
`AMC_DEV_NO_AUTH=0`. `.env` is gitignored: never commit it.

## 5. Gmail app password

The Digest is sent through Gmail SMTP (`smtp.gmail.com`, port `587`) using the
maintainer account:

1. Google Account, Security, turn on 2-Step Verification.
2. Google Account, Security, 2-Step Verification, App passwords.
3. Create one named `AMCrawler` and copy the 16-character password.
4. Put it in `.env` as `SMTP_PASSWORD`; set `SMTP_USER` and `DIGEST_FROM` to
   `maintainer@example.com`, and `DIGEST_TO` to `owner@example.com`.

## 6. swag proxy conf

```bash
cp /mnt/user/appdata/amcrawler/app/deploy/swag/viaggi.subdomain.conf.sample /mnt/user/appdata/swag/nginx/proxy-confs/viaggi.subdomain.conf
```

```bash
docker restart swag
```

The container name `amcrawler` and port `8000` in the conf are resolved over
the `proxy` network, so nothing is published on the host.

## 7. Cloudflare Tunnel and Access

1. Zero Trust, Networks, Tunnels, open the existing tunnel, Public Hostname,
   Add: hostname `viaggi.<your-domain>`. For the service, copy exactly what your
   existing hostnames already use to reach swag (same type and URL).
2. Zero Trust, Access, Applications, Add an application, Self-hosted:
   - Application domain: `viaggi.<your-domain>`
   - Identity provider: reuse the existing Google provider (no new one needed)
   - Session duration: 1 month
   - Cookie settings: SameSite = Lax (CSRF protection for the POST routes)
   - Policy: action Allow, Include, Emails: `owner@example.com` and
     `maintainer@example.com`
3. Open the saved application and copy the Application Audience (AUD) tag.
   Your team domain is `<team>.cloudflareaccess.com` (shown in Zero Trust,
   Settings, Custom Pages).
4. Put them in `.env`:
   `CF_ACCESS_AUD=<AUD tag>` and `CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com`.

## 8. Start

```bash
cd /mnt/user/appdata/amcrawler/app
```

```bash
docker compose up -d --build
```

## 9. Verify

Run a crawl by hand (exit code 0 means success):

```bash
docker exec amcrawler python -m amcrawler crawl
```

Check the container is healthy:

```bash
docker ps --filter name=amcrawler
```

Then open `https://viaggi.<your-domain>` on the phone, sign in with Google, and
use the browser menu "Aggiungi a schermata Home" to install it. A request that
reaches the container without passing Cloudflare Access gets `403 Accesso negato`.

To send a test Digest use Impostazioni, "Invia prova": it mails a `[PROVA]`
copy and changes nothing.

The shell command below is **not** a test. It is the real daily run: it uses
today's slot and records a baseline, sent or empty Digest. Run it only to send
today's Digest now, and only if it has not run yet today:

```bash
docker exec amcrawler python -m amcrawler digest
```

## 10. Update

```bash
cd /mnt/user/appdata/amcrawler/app
```

```bash
git pull && docker compose up -d --build
```

The database lives in `/mnt/user/appdata/amcrawler/data` and survives rebuilds.
Back it up by copying `amcrawler.db` from that folder.

## Troubleshooting

```bash
docker logs --tail 100 amcrawler
```

- 502 from swag: the container is not running or not on the `proxy` network.
- `403 Accesso negato` when signed in: `CF_ACCESS_AUD` or
  `CF_ACCESS_TEAM_DOMAIN` in `.env` is wrong; fix it, then
  `docker compose up -d`.
- Permission errors on the database: re-run the `chown 99:100` from step 3.
