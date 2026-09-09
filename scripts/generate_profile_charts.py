#!/usr/bin/env python3
"""Generate profile charts from the owner's live public GitHub repositories."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import json
import math
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

API_ROOT = "https://api.github.com"
COLORS = [
    "#58a6ff",
    "#3fb950",
    "#d29922",
    "#f778ba",
    "#a371f7",
    "#f85149",
    "#39c5cf",
    "#db6d28",
]


def select_source_repositories(repos: list[dict[str, Any]], username: str) -> list[dict[str, Any]]:
    """Keep active, public, non-fork repositories owned by the user, excluding this profile repo."""
    return [
        repo
        for repo in repos
        if repo.get("name", "").casefold() != username.casefold()
        and not repo.get("fork", False)
        and not repo.get("archived", False)
        and not repo.get("private", False)
    ]


def aggregate_languages(language_maps: Iterable[dict[str, int]]) -> dict[str, int]:
    totals: collections.Counter[str] = collections.Counter()
    for language_map in language_maps:
        totals.update({name: size for name, size in language_map.items() if size > 0})
    return dict(sorted(totals.items(), key=lambda item: (-item[1], item[0])))


def collapse_categories(values: dict[str, int], *, limit: int = 7) -> dict[str, int]:
    """Keep SVG legends readable by combining categories beyond the display limit."""
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) <= limit:
        return dict(ordered)
    visible = ordered[: limit - 1]
    return dict([*visible, ("Other", sum(value for _, value in ordered[limit - 1 :]))])


def classify_repository(
    repo: dict[str, Any], paths: Iterable[str], languages: dict[str, int]
) -> str:
    """Classify a repository's primary development layer from metadata and file-tree signals."""
    path_list = [path.casefold() for path in paths]
    metadata = " ".join(
        [
            str(repo.get("name", "")),
            str(repo.get("description") or ""),
            " ".join(repo.get("topics") or []),
        ]
    ).casefold()
    language_names = {name.casefold() for name in languages}

    infra_signals = (
        any(path.endswith(".tf") or path.endswith(".tfvars") for path in path_list)
        or any("kubernetes" in path or "/k8s/" in f"/{path}/" for path in path_list)
        or any(term in metadata for term in ("terraform", "kubernetes", "infrastructure as code"))
    )
    if infra_signals:
        return "Cloud & Infra"

    data_ai_signals = (
        any(path.endswith(".ipynb") for path in path_list)
        or any(
            term in metadata
            for term in (
                "machine learning",
                "artificial intelligence",
                "whisper",
                "transcription",
                "analytics",
                "data science",
                " llm",
                "ai ",
            )
        )
        or any(
            term in path
            for path in path_list
            for term in ("notebook", "streamlit", "pandas", "transcrib", "/ml/")
        )
    )
    if data_ai_signals:
        return "Data & AI"

    frontend_signals = (
        any(path.endswith((".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss")) for path in path_list)
        or bool(language_names & {"html", "css", "scss", "vue"})
        or repo.get("name", "").casefold().endswith(".github.io")
    )
    backend_signals = (
        any(
            segment in f"/{path}/"
            for path in path_list
            for segment in ("/api/", "/server/", "/backend/", "/routes/")
        )
        or any(
            term in metadata
            for term in (
                "backend",
                "back-end",
                "rest api",
                "graphql api",
                "web api",
                "api server",
            )
        )
    )

    if frontend_signals and backend_signals:
        return "Full Stack"
    if frontend_signals:
        return "Frontend"
    if backend_signals:
        return "Backend & API"
    return "Unknown"


def commit_period_start(today: dt.date, weeks: int) -> dt.date:
    """Return the Monday starting a window of ISO calendar weeks."""
    current_week_start = today - dt.timedelta(days=today.weekday())
    return current_week_start - dt.timedelta(weeks=weeks - 1)


