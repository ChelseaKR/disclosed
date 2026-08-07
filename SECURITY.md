# Security policy

## Supported versions

There are no tagged releases yet. The tip of the default branch is the only supported version;
fixes land there and nowhere else.

| Version | Supported |
| --- | --- |
| default branch (pre-release, 0.1.0.dev0) | Yes |
| anything else | No |

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository ("Report a
vulnerability" under the Security tab) rather than a public issue. You will get an
acknowledgement within 72 hours.

## What counts as a vulnerability here

The project runs no service and stores no personal data. Its assets are:

- **The integrity of the findings.** A flaw that lets crafted upstream data misgrade an
  institution, or that makes the site or dataset state a claim its own data does not support,
  is treated with the same urgency as a classic vulnerability, because a wrong public claim
  about a named institution is this project's worst failure mode.
- **The CI supply chain.** Actions are pinned to full commit SHAs; gitleaks, semgrep, and
  pip-audit run as blocking gates (`.github/workflows/security.yml`). A way around those gates
  is a vulnerability.
- **The one secret.** `DATA_GOV_API_KEY` lives only in GitHub Actions secrets. It is a
  free-tier rate-limit key, not a credential to any private data, but a leak still gets fixed
  and rotated.

Scan configuration lives in CI, not in this file. The audit record, including the supply-chain
declarations, is in `docs/RESPONSIBLE-TECH-AUDITS.md`.
