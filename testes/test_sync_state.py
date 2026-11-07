"""Exercise the checkpoint hook with temporary local Git repositories only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_state.py"


class StateSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.remote = self.base / "remote.git"
        self.repo = self.base / "repo"
        self.run_git(self.base, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.run_git(self.base, "clone", str(self.remote), str(self.repo))
        (self.repo / "README.txt").write_text("Test fixture, no actual publishing.\n")
        self.run_git(self.repo, "add", "README.txt")
        self.run_git(self.repo, "commit", "-m", "Initial fixture")
        self.run_git(self.repo, "push", "-u", "origin", "main")
        self.state = self.repo / "runtime/state.json"
        self.state.parent.mkdir(mode=0o700)
        self.write_state()
        self.env = os.environ.copy()
        self.env.update(GITHUB_ACTIONS="true", GITHUB_REF_NAME="main",
                        IG_ACCESS_TOKEN="private-meta-token-fixture-never-log")

    @staticmethod
    def run_git(cwd, *arguments):
        result = subprocess.run(
            ["git", "-c", "user.name=Test Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", *arguments], cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        if result.returncode:
            raise AssertionError("Fixture Git operation failed: " + repr(arguments))
        return result.stdout

    def write_state(self, marker="prepared"):
        value = {"schema_version": 1, "account": "acciolymarceloo",
                 "pieces": {"01-fixture": {"state": marker}}}
        self.state.write_text(json.dumps(value) + "\n")
        self.state.chmod(0o600)

    def invoke(self, path=None, environment=None):
        return subprocess.run([sys.executable, str(SCRIPT), str(path or self.state)],
                              cwd=self.repo, env=environment or self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def remote_head(self):
        return self.run_git(self.base, "--git-dir", str(self.remote), "rev-parse", "main")

    def assert_safe_failure(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.env["IG_ACCESS_TOKEN"].encode(), result.stdout + result.stderr)
        self.assertNotIn(str(self.remote).encode(), result.stdout + result.stderr)
        self.assertIn(b"publication must stop", result.stderr)

    def test_state_is_committed_pushed_and_verified_without_staging_other_files(self):
        (self.repo / "untracked.txt").write_text("Must remain local.\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        stored = self.run_git(self.base, "--git-dir", str(self.remote), "show", "main:runtime/state.json")
        self.assertEqual(stored, self.state.read_bytes())
        self.assertEqual(self.remote_head(), self.run_git(self.repo, "rev-parse", "HEAD"))
        names = self.run_git(self.repo, "show", "--format=", "--name-only", "HEAD").decode().strip()
        self.assertEqual(names, "runtime/state.json")
        self.assertTrue((self.repo / "untracked.txt").exists())
        self.assertEqual(self.run_git(self.repo, "diff", "--cached", "--name-only"), b"")

    def test_repeated_identical_state_does_not_create_a_commit(self):
        self.assertEqual(self.invoke().returncode, 0)
        before = self.remote_head()
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual(before, self.remote_head())
        self.write_state("processing")
        self.assertEqual(self.invoke().returncode, 0)
        self.assertNotEqual(before, self.remote_head())

    def test_wrong_path_is_rejected_without_remote_mutation(self):
        other = self.repo / "other.json"
        other.write_bytes(self.state.read_bytes())
        other.chmod(0o600)
        before = self.remote_head()
        self.assert_safe_failure(self.invoke(other))
        self.assertEqual(before, self.remote_head())

    def test_remote_change_requires_reconciliation_without_merge_or_reset(self):
        other = self.base / "other"
        self.run_git(self.base, "clone", str(self.remote), str(other))
        (other / "elsewhere.txt").write_text("Concurrent update.\n")
        self.run_git(other, "add", "elsewhere.txt")
        self.run_git(other, "commit", "-m", "Other writer")
        self.run_git(other, "push", "origin", "main")
        local_before = self.run_git(self.repo, "rev-parse", "HEAD")
        remote_before = self.remote_head()
        self.assert_safe_failure(self.invoke())
        self.assertEqual(local_before, self.run_git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(remote_before, self.remote_head())
        self.assertEqual(self.run_git(self.repo, "diff", "--cached", "--name-only"), b"")

    def test_preexisting_staged_file_is_preserved_and_blocks_sync(self):
        (self.repo / "staged.txt").write_text("Must not be committed.\n")
        self.run_git(self.repo, "add", "staged.txt")
        before = self.remote_head()
        self.assert_safe_failure(self.invoke())
        self.assertEqual(before, self.remote_head())
        self.assertEqual(self.run_git(self.repo, "diff", "--cached", "--name-only"), b"staged.txt\n")

    def test_actions_and_valid_branch_are_required(self):
        for changes in ({"GITHUB_ACTIONS": "false"}, {"GITHUB_REF_NAME": "bad..branch"},
                        {"GITHUB_REF_NAME": "-bad"}):
            with self.subTest(changes=changes):
                before = self.remote_head()
                self.assert_safe_failure(self.invoke(environment={**self.env, **changes}))
                self.assertEqual(before, self.remote_head())

    def test_explicit_state_branch_uses_default_branch_checkout(self):
        environment = {**self.env, "ACCIOLY_STATE_BRANCH": "main", "GITHUB_REF_NAME": "another-branch"}
        self.assertEqual(self.invoke(environment=environment).returncode, 0)
        stored = self.run_git(self.base, "--git-dir", str(self.remote), "show", "main:runtime/state.json")
        self.assertEqual(stored, self.state.read_bytes())

    def test_inherited_git_tracing_is_not_allowed_to_write_a_log(self):
        trace = self.base / "git-trace.txt"
        result = self.invoke(environment={**self.env, "GIT_TRACE": str(trace),
                                          "GIT_TRACE_CURL": str(trace), "GIT_CURL_VERBOSE": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(trace.exists())
        self.assertNotIn(self.env["IG_ACCESS_TOKEN"].encode(), result.stdout + result.stderr)

    def test_symlink_or_publicly_readable_state_is_rejected(self):
        self.state.chmod(0o644)
        self.assert_safe_failure(self.invoke())
        self.state.chmod(0o600)
        target = self.base / "external.json"
        target.write_bytes(self.state.read_bytes())
        target.chmod(0o600)
        self.state.unlink()
        self.state.symlink_to(target)
        self.assert_safe_failure(self.invoke())


if __name__ == "__main__":
    unittest.main()