def bucket_commits_by_week(
    commit_dates: Iterable[dt.datetime], *, today: dt.date | None = None, weeks: int = 52
) -> tuple[list[str], list[int]]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    start = commit_period_start(today, weeks)
    labels = [(start + dt.timedelta(days=7 * index)).isoformat() for index in range(weeks)]
    counts = [0] * weeks
    for commit_date in commit_dates:
        index = (commit_date.date() - start).days // 7
        if 0 <= index < weeks:
            counts[index] += 1
    return labels, counts


def _svg_style() -> str:
    return """<style>
      .bg { fill: #ffffff; stroke: #d0d7de; }
      .title { fill: #1f2328; font: 700 25px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
      .subtitle { fill: #656d76; font: 14px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
      .label { fill: #1f2328; font: 600 15px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
      .muted { fill: #656d76; font: 13px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
      .grid { stroke: #d8dee4; stroke-width: 1; }
      @media (prefers-color-scheme: dark) {
        .bg { fill: #0d1117; stroke: #30363d; }
        .title, .label { fill: #e6edf3; }
        .subtitle, .muted { fill: #8b949e; }
        .grid { stroke: #30363d; }
      }
    </style>"""


def render_pie_chart(title: str, values: dict[str, int], *, subtitle: str = "") -> str:
    width, height = 700, 420
    total = sum(max(value, 0) for value in values.values())
    safe_values = [(name, value) for name, value in values.items() if value > 0]
    radius = 105
    circumference = 2 * math.pi * radius
    offset = 0.0
    segments: list[str] = []
    legend: list[str] = []

    if total:
        for index, (name, value) in enumerate(safe_values):
            fraction = value / total
            length = fraction * circumference
            color = COLORS[index % len(COLORS)]
            segments.append(
                f'<circle cx="220" cy="235" r="{radius}" fill="none" stroke="{color}" '
                f'stroke-width="58" stroke-dasharray="{length:.3f} {circumference - length:.3f}" '
                f'stroke-dashoffset="{-offset:.3f}" transform="rotate(-90 220 235)"/>'
            )
            y = 145 + index * 33
            percent = fraction * 100
            legend.append(
                f'<rect x="395" y="{y - 12}" width="14" height="14" rx="3" fill="{color}"/>'
                f'<text class="label" x="420" y="{y}">{html.escape(name)}</text>'
                f'<text class="muted" x="645" y="{y}" text-anchor="end">{percent:.1f}%</text>'
            )
            offset += length
    else:
        segments.append('<circle cx="220" cy="235" r="105" fill="none" stroke="#8b949e" stroke-width="58"/>')
        legend.append('<text class="muted" x="420" y="145">No repository data</text>')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
  <title>{html.escape(title)}</title>
  <desc>{html.escape(subtitle)}</desc>
  {_svg_style()}
  <rect class="bg" x="1" y="1" width="698" height="418" rx="10"/>
  <text class="title" x="34" y="48">{html.escape(title)}</text>
  <text class="subtitle" x="34" y="75">{html.escape(subtitle)}</text>
  {''.join(segments)}
  <text class="label" x="220" y="230" text-anchor="middle">{len(safe_values)}</text>
  <text class="muted" x="220" y="252" text-anchor="middle">categories</text>
  {''.join(legend)}
