# External news-update scheduler

This optional Cloudflare Worker provides a scheduler independent of GitHub Actions cron events.

## 1. Create the GitHub token

Create a **fine-grained personal access token** in GitHub:
- Resource owner: the owner of this repository.
- Repository access: only `african-security-observer`.
- Repository permissions: **Actions — Read and write**.
- Do not grant Contents, Administration, or other permissions.
- Set an expiry and rotate the token periodically.

Never paste the token into this repository, a public file, or a chat.

## 2. Create the Cloudflare Worker

1. In Cloudflare, create a Worker (for example, `aso-news-scheduler`).
2. Paste the contents of `cloudflare_worker.js` into its editor and deploy it.
3. In Worker Settings → Variables and Secrets, add a secret named `GITHUB_TOKEN` with the token created above.
4. Add a Cron Trigger: `*/15 * * * *` (every 15 minutes, UTC).
5. Check Worker Logs after the first scheduled invocation.

The Worker reads the public `news.json` timestamp. If it is older than 25 minutes, it checks for an active updater run and dispatches `update-news.yml` only when no run is queued or running.

## 3. Verify operation

After deployment, observe at least 3–4 scheduled cycles. Confirm both:
- Worker logs show either fresh-data skips or accepted dispatches.
- GitHub Actions shows corresponding `workflow_dispatch` runs, and `news.json` receives a newer `updated` timestamp.

The existing GitHub Actions schedule and in-repository watchdog remain as additional layers. The external Worker is the independent trigger.
