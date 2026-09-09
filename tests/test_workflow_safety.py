import pathlib
import unittest


WORKFLOW_PATH = pathlib.Path(".github/workflows/sync-profile-charts.yml")


class WorkflowSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_checkout_tracks_full_latest_main_history(self):
        self.assertIn("fetch-depth: 0", self.workflow)
        self.assertIn("ref: main", self.workflow)

    def test_serializes_profile_chart_updates(self):
        self.assertIn("group: profile-chart-sync", self.workflow)
        self.assertIn("cancel-in-progress: true", self.workflow)

    def test_rebases_remote_main_before_normal_push(self):
        self.assertIn("git pull --rebase origin main", self.workflow)
        self.assertIn("git push origin HEAD:main", self.workflow)
        pull_position = self.workflow.find("git pull --rebase origin main")
        push_position = self.workflow.find("git push origin HEAD:main")

        self.assertLess(pull_position, push_position)
        self.assertNotIn("--force", self.workflow)


if __name__ == "__main__":
    unittest.main()