</svg>
'''


def render_bar_chart(
    title: str, labels: list[str], counts: list[int], *, subtitle: str = ""
) -> str:
    width, height = 1200, 390
    chart_x, chart_y, chart_w, chart_h = 55, 112, 1090, 220
    maximum = max(counts, default=0) or 1
    gap = 3
    bar_width = max(1, (chart_w - gap * max(0, len(counts) - 1)) / max(1, len(counts)))
    bars: list[str] = []
    for index, count in enumerate(counts):
        bar_height = (count / maximum) * chart_h
        x = chart_x + index * (bar_width + gap)
        y = chart_y + chart_h - bar_height
        bars.append(
            f'<rect class="bar" x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" '
            f'height="{bar_height:.2f}" rx="2" fill="#58a6ff"><title>{html.escape(labels[index])}: {count}</title></rect>'
        )

    month_labels: list[str] = []
    previous_month = ""
    for index, label in enumerate(labels):
        month = label[:7]
        if month != previous_month:
            x = chart_x + index * (bar_width + gap)
            try:
                display = dt.date.fromisoformat(label).strftime("%b")
            except ValueError:
                display = label
            month_labels.append(f'<text class="muted" x="{x:.2f}" y="357">{display}</text>')
            previous_month = month

    total = sum(counts)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">
  <title>{html.escape(title)}</title>
  <desc>{html.escape(subtitle)}; {total} commits</desc>
  {_svg_style()}
  <rect class="bg" x="1" y="1" width="1198" height="388" rx="10"/>
  <text class="title" x="38" y="48">{html.escape(title)}</text>
  <text class="subtitle" x="38" y="75">{html.escape(subtitle)}</text>
  <text class="label" x="1150" y="48" text-anchor="end">{total} commits</text>
  <line class="grid" x1="{chart_x}" y1="{chart_y}" x2="{chart_x + chart_w}" y2="{chart_y}"/>
  <line class="grid" x1="{chart_x}" y1="{chart_y + chart_h / 2}" x2="{chart_x + chart_w}" y2="{chart_y + chart_h / 2}"/>
  <line class="grid" x1="{chart_x}" y1="{chart_y + chart_h}" x2="{chart_x + chart_w}" y2="{chart_y + chart_h}"/>
  <text class="muted" x="45" y="{chart_y + 5}" text-anchor="end">{maximum}</text>
  <text class="muted" x="45" y="{chart_y + chart_h + 5}" text-anchor="end">0</text>
  {''.join(bars)}
  {''.join(month_labels)}
</svg>
'''


class GitHubClient:
    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{API_ROOT}{path}" + (f"?{query}" if query else "")
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "profile-chart-generator",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {error.code} for {path}: {detail}") from error

    def paginated(self, path: str, params: dict[str, str] | None = None) -> list[Any]:
        output: list[Any] = []
        page = 1
        while True:
            page_params = {**(params or {}), "per_page": "100", "page": str(page)}
            batch = self.get(path, page_params)
            if not isinstance(batch, list):
                raise RuntimeError(f"Expected a list from GitHub API path {path}")
            output.extend(batch)
            if len(batch) < 100:
                return output
            page += 1


def fetch_repository_tree(client: GitHubClient, repo: dict[str, Any]) -> list[str]:
    branch = repo.get("default_branch")
    if not branch:
        return []
    path = f"/repos/{repo['full_name']}/git/trees/{urllib.parse.quote(branch, safe='')}"
    try:
        payload = client.get(path, {"recursive": "1"})
    except RuntimeError as error:
        if " 409 " in f" {error} ":
            return []
        raise
    return [item["path"] for item in payload.get("tree", []) if item.get("type") == "blob"]


def fetch_commit_dates(
    client: GitHubClient, repositories: list[dict[str, Any]], username: str, since: dt.datetime
) -> list[dt.datetime]:
    dates: list[dt.datetime] = []
    for repo in repositories:
        commits = client.paginated(
            f"/repos/{repo['full_name']}/commits",
            {"author": username, "since": since.isoformat().replace("+00:00", "Z")},
        )
        for commit in commits:
            date_text = commit.get("commit", {}).get("author", {}).get("date")
            if date_text:
                dates.append(dt.datetime.fromisoformat(date_text.replace("Z", "+00:00")))
    return dates


