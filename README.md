# Shoco public site

Static public site and launch-list endpoint for `getshoco.com`.

The site is served by Nginx. The same-origin signup endpoint stores normalized
email addresses in SQLite at `/data/mailing-list.sqlite3`; the database is
held in the named Docker volume `public-site_mailing_list_data`. Email
addresses are never written to container logs.

## Run

```bash
docker compose up -d --build
```

The `mailing-list` service includes a honeypot field, basic email validation,
duplicate suppression, and in-memory request throttling. Cloudflare Turnstile
support is available through `TURNSTILE_SECRET_KEY` once the matching widget
site key is added to the form.
