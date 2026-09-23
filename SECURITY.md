# Security

The only secret is `OPENROUTER_API_KEY`. Everything below exists to keep it
from leaking, and to cap the damage if it ever does.

## Limiting the damage (do this first)

- Create a **dedicated** OpenRouter key for this repo only.
- Give that key a **credit limit** (e.g. $2–5) in OpenRouter's key settings, and **turn off
  auto top-up**. A leaked key can then spend at most that amount. This limit is enforced by
  OpenRouter, outside anything this repo controls.
- Rotate the key if anything looks off; the old one is useless once revoked.

## Where the key lives

- It is stored only as a GitHub Actions secret in the `openrouter` **environment**. Restrict
  that environment's deployment branches to `main`, so workflows on other branches and pull
  requests can't read it. Pull requests from forks never receive secrets.
- The key is never in the repo, never in `config/`, never in a command-line argument, and never
  printed.

## Workflow isolation (`.github/workflows/daily.yml`)

| Job | Sees key | Can push | Runs third-party code |
|---|---|---|---|
| `pick` | yes (one step) | no (read-only token, `persist-credentials: false`) | no: stdlib Python only, nothing installed |
| `publish` | no | yes | no |
| `deploy` | no | Pages only | no |

- Top-level `permissions: {}`. Each job asks only for what it needs.
- Every action is pinned to a full commit SHA. Dependabot proposes updates.
- No workflow uses `pull_request_target`, and none puts untrusted text (issue titles, model
  output) into shell commands.
- The Tests workflow installs `pytest` but has no secrets and a read-only token.

## Code-level guards

- `lottery/` imports only the Python standard library. `test_no_third_party_imports` fails if
  that changes, because a compromised PyPI package in the key-holding job could read the key.
- `lottery.http.redact()` scrubs the key's value and any key-shaped string (`sk-…`,
  `Bearer …`) from error messages and from model output before anything is printed or written
  to disk. Models never see the key, but this also covers API error bodies.
- `test_no_secrets_in_repo` scans all tracked files for key patterns.
- Model output is untrusted. It is validated (JSON, integer ranges) and length-capped. The site
  escapes every string, and a Content-Security-Policy allows only same-origin scripts.

## Also turn on (repo Settings → Code security)

- Secret scanning and **push protection**, so GitHub blocks a push that contains a key.
- Dependabot alerts.

## Reporting

Open a private security advisory on this repository.
