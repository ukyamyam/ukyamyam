import datetime as dt
import pathlib
import tempfile
import unittest
from typing import cast

from scripts.generate_profile_charts import (
    GitHubClient,
    aggregate_languages,
    bucket_commits_by_week,
    classify_repository,
    collapse_categories,
    generate,
    render_bar_chart,
    render_pie_chart,
    select_source_repositories,
)


class RepositorySelectionTests(unittest.TestCase):
    def test_selects_owned_public_source_repositories_without_profile_repo(self):
        repos = [
            {"name": "app", "fork": False, "archived": False, "private": False},
            {"name": "ukyamyam", "fork": False, "archived": False, "private": False},
            {"name": "forked", "fork": True, "archived": False, "private": False},
            {"name": "old", "fork": False, "archived": True, "private": False},
            {"name": "secret", "fork": False, "archived": False, "private": True},
        ]

        selected = select_source_repositories(repos, "ukyamyam")

        self.assertEqual([repo["name"] for repo in selected], ["app"])


class AggregationTests(unittest.TestCase):
    def test_aggregate_languages_sums_bytes_and_drops_zero_values(self):
        result = aggregate_languages(
            [
                {"TypeScript": 120, "CSS": 30},
                {"TypeScript": 80, "Python": 100, "Shell": 0},
            ]
        )

        self.assertEqual(
            result,
            {"TypeScript": 200, "Python": 100, "CSS": 30},
        )

    def test_collapse_categories_combines_small_values_for_readable_legend(self):
        result = collapse_categories(
            {f"Language {index}": 10 - index for index in range(9)}, limit=7
        )

        self.assertEqual(len(result), 7)
        self.assertEqual(result["Other"], 4 + 3 + 2)

    def test_classifies_repository_from_actual_tree_signals(self):
        frontend = classify_repository(
            {"name": "web", "description": "browser app", "topics": []},
            ["package.json", "src/App.tsx", "src/styles.css"],
            {"TypeScript": 1000, "CSS": 200},
        )
        data_ai = classify_repository(
            {"name": "speech", "description": "Whisper transcription", "topics": ["ai"]},
            ["pyproject.toml", "src/transcribe.py", "models/model.py"],
            {"Python": 1000},
        )
        infra = classify_repository(
            {"name": "platform", "description": "cloud platform", "topics": []},
            ["main.tf", "modules/app/main.tf", "Dockerfile"],
            {"HCL": 1000},
        )

        self.assertEqual(frontend, "Frontend")
        self.assertEqual(data_ai, "Data & AI")
        self.assertEqual(infra, "Cloud & Infra")

    def test_backend_models_directory_does_not_mean_machine_learning(self):
        layer = classify_repository(
            {"name": "market", "description": "web app", "topics": []},
            [
                "frontend/src/App.jsx",
                "frontend/src/index.css",
                "backend/app.js",
                "backend/models/Item.js",
                "backend/routes/items.js",
            ],
            {"JavaScript": 1000, "CSS": 100},
        )

        self.assertEqual(layer, "Full Stack")

    def test_returns_unknown_when_repository_has_no_reliable_layer_signal(self):
        layer = classify_repository(
            {"name": "utilities", "description": None, "topics": []},
            ["README.md", "src/main.py"],
            {"Python": 1000},
        )

        self.assertEqual(layer, "Unknown")

    def test_generate_includes_unclassified_repositories_as_unknown(self):
        class FakeClient:
            def paginated(self, path, params=None):
                if path == "/users/ukyamyam/repos":
                    return [
                        {
                            "name": "notes",
                            "full_name": "ukyamyam/notes",
                            "description": None,
                            "topics": [],
                            "default_branch": "main",
                            "fork": False,
                            "archived": False,
                            "private": False,
                        }
                    ]
                if path == "/repos/ukyamyam/notes/commits":
                    return []
                raise AssertionError(path)

            def get(self, path, params=None):
                if path == "/repos/ukyamyam/notes/languages":
                    return {}
                if path == "/repos/ukyamyam/notes/git/trees/main":
                    return {"tree": [{"path": "README.md", "type": "blob"}]}
                raise AssertionError(path)

        with tempfile.TemporaryDirectory() as directory:
            payload = generate(
                "ukyamyam", pathlib.Path(directory), cast(GitHubClient, FakeClient())
            )

        self.assertEqual(payload["layers"], {"Unknown": 1})
        self.assertTrue(payload["repositories"][0]["included_in_layer_chart"])
        self.assertEqual(payload["methodology"]["languages"]["unit"], "bytes")
        self.assertEqual(payload["methodology"]["layers"]["unknown_policy"], "No reliable signal → Unknown")
        self.assertEqual(payload["methodology"]["commits"]["timezone"], "UTC")
        self.assertIn("start", payload["methodology"]["commits"])
        self.assertIn("end", payload["methodology"]["commits"])

    def test_bucket_commits_uses_utc_calendar_weeks_starting_monday(self):
        today = dt.date(2026, 9, 9)  # Wednesday
        dates = [
            dt.datetime(2026, 9, 6, tzinfo=dt.timezone.utc),  # before current week
            dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc),  # current Monday
            dt.datetime(2026, 9, 9, tzinfo=dt.timezone.utc),
        ]

        labels, counts = bucket_commits_by_week(dates, today=today, weeks=52)

        self.assertTrue(all(dt.date.fromisoformat(label).weekday() == 0 for label in labels))
        self.assertEqual(labels[-1], "2026-09-07")
        self.assertEqual(counts[-2:], [1, 2])

    def test_bucket_commits_returns_oldest_to_newest_52_week_counts(self):
        today = dt.date(2026, 8, 23)
        dates = [
            dt.datetime(2026, 8, 23, tzinfo=dt.timezone.utc),
            dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc),
            dt.datetime(2025, 8, 25, tzinfo=dt.timezone.utc),
            dt.datetime(2025, 7, 1, tzinfo=dt.timezone.utc),
        ]

        labels, counts = bucket_commits_by_week(dates, today=today, weeks=52)

        self.assertEqual(len(labels), 52)
        self.assertEqual(len(counts), 52)
        self.assertEqual(sum(counts), 3)
        self.assertEqual(counts[-1], 2)


class RenderingTests(unittest.TestCase):
    def test_pie_chart_is_accessible_svg_with_percentages(self):
        svg = render_pie_chart(
            "Languages",
            {"TypeScript": 75, "Python": 25},
            subtitle="Synced from GitHub",
        )

        self.assertIn("<svg", svg)
        self.assertIn("<title>Languages</title>", svg)
        self.assertIn("TypeScript", svg)
        self.assertIn("75.0%", svg)
        self.assertNotIn("<script", svg)

    def test_bar_chart_reports_total_and_has_one_bar_per_bucket(self):
        svg = render_bar_chart(
            "Commits by Week",
            ["W1", "W2", "W3"],
            [0, 2, 1],
            subtitle="Last 3 weeks",
        )

        self.assertIn("3 commits", svg)
        self.assertEqual(svg.count('class="bar"'), 3)
        self.assertNotIn("<script", svg)


if __name__ == "__main__":
    unittest.main()
