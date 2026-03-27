#!/usr/bin/env python3
"""
GitHub Repo Activity Analyzer - Analysis & Chart Generation

Reads raw JSON data collected by collect.yml and produces:
  - PNG charts in reports/
  - A text summary in reports/summary.txt

Usage:
  python3 scripts/analyze.py [--data-dir data] [--reports-dir reports] [--session-gap 120]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from glob import glob
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def load_repo_data(data_dir):
    """Return dict keyed by repo slug with sub-keys: commits, pulls, issues, code_frequency."""
    repos = {}
    for entry in sorted(Path(data_dir).iterdir()):
        if not entry.is_dir():
            continue
        slug = entry.name
        commits = []
        for cfile in sorted(glob(str(entry / "commits_page*.json"))):
            page = load_json(cfile)
            if isinstance(page, list):
                commits.extend(page)

        repos[slug] = {
            "commits": commits,
            "pulls": load_json(entry / "pulls.json"),
            "issues": load_json(entry / "issues.json"),
            "code_frequency": load_json(entry / "code_frequency.json"),
        }
    return repos


def load_metadata(data_dir):
    meta_path = Path(data_dir) / "collection_metadata.json"
    if meta_path.exists():
        return load_json(str(meta_path))
    return {}


# ---------------------------------------------------------------------------
# Commit analysis
# ---------------------------------------------------------------------------

def parse_commits(commits):
    """Extract date and author from commit objects."""
    rows = []
    for c in commits:
        commit_data = c.get("commit", {})
        author_info = commit_data.get("author") or {}
        date_str = author_info.get("date")
        if not date_str:
            continue
        rows.append({
            "date": pd.to_datetime(date_str, utc=True),
            "author": author_info.get("name", "unknown"),
            "sha": c.get("sha", ""),
        })
    return pd.DataFrame(rows)


def commits_per_period(df, freq="W"):
    if df.empty:
        return pd.Series(dtype=int)
    return df.set_index("date").resample(freq).size()


# ---------------------------------------------------------------------------
# Work session estimation (commit-based)
# ---------------------------------------------------------------------------

def estimate_sessions(df, gap_minutes=120):
    """
    Group commits into work sessions. A new session starts when the gap
    between consecutive commits exceeds gap_minutes. Each session's duration
    is the span from first to last commit, plus a fixed 30-min buffer for
    the first/only commit.
    """
    if df.empty:
        return pd.DataFrame(columns=["start", "end", "duration_hours", "commits"])

    sorted_df = df.sort_values("date")
    gap = timedelta(minutes=gap_minutes)
    sessions = []
    session_start = sorted_df.iloc[0]["date"]
    session_end = session_start
    commit_count = 1

    for i in range(1, len(sorted_df)):
        current = sorted_df.iloc[i]["date"]
        if current - session_end > gap:
            duration = (session_end - session_start).total_seconds() / 3600 + 0.5
            sessions.append({
                "start": session_start,
                "end": session_end,
                "duration_hours": max(duration, 0.5),
                "commits": commit_count,
            })
            session_start = current
            session_end = current
            commit_count = 1
        else:
            session_end = current
            commit_count += 1

    duration = (session_end - session_start).total_seconds() / 3600 + 0.5
    sessions.append({
        "start": session_start,
        "end": session_end,
        "duration_hours": max(duration, 0.5),
        "commits": commit_count,
    })

    return pd.DataFrame(sessions)


# ---------------------------------------------------------------------------
# Issue analysis
# ---------------------------------------------------------------------------

def parse_issues(issues):
    """Parse issues, excluding pull requests (GitHub includes PRs in /issues)."""
    rows = []
    for iss in issues:
        if iss.get("pull_request"):
            continue
        created = iss.get("created_at")
        if not created:
            continue
        closed = iss.get("closed_at")
        rows.append({
            "created": pd.to_datetime(created, utc=True),
            "closed": pd.to_datetime(closed, utc=True) if closed else pd.NaT,
            "state": iss.get("state", "open"),
            "title": iss.get("title", ""),
        })
    return pd.DataFrame(rows)


def issue_resolution_time(df):
    """Return Series of resolution times in hours for closed issues."""
    closed = df.dropna(subset=["closed"])
    if closed.empty:
        return pd.Series(dtype=float)
    return (closed["closed"] - closed["created"]).dt.total_seconds() / 3600


# ---------------------------------------------------------------------------
# Pull request analysis
# ---------------------------------------------------------------------------

def parse_pulls(pulls):
    rows = []
    for pr in pulls:
        created = pr.get("created_at")
        if not created:
            continue
        merged = pr.get("merged_at")
        closed = pr.get("closed_at")
        rows.append({
            "created": pd.to_datetime(created, utc=True),
            "merged": pd.to_datetime(merged, utc=True) if merged else pd.NaT,
            "closed": pd.to_datetime(closed, utc=True) if closed else pd.NaT,
            "state": pr.get("state", "open"),
            "title": pr.get("title", ""),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Code frequency (lines added/deleted per week)
# ---------------------------------------------------------------------------

def parse_code_frequency(data):
    """GitHub returns [[unix_ts, additions, deletions], ...]."""
    rows = []
    for entry in data:
        if isinstance(entry, list) and len(entry) >= 3:
            rows.append({
                "week": pd.to_datetime(entry[0], unit="s", utc=True),
                "additions": entry[1],
                "deletions": abs(entry[2]),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------

CHART_STYLE = {
    "figure.facecolor": "#1e1e2e",
    "axes.facecolor": "#1e1e2e",
    "axes.edgecolor": "#585b70",
    "axes.labelcolor": "#cdd6f4",
    "text.color": "#cdd6f4",
    "xtick.color": "#a6adc8",
    "ytick.color": "#a6adc8",
    "grid.color": "#313244",
    "grid.alpha": 0.5,
}

PALETTE = ["#89b4fa", "#a6e3a1", "#f9e2af", "#f38ba8", "#cba6f7",
           "#fab387", "#94e2d5", "#74c7ec", "#f5c2e7", "#b4befe"]


def apply_style():
    plt.rcParams.update(CHART_STYLE)
    plt.rcParams["font.size"] = 10


def save_chart(fig, reports_dir, name):
    path = os.path.join(reports_dir, f"{name}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def chart_commit_activity(all_commit_series, reports_dir):
    """Stacked area chart of commits per week, per repo."""
    apply_style()
    fig, ax = plt.subplots(figsize=(14, 6))

    combined = pd.DataFrame(all_commit_series)
    if combined.empty:
        ax.text(0.5, 0.5, "No commit data available", transform=ax.transAxes,
                ha="center", va="center", fontsize=14)
        return save_chart(fig, reports_dir, "01_commit_activity")

    combined = combined.fillna(0)
    combined.plot.area(ax=ax, stacked=True, alpha=0.7, color=PALETTE[:len(combined.columns)])

    ax.set_title("Commit Activity Over Time (Weekly)", fontsize=14, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("Commits")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.legend(loc="upper left", fontsize=8, framealpha=0.8)
    ax.grid(True, axis="y")
    return save_chart(fig, reports_dir, "01_commit_activity")


def chart_lines_changed(all_code_freq, reports_dir):
    """Additions vs deletions over time."""
    apply_style()
    fig, ax = plt.subplots(figsize=(14, 6))

    if not all_code_freq:
        ax.text(0.5, 0.5, "No code frequency data available", transform=ax.transAxes,
                ha="center", va="center", fontsize=14)
        return save_chart(fig, reports_dir, "02_lines_changed")

    combined = pd.concat(all_code_freq, ignore_index=True)
    weekly = combined.groupby("week")[["additions", "deletions"]].sum().sort_index()

    ax.fill_between(weekly.index, weekly["additions"], alpha=0.6, color="#a6e3a1", label="Additions")
    ax.fill_between(weekly.index, -weekly["deletions"], alpha=0.6, color="#f38ba8", label="Deletions")
    ax.axhline(y=0, color="#585b70", linewidth=0.8)

    ax.set_title("Lines Changed Over Time (Weekly)", fontsize=14, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("Lines")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, axis="y")
    return save_chart(fig, reports_dir, "02_lines_changed")


def chart_work_hours(all_sessions, reports_dir):
    """Estimated work hours per month."""
    apply_style()
    fig, ax = plt.subplots(figsize=(14, 6))

    if not all_sessions:
        ax.text(0.5, 0.5, "No session data available", transform=ax.transAxes,
                ha="center", va="center", fontsize=14)
        return save_chart(fig, reports_dir, "03_work_hours")

    combined = pd.concat(all_sessions, ignore_index=True)
    combined["month"] = combined["start"].dt.to_period("M")
    monthly = combined.groupby("month")["duration_hours"].sum()
    monthly.index = monthly.index.to_timestamp()

    ax.bar(monthly.index, monthly.values, width=20, color="#89b4fa", alpha=0.8)
    if len(monthly) >= 3:
        rolling = monthly.rolling(3, min_periods=1).mean()
        ax.plot(rolling.index, rolling.values, color="#f9e2af", linewidth=2, label="3-month avg")
        ax.legend(fontsize=9)

    ax.set_title("Estimated Work Hours Per Month (Commit Sessions)", fontsize=14, fontweight="bold")
    ax.set_xlabel("")
    ax.set_ylabel("Hours")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.grid(True, axis="y")
    return save_chart(fig, reports_dir, "03_work_hours")


def chart_issue_velocity(all_issues, reports_dir):
    """Issues opened vs closed per month with net trend."""
    apply_style()
    fig, ax = plt.subplots(figsize=(14, 6))

    if not all_issues:
        ax.text(0.5, 0.5, "No issue data available", transform=ax.transAxes,
                ha="center", va="center", fontsize=14)
        return save_chart(fig, reports_dir, "04_issue_velocity")

    combined = pd.concat(all_issues, ignore_index=True)
    opened = combined.set_index("created").resample("ME").size().rename("opened")
    closed_issues = combined.dropna(subset=["closed"])
    closed = closed_issues.set_index("closed").resample("ME").size().rename("closed") if not closed_issues.empty else pd.Series(dtype=int, name="closed")

    df = pd.DataFrame({"opened": opened, "closed": closed}).fillna(0)

    x = range(len(df))
    width = 0.35
    ax.bar([i - width / 2 for i in x], df["opened"], width, label="Opened", color="#f38ba8", alpha=0.8)
    ax.bar([i + width / 2 for i in x], df["closed"], width, label="Closed", color="#a6e3a1", alpha=0.8)

    net = (df["opened"] - df["closed"]).cumsum()
    ax2 = ax.twinx()
    ax2.plot(list(x), net.values, color="#f9e2af", linewidth=2, marker="o", markersize=3, label="Net open (cumulative)")
    ax2.set_ylabel("Net Open Issues", color="#f9e2af")
    ax2.tick_params(axis="y", labelcolor="#f9e2af")

    ax.set_xticks(list(x))
    ax.set_xticklabels([d.strftime("%b %Y") for d in df.index], rotation=45, ha="right", fontsize=8)
    ax.set_title("Issue Velocity (Monthly)", fontsize=14, fontweight="bold")
    ax.set_ylabel("Count")
    ax.legend(loc="upper left", fontsize=9)
    ax2.legend(loc="upper right", fontsize=9)
    ax.grid(True, axis="y")
    return save_chart(fig, reports_dir, "04_issue_velocity")


def chart_pr_throughput(all_pulls, reports_dir):
    """PR opened, merged, and avg time-to-merge per month."""
    apply_style()
    fig, ax = plt.subplots(figsize=(14, 6))

    if not all_pulls:
        ax.text(0.5, 0.5, "No pull request data available", transform=ax.transAxes,
                ha="center", va="center", fontsize=14)
        return save_chart(fig, reports_dir, "05_pr_throughput")

    combined = pd.concat(all_pulls, ignore_index=True)
    created_monthly = combined.set_index("created").resample("ME").size().rename("created")
    merged = combined.dropna(subset=["merged"])
    merged_monthly = merged.set_index("merged").resample("ME").size().rename("merged") if not merged.empty else pd.Series(dtype=int, name="merged")

    df = pd.DataFrame({"created": created_monthly, "merged": merged_monthly}).fillna(0)

    x = range(len(df))
    width = 0.35
    ax.bar([i - width / 2 for i in x], df["created"], width, label="Opened", color="#89b4fa", alpha=0.8)
    ax.bar([i + width / 2 for i in x], df["merged"], width, label="Merged", color="#a6e3a1", alpha=0.8)

    if not merged.empty:
        merged_data = combined.dropna(subset=["merged"]).copy()
        merged_data["ttm_hours"] = (merged_data["merged"] - merged_data["created"]).dt.total_seconds() / 3600
        avg_ttm = merged_data.set_index("merged").resample("ME")["ttm_hours"].mean()
        ax2 = ax.twinx()
        valid = avg_ttm.dropna()
        if not valid.empty:
            positions = [list(df.index).index(d) for d in valid.index if d in df.index]
            values = [valid[d] for d in valid.index if d in df.index]
            ax2.plot(positions, values, color="#f9e2af", linewidth=2, marker="o", markersize=3, label="Avg time-to-merge (hrs)")
            ax2.set_ylabel("Hours to Merge", color="#f9e2af")
            ax2.tick_params(axis="y", labelcolor="#f9e2af")
            ax2.legend(loc="upper right", fontsize=9)

    ax.set_xticks(list(x))
    ax.set_xticklabels([d.strftime("%b %Y") for d in df.index], rotation=45, ha="right", fontsize=8)
    ax.set_title("Pull Request Throughput (Monthly)", fontsize=14, fontweight="bold")
    ax.set_ylabel("Count")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, axis="y")
    return save_chart(fig, reports_dir, "05_pr_throughput")


def chart_trend_summary(stats, reports_dir):
    """Sparkline dashboard with trend indicators."""
    apply_style()
    metrics = [
        ("Commits/Week", stats.get("commits_weekly", pd.Series(dtype=float))),
        ("Work Hours/Month", stats.get("hours_monthly", pd.Series(dtype=float))),
        ("Issues Opened/Month", stats.get("issues_monthly", pd.Series(dtype=float))),
        ("PRs Merged/Month", stats.get("prs_merged_monthly", pd.Series(dtype=float))),
        ("Lines Added/Week", stats.get("additions_weekly", pd.Series(dtype=float))),
    ]

    non_empty = [(name, data) for name, data in metrics if not data.empty and len(data) > 1]

    if not non_empty:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.text(0.5, 0.5, "Insufficient data for trend analysis", transform=ax.transAxes,
                ha="center", va="center", fontsize=14)
        return save_chart(fig, reports_dir, "06_trend_summary")

    fig, axes = plt.subplots(len(non_empty), 1, figsize=(12, 2.5 * len(non_empty)))
    if len(non_empty) == 1:
        axes = [axes]

    for ax, (name, data) in zip(axes, non_empty):
        data = data.fillna(0)
        color = "#a6e3a1"
        if len(data) >= 4:
            recent_half = data.iloc[len(data) // 2:]
            older_half = data.iloc[:len(data) // 2]
            if recent_half.mean() > older_half.mean() * 1.1:
                trend = "INCREASING"
                color = "#a6e3a1"
            elif recent_half.mean() < older_half.mean() * 0.9:
                trend = "DECREASING"
                color = "#f38ba8"
            else:
                trend = "STABLE"
                color = "#89b4fa"
        else:
            trend = "N/A"
            color = "#585b70"

        ax.plot(data.index, data.values, color=color, linewidth=2)
        ax.fill_between(data.index, data.values, alpha=0.2, color=color)
        ax.set_ylabel(name, fontsize=10, fontweight="bold")
        ax.text(0.98, 0.85, trend, transform=ax.transAxes, ha="right", va="top",
                fontsize=11, fontweight="bold", color=color,
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#313244", edgecolor=color, alpha=0.8))
        ax.grid(True, axis="y")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

    fig.suptitle("Trend Summary", fontsize=16, fontweight="bold", y=1.02)
    fig.tight_layout()
    return save_chart(fig, reports_dir, "06_trend_summary")


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------

def generate_summary(repo_stats, all_sessions_list, all_issues_list, all_pulls_list, reports_dir):
    lines = []
    lines.append("=" * 60)
    lines.append("  GitHub Repo Activity Analyzer - Summary Report")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")

    total_commits = 0
    total_hours = 0.0
    total_issues = 0
    total_prs = 0

    for slug, stats in sorted(repo_stats.items()):
        lines.append(f"  Repo: {slug.replace('_', '/')}")
        lines.append(f"    Commits:       {stats['commits']}")
        lines.append(f"    Sessions:      {stats['sessions']}")
        lines.append(f"    Est. Hours:    {stats['hours']:.1f}")
        lines.append(f"    Issues:        {stats['issues']}")
        lines.append(f"    Pull Requests: {stats['pulls']}")
        lines.append(f"    Lines Added:   {stats['additions']:,}")
        lines.append(f"    Lines Deleted: {stats['deletions']:,}")
        lines.append("")
        total_commits += stats["commits"]
        total_hours += stats["hours"]
        total_issues += stats["issues"]
        total_prs += stats["pulls"]

    lines.append("-" * 60)
    lines.append(f"  TOTALS")
    lines.append(f"    Repos analyzed:    {len(repo_stats)}")
    lines.append(f"    Total commits:     {total_commits}")
    lines.append(f"    Total est. hours:  {total_hours:.1f}")
    lines.append(f"    Total issues:      {total_issues}")
    lines.append(f"    Total PRs:         {total_prs}")
    lines.append("-" * 60)
    lines.append("")

    if all_sessions_list:
        combined_sessions = pd.concat(all_sessions_list, ignore_index=True)
        if not combined_sessions.empty:
            monthly = combined_sessions.copy()
            monthly["month"] = monthly["start"].dt.to_period("M")
            monthly_hours = monthly.groupby("month")["duration_hours"].sum()
            if len(monthly_hours) >= 2:
                recent = monthly_hours.iloc[-3:].mean() if len(monthly_hours) >= 3 else monthly_hours.iloc[-1]
                older = monthly_hours.iloc[:-3].mean() if len(monthly_hours) > 3 else monthly_hours.iloc[0]
                if recent > older * 1.1:
                    lines.append("  Work Trend:  INCREASING (recent months show more activity)")
                elif recent < older * 0.9:
                    lines.append("  Work Trend:  DECREASING (recent months show less activity)")
                else:
                    lines.append("  Work Trend:  STABLE")
                lines.append("")

    busiest = sorted(repo_stats.items(), key=lambda x: x[1]["commits"], reverse=True)
    if busiest:
        lines.append("  Busiest Repos (by commits):")
        for slug, stats in busiest[:5]:
            lines.append(f"    {slug.replace('_', '/')}: {stats['commits']} commits, {stats['hours']:.1f} est. hours")
        lines.append("")

    lines.append("=" * 60)
    lines.append("  Charts saved to reports/ directory")
    lines.append("=" * 60)

    summary_text = "\n".join(lines)
    path = os.path.join(reports_dir, "summary.txt")
    with open(path, "w") as f:
        f.write(summary_text)
    return summary_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Analyze GitHub repo activity data")
    parser.add_argument("--data-dir", default="data", help="Path to collected data")
    parser.add_argument("--reports-dir", default="reports", help="Path for output reports")
    parser.add_argument("--session-gap", type=int, default=120, help="Session gap in minutes")
    args = parser.parse_args()

    data_dir = args.data_dir
    reports_dir = args.reports_dir
    session_gap = args.session_gap

    metadata = load_metadata(data_dir)
    if metadata:
        session_gap = metadata.get("session_gap_minutes", session_gap)
        print(f"Using session gap: {session_gap} minutes (from collection metadata)")

    os.makedirs(reports_dir, exist_ok=True)

    print(f"Loading data from {data_dir}/...")
    repos = load_repo_data(data_dir)
    if not repos:
        print("ERROR: No repo data found. Run collect.yml first.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(repos)} repo(s): {', '.join(repos.keys())}")

    all_commit_series = {}
    all_code_freq = []
    all_sessions = []
    all_issues = []
    all_pulls = []
    repo_stats = {}

    for slug, data in repos.items():
        print(f"\n  Analyzing {slug}...")

        commit_df = parse_commits(data["commits"])
        weekly_commits = commits_per_period(commit_df, "W")
        if not weekly_commits.empty:
            all_commit_series[slug] = weekly_commits
        print(f"    Commits: {len(commit_df)}")

        sessions_df = estimate_sessions(commit_df, gap_minutes=session_gap)
        total_hours = sessions_df["duration_hours"].sum() if not sessions_df.empty else 0
        if not sessions_df.empty:
            all_sessions.append(sessions_df)
        print(f"    Sessions: {len(sessions_df)}, Est. hours: {total_hours:.1f}")

        code_freq_df = parse_code_frequency(data["code_frequency"])
        if not code_freq_df.empty:
            all_code_freq.append(code_freq_df)

        issues_df = parse_issues(data["issues"])
        if not issues_df.empty:
            all_issues.append(issues_df)
        print(f"    Issues: {len(issues_df)}")

        pulls_df = parse_pulls(data["pulls"])
        if not pulls_df.empty:
            all_pulls.append(pulls_df)
        print(f"    PRs: {len(pulls_df)}")

        repo_stats[slug] = {
            "commits": len(commit_df),
            "sessions": len(sessions_df),
            "hours": total_hours,
            "issues": len(issues_df),
            "pulls": len(pulls_df),
            "additions": int(code_freq_df["additions"].sum()) if not code_freq_df.empty else 0,
            "deletions": int(code_freq_df["deletions"].sum()) if not code_freq_df.empty else 0,
        }

    print("\nGenerating charts...")

    chart_commit_activity(all_commit_series, reports_dir)
    print("  [1/6] Commit activity")

    chart_lines_changed(all_code_freq, reports_dir)
    print("  [2/6] Lines changed")

    chart_work_hours(all_sessions, reports_dir)
    print("  [3/6] Work hours")

    chart_issue_velocity(all_issues, reports_dir)
    print("  [4/6] Issue velocity")

    chart_pr_throughput(all_pulls, reports_dir)
    print("  [5/6] PR throughput")

    trend_stats = {}
    if all_commit_series:
        combined_commits = pd.DataFrame(all_commit_series).sum(axis=1).fillna(0)
        trend_stats["commits_weekly"] = combined_commits
    if all_sessions:
        combined = pd.concat(all_sessions, ignore_index=True)
        monthly_hrs = combined.set_index("start").resample("ME")["duration_hours"].sum()
        trend_stats["hours_monthly"] = monthly_hrs
    if all_issues:
        combined_iss = pd.concat(all_issues, ignore_index=True)
        trend_stats["issues_monthly"] = combined_iss.set_index("created").resample("ME").size()
    if all_pulls:
        combined_pr = pd.concat(all_pulls, ignore_index=True)
        merged_pr = combined_pr.dropna(subset=["merged"])
        trend_stats["prs_merged_monthly"] = merged_pr.set_index("merged").resample("ME").size() if not merged_pr.empty else pd.Series(dtype=int)
    if all_code_freq:
        combined_cf = pd.concat(all_code_freq, ignore_index=True)
        trend_stats["additions_weekly"] = combined_cf.groupby("week")["additions"].sum().sort_index()

    chart_trend_summary(trend_stats, reports_dir)
    print("  [6/6] Trend summary")

    print("\nGenerating text summary...")
    summary = generate_summary(repo_stats, all_sessions, all_issues, all_pulls, reports_dir)
    print(summary)

    print(f"\nDone! Reports saved to {reports_dir}/")


if __name__ == "__main__":
    main()
