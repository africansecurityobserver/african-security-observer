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
4. Add a Cron Trigger: `25,55 * * * *` (at minutes 25 and 55 of every hour, UTC).
5. Check Worker Logs after the first scheduled invocation.

The Worker checks the latest `update-news.yml` workflow attempt. If no attempt has started in the last 40 minutes, it checks for an active run and dispatches `update-news.yml` only when no run is queued or running. It deliberately does not use `news.json.updated`, because that timestamp is preserved when a successful refresh finds no new stories.

## 3. Verify operation

After deployment, observe at least 3–4 scheduled cycles. Confirm both:
- Worker logs show either a recent-run skip or an accepted recovery dispatch.
- GitHub Actions shows the corresponding `workflow_dispatch` run. `news.json.updated` changes only when the published news content changes, so an unchanged timestamp does not by itself indicate failure.

The primary GitHub Actions schedule runs at minutes 10 and 40. The in-repository watchdog and this optional Worker check at minutes 25 and 55 as recovery layers. If this Worker is already deployed, publish the updated `cloudflare_worker.js` and change its Cron Trigger to `25,55 * * * *` in the Cloudflare dashboard; changing the repository file alone does not redeploy a Worker.
