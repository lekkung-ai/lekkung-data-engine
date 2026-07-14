# External trigger for the daily pipeline (cron-job.org)

## Why

GitHub Actions' own `schedule` trigger (`cron: '11 11 * * 1-5'`, meant to fire
at 18:11 ICT) has been observed running several hours late — GitHub queues
scheduled workflows and doesn't guarantee on-time execution, especially
during platform load. The `schedule` trigger is kept in `daily-scan.yml` as a
last-resort safety net, but the primary trigger is now an external cron
service (cron-job.org) calling GitHub's REST API directly to fire
`workflow_dispatch` on time instead.

## What to set up

A cron-job.org job that sends one HTTP request on your desired schedule
(e.g. daily at 18:11 ICT). No response body is needed; a 204 means it queued
successfully.

### Endpoint

```
POST https://api.github.com/repos/lekkung-ai/lekkung-data-engine/actions/workflows/daily-scan.yml/dispatches
```

(`daily-scan.yml` is the workflow file name under `.github/workflows/` in this repo — replace only if the file gets renamed.)

### Headers

```
Accept: application/vnd.github+json
Authorization: Bearer <PAT>
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

`<PAT>` is a placeholder — put your own personal access token there when you
configure cron-job.org. **Never paste the real token into this file, into
chat, or into any committed config.**

### Body

For the normal daily run (respects the freshness guard — skips if today's
data is already in `lekkung-stockdesk`):

```json
{
  "ref": "main",
  "inputs": {
    "force": "false"
  }
}
```

For a manually-forced run (e.g. you want to re-run right now regardless of
freshness) — set up as a *separate*, manually-triggered cron-job.org job (or
just call this yourself with curl when needed), not part of the daily
schedule:

```json
{
  "ref": "main",
  "inputs": {
    "force": "true"
  }
}
```

Note: `inputs` values must be strings (`"false"` / `"true"`), not JSON
booleans — the GitHub API requires this for `workflow_dispatch` inputs
regardless of the `type: boolean` declared in the workflow file.

## PAT scope

Create a **fine-grained personal access token** (not classic) scoped to:

- **Repository access:** only `lekkung-ai/lekkung-data-engine` (no other repos)
- **Permissions:** Repository → **Actions** → Read and write (this is the only permission needed to dispatch a workflow)

Nothing else needs to be granted. Set an expiration you're comfortable
rotating on (GitHub fine-grained tokens max out at 1 year).

## Testing with curl

Replace `<PAT>` with your real token when running this yourself — never
share the filled-in command.

```bash
curl -i -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer <PAT>" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/lekkung-ai/lekkung-data-engine/actions/workflows/daily-scan.yml/dispatches \
  -d '{"ref":"main","inputs":{"force":"false"}}'
```

Expected response: `HTTP/2 204` with an empty body — this means GitHub
accepted the dispatch request and queued a run (not that the run itself
succeeded; check the Actions tab or `gh run list` for that).

If you get `404`, double-check the workflow file name in the URL and that
the token's repository access includes this repo. If you get `401`/`403`,
the token is missing, expired, or lacks the Actions read/write permission.

## cron-job.org setup notes

- Method: `POST`
- URL: the endpoint above
- Headers: the three listed above, added as custom headers in cron-job.org's job editor
- Body: the JSON body above, as raw request body (set request body type to JSON)
- Schedule: whatever time you want the daily run to fire (e.g. 18:11 ICT / Asia-Bangkok, or convert to the timezone cron-job.org expects if it doesn't support IANA timezone names directly — check its schedule editor)
- Save the token only inside cron-job.org's own job configuration (its custom headers field) — do not store it anywhere else.
