# GitHub Repo Activity Analyzer

An Ansible + Python tool that polls public GitHub repositories via the REST API and generates historical activity reports with charts.

**Strictly read-only** -- this tool never modifies, clones, or writes to any target repository.

## What it does

- Collects commit history, pull requests, issues, and code frequency data from GitHub
- Estimates time spent based on commit session analysis and issue lifecycles
- Generates PNG charts showing activity trends over time
- Produces a text summary with totals and trend indicators (increasing / decreasing / stable)

## Charts generated

| Chart | Description |
|-------|-------------|
| `01_commit_activity.png` | Weekly commits per repo (stacked area) |
| `02_lines_changed.png` | Lines added vs deleted per week |
| `03_work_hours.png` | Estimated work hours per month with rolling average |
| `04_issue_velocity.png` | Issues opened vs closed per month |
| `05_pr_throughput.png` | PRs opened/merged with avg time-to-merge |
| `06_trend_summary.png` | Sparkline dashboard with trend indicators |
| `summary.txt` | Text report with per-repo and aggregate stats |

## Prerequisites

- Python 3.9+
- Ansible 2.14+
- Python packages: `pandas`, `matplotlib`

```bash
pip install -r requirements.txt
```

## Quick start

### 1. Configure repositories

Edit `repos.yml` to specify which repos to analyze:

```yaml
# Auto-discover repos by name pattern from a GitHub user or org
org_patterns:
  - org: "my-github-user"
    type: "users"              # "users" for personal accounts, "orgs" for organizations
    match: "my-project"        # substring match on repo name

# Or list repos manually
manual_repos:
  - "owner/repo-name"
  - "owner/another-repo"
```

### 2. Set a GitHub token (recommended)

Without a token the API limit is 60 requests/hour. With a token it's 5,000/hour.

Create a [personal access token](https://github.com/settings/tokens) with no extra scopes (public repo access is free) and export it:

```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

### 3. Run

```bash
# Full pipeline: collect data from GitHub + generate charts
ansible-playbook run-all.yml

# Or run stages independently:
ansible-playbook collect.yml      # fetch data only
ansible-playbook analyze.yml      # regenerate charts from existing data
```

### 4. View results

Charts and summary are saved to the `reports/` directory.

## Configuration reference

All settings live in `repos.yml`:

| Setting | Description | Default |
|---------|-------------|---------|
| `github_token` | Read from `GITHUB_TOKEN` env var. Optional but recommended. | `""` |
| `org_patterns` | Auto-discover repos from a GitHub user/org matching a name pattern. | `[]` |
| `manual_repos` | Manually listed repos in `owner/repo` format. | `[]` |
| `history_since` | How far back to collect data (ISO 8601 date). | `2024-01-01` |
| `session_gap_minutes` | Max gap between commits to count as one work session. | `120` |

## Project structure

```
github-repo-analyzer/
  repos.yml              # Repository and analysis configuration
  ansible.cfg            # Ansible settings
  inventory.ini          # Localhost inventory
  collect.yml            # Playbook: fetch data from GitHub API
  analyze.yml            # Playbook: run Python analysis script
  run-all.yml            # Playbook: collect + analyze in one command
  scripts/
    analyze.py           # Analysis engine and chart generation
  requirements.txt       # Python dependencies
  data/                  # Collected JSON data (git-ignored)
  reports/               # Generated charts and summary (git-ignored)
```

## How time estimation works

The tool uses two methods to estimate time spent:

**Commit-based sessions**: Consecutive commits within the `session_gap_minutes` threshold (default: 2 hours) are grouped into a single work session. Each session's duration is the time span between its first and last commit, plus a 30-minute buffer.

**Issue lifecycle**: Measures elapsed time from issue creation to close. Useful for tracking resolution effort alongside commit activity.

## Rate limits

| Auth mode | Limit | Repos supported per run |
|-----------|-------|------------------------|
| No token | 60 req/hr | ~15 repos |
| With token | 5,000 req/hr | ~1,250 repos |

The tool handles rate limit errors gracefully -- it collects as much data as possible and skips repos that hit the limit. Run again after the limit resets (usually 1 hour) to fill in gaps.

## Security

- The GitHub token is read from the `GITHUB_TOKEN` environment variable, never stored in files
- All API calls are read-only `GET` requests
- The token needs no additional scopes for public repositories
- `data/` and `reports/` directories are git-ignored
