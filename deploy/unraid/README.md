# Running AMCrawler from the Unraid template (auto-updating)

Every push to `main` on GitHub runs the tests and publishes
`ghcr.io/dpremoli/amcrawler:latest` (workflow `.github/workflows/image.yml`).
The Unraid template in this folder runs that image, so Unraid can update it like
any other container. swag, Cloudflare Tunnel and Access stay exactly as in
`deploy/README.md`; only how the container is created changes.

The package is private, so the NAS has to log in to ghcr.io once.

## 1. Log the NAS in to ghcr.io

On GitHub: Settings, Developer settings, Personal access tokens, Tokens (classic),
Generate new token. Tick only `read:packages`, no expiry or a long one. Copy it.

On the NAS:

```bash
docker login ghcr.io -u dpremoli
```

Paste the token as the password. Unraid keeps `/root` in RAM, so save the login
on the flash drive and restore it at every boot:

```bash
cp /root/.docker/config.json /boot/config/ghcr-docker-config.json
```

```bash
printf '\nmkdir -p /root/.docker && cp /boot/config/ghcr-docker-config.json /root/.docker/config.json\n' >> /boot/config/go
```

## 2. Install the icon and the template

From the repo checkout on the NAS (`/mnt/user/appdata/amcrawler/app`, after `git pull`):

```bash
cd /mnt/user/appdata/amcrawler/app/deploy/unraid
```

```bash
cp amcrawler-icon.png /boot/config/plugins/dockerMan/images/amcrawler-icon.png && mkdir -p /var/lib/docker/unraid/images && cp amcrawler-icon.png /var/lib/docker/unraid/images/amcrawler-icon.png
```

```bash
cp amcrawler.xml /boot/config/plugins/dockerMan/templates-user/my-amcrawler.xml
```

The icon is copied by hand because Unraid cannot download it from a private repository.

## 3. Swap the compose container for the template one

Keep your current values to hand (they go into the Unraid form):

```bash
cat /mnt/user/appdata/amcrawler/app/.env
```

Stop and remove the compose container (the database in `/mnt/user/appdata/amcrawler/data` is kept):

```bash
cd /mnt/user/appdata/amcrawler/app && docker compose down
```

In the Unraid web UI: Docker, Add Container, Template: `amcrawler`. Fill every
field from `.env`, check Network type is `proxy` and the Data path is
`/mnt/user/appdata/amcrawler/data`, then Apply. Unraid pulls the image and
starts it with the name `amcrawler`, so swag finds it as before.

Check it:

```bash
docker exec amcrawler python -m amcrawler crawl
```

## 4. Automatic updates

Unraid's Docker tab shows "update ready" when a new `:latest` is published. To
apply it without clicking, install **CA Auto Update Applications** from Apps,
then in its settings turn auto-update on for `amcrawler` (for example daily).

If the Docker tab shows the update status as "not available", Unraid could not
read the private registry: check step 1 (`docker pull ghcr.io/dpremoli/amcrawler:latest`
must work on the NAS).

## Releasing a change

From the PC: `git push origin main`. GitHub Actions builds the new image in a few
minutes (Actions tab), and the NAS picks it up at the next update check.
