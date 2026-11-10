/**
 * Cloudflare Worker backup trigger for African Security Observer news updates.
 *
 * Configure its Cron Trigger to run at minutes 25 and 55 of every hour:
 *   25,55 * * * *
 * These checks run between the primary GitHub Actions refreshes at :10 and :40.
 * Store GITHUB_TOKEN as a Worker secret (never commit it).
 * Required secret scope: fine-grained PAT restricted to this repository,
 * with Actions: Read and write.
 */

const OWNER = "africansecurityobserver";
const REPO = "african-security-observer";
const WORKFLOW_FILE = "update-news.yml";
const BRANCH = "main";
const MAX_RUN_AGE_MINUTES = 40;

async function githubRequest(url, token, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`GitHub API ${response.status}: ${body.slice(0, 500)}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

export default {
  async scheduled(controller, env, ctx) {
    if (!env.GITHUB_TOKEN) throw new Error("Missing GITHUB_TOKEN Worker secret.");

    const apiRoot = `https://api.github.com/repos/${OWNER}/${REPO}`;
    const result = await githubRequest(
      `${apiRoot}/actions/workflows/${WORKFLOW_FILE}/runs?branch=${BRANCH}&per_page=10`,
      env.GITHUB_TOKEN
    );
    const runs = result.workflow_runs || [];
    const active = runs.some(run =>
      ["queued", "in_progress", "waiting", "requested", "pending"].includes(run.status)
    );
    if (active) {
      console.log("A news update is already queued or running; skipping duplicate dispatch.");
      return;
    }

    // Do not use news.json.updated here: it intentionally remains unchanged
    // when a successful refresh finds no new stories.
    const latest = runs[0];
    const latestRaw = latest && (latest.run_started_at || latest.created_at);
    const latestAt = latestRaw ? Date.parse(latestRaw) : Number.NaN;
    const ageMinutes = Number.isFinite(latestAt)
      ? (Date.now() - latestAt) / 60000
      : Number.POSITIVE_INFINITY;

    console.log(`Latest news workflow attempt age: ${ageMinutes.toFixed(1)} minutes`);
    if (ageMinutes >= 0 && ageMinutes <= MAX_RUN_AGE_MINUTES) {
      console.log("A news refresh was attempted within the safety window; skipping dispatch.");
      return;
    }

    await githubRequest(
      `${apiRoot}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      env.GITHUB_TOKEN,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ref: BRANCH }),
      }
    );
    console.log("GitHub accepted the backup news-update dispatch.");
  },
};
