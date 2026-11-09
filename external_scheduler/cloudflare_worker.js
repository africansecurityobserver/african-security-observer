/**
 * Cloudflare Worker scheduled trigger for African Security Observer news updates.
 *
 * Configure a Cron Trigger to run every 15 minutes (UTC).
 * Store GITHUB_TOKEN as a Worker secret (never commit it).
 * Required secret scope: fine-grained PAT restricted to this repository,
 * with Actions: Read and write.
 */

const OWNER = "africansecurityobserver";
const REPO = "african-security-observer";
const WORKFLOW_FILE = "update-news.yml";
const BRANCH = "main";
const MAX_AGE_MINUTES = 25;

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

    const rawUrl = `https://raw.githubusercontent.com/${OWNER}/${REPO}/${BRANCH}/news.json`;
    const newsResponse = await fetch(rawUrl, { cf: { cacheTtl: 0, cacheEverything: false } });
    if (!newsResponse.ok) throw new Error(`Could not read news.json: HTTP ${newsResponse.status}`);
    const news = await newsResponse.json();
    const updatedAt = Date.parse(news.updated || "");
    const ageMinutes = Number.isFinite(updatedAt)
      ? (Date.now() - updatedAt) / 60000
      : Number.POSITIVE_INFINITY;

    console.log(`news.json age: ${ageMinutes.toFixed(1)} minutes`);
    if (ageMinutes >= 0 && ageMinutes <= MAX_AGE_MINUTES) {
      console.log("News data is fresh; skipping dispatch.");
      return;
    }

    const apiRoot = `https://api.github.com/repos/${OWNER}/${REPO}`;
    const runs = await githubRequest(
      `${apiRoot}/actions/workflows/${WORKFLOW_FILE}/runs?branch=${BRANCH}&per_page=5`,
      env.GITHUB_TOKEN
    );
    const active = (runs.workflow_runs || []).some(run =>
      ["queued", "in_progress", "waiting", "requested", "pending"].includes(run.status)
    );
    if (active) {
      console.log("A news update is already queued or running; skipping duplicate dispatch.");
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
    console.log("GitHub accepted the external news-update dispatch.");
  },
};
