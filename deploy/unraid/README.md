# Running AMCrawler from the Unraid template (auto-updating)

Every push to `main` on GitHub runs the tests and publishes
`ghcr.io/dpremoli/amcrawler:latest` (workflow `.github/workflows/image.yml`).
The Unraid template in this folder runs that image, so Unraid can update it like
any other container. swag, Cloudflare Tunnel and Access stay exactly as in
`deploy/README.md`; only how the container is created changes.

The repository is private but the image package is public, so the NAS needs no
credentials. The image holds code only: hostnames, addresses, passwords and the
Access AUD tag are entered in the Unraid form and stay on the NAS.

## 1. Make the image public (once)

On GitHub: your profile, Packages, `amcrawler`, Package settings, Danger Zone,
Change visibility, Public. Check from the NAS:

```bash
docker pull ghcr.io/dpremoli/amcrawler:latest
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

The icon is copied by hand because Unraid cannot download it from the private repository.

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

## Releasing a change

From the PC: `git push origin main`. GitHub Actions builds the new image in about
a minute (Actions tab), and the NAS picks it up at the next update check.