def generate(username: str, output_dir: pathlib.Path, client: GitHubClient) -> dict[str, Any]:
    all_repos = client.paginated(f"/users/{username}/repos", {"type": "owner", "sort": "full_name"})
    repositories = select_source_repositories(all_repos, username)
    language_maps: list[dict[str, int]] = []
    layer_counts: collections.Counter[str] = collections.Counter()
    repository_data: list[dict[str, Any]] = []

    for repo in repositories:
        languages = client.get(f"/repos/{repo['full_name']}/languages")
        paths = fetch_repository_tree(client, repo)
        layer = classify_repository(repo, paths, languages)
        language_maps.append(languages)
        # Every selected repository contributes exactly one layer. When the
        # metadata and tree do not provide a reliable signal, the classifier
        # deliberately records it as Unknown instead of guessing.
        included_in_layer_chart = True
        layer_counts[layer] += 1
        repository_data.append(
            {
                "name": repo["name"],
                "layer": layer,
                "languages": languages,
                "files_scanned": len(paths),
                "included_in_layer_chart": included_in_layer_chart,
            }
        )

    today = dt.datetime.now(dt.timezone.utc).date()
    period_start_date = commit_period_start(today, 52)
    since = dt.datetime.combine(period_start_date, dt.time.min, tzinfo=dt.timezone.utc)
    commit_dates = fetch_commit_dates(client, repositories, username, since)
    labels, counts = bucket_commits_by_week(commit_dates, today=today, weeks=52)
    languages = collapse_categories(aggregate_languages(language_maps))
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    period_start = labels[0]
    period_end = today.isoformat()
    repository_count = len(repositories)
    language_note = (
        f"GitHub Linguist bytes · {repository_count} public source repos · {period_end} UTC"
    )
    layer_note = "1 repo = 1 · metadata/topics/files/languages · unmatched = Unknown"
    commit_note = (
        f"52 weeks {period_start}–{period_end} UTC · authored by @{username} · public source repos"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "languages.svg").write_text(
        render_pie_chart("Languages by Code Size", languages, subtitle=language_note), encoding="utf-8"
    )
    ordered_layers = collapse_categories(dict(layer_counts))
    (output_dir / "layers.svg").write_text(
        render_pie_chart("Development Layers", ordered_layers, subtitle=layer_note), encoding="utf-8"
    )
    (output_dir / "commits.svg").write_text(
        render_bar_chart("Commits by Week", labels, counts, subtitle=commit_note),
        encoding="utf-8",
    )

    payload = {
        "username": username,
        "generated_at": generated_at,
        "scope": {
            "included": "public repositories owned by the user",
            "excluded": ["forks", "archived repositories", "private repositories", "profile repository"],
            "repository_count": repository_count,
        },
        "methodology": {
            "languages": {
                "source": "GitHub REST API /languages (GitHub Linguist)",
                "aggregation": "sum bytes by language across selected repositories",
                "unit": "bytes",
                "period": f"repository default branches as observed at {generated_at}",
            },
            "layers": {
                "unit": "repositories",
                "aggregation": "one primary layer per selected repository",
                "signals": ["name", "description", "topics", "default-branch file paths", "languages"],
                "precedence": ["Cloud & Infra", "Data & AI", "Full Stack", "Frontend", "Backend & API", "Unknown"],
                "unknown_policy": "No reliable signal → Unknown",
            },
            "commits": {
                "source": "GitHub REST API /commits with author=username",
                "aggregation": "weekly counts, oldest to newest",
                "start": period_start,
                "end": period_end,
                "weeks": 52,
                "timezone": "UTC",
            },
        },
        "repositories": repository_data,
        "languages": languages,
        "layers": ordered_layers,
        "weekly_commits": dict(zip(labels, counts)),
    }
    (output_dir / "profile-data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default=os.getenv("PROFILE_USERNAME", "ukyamyam"))
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("assets/generated"))
    args = parser.parse_args()
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    payload = generate(args.username, args.output, GitHubClient(token))
    print(
        f"Generated charts from {len(payload['repositories'])} repositories and "
        f"{sum(payload['weekly_commits'].values())} commits."
    )


if __name__ == "__main__":
    main()
