"""Exercise production checkout safeguards without deploying containers."""

import base64
import http.client
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent


def git(directory, *args):
    env = os.environ.copy()
    env.update(
        GIT_AUTHOR_NAME="CI",
        GIT_AUTHOR_EMAIL="ci@example.invalid",
        GIT_COMMITTER_NAME="CI",
        GIT_COMMITTER_EMAIL="ci@example.invalid",
    )
    return subprocess.run(
        ["git", *args],
        cwd=directory,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class ProductionDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        tmp = Path(self.temporary_directory.name)
        origin = tmp / "origin.git"
        self.seed = tmp / "seed"
        self.checkout = tmp / "checkout"
        self.docker_log = tmp / "docker.log"
        git(tmp, "init", "--bare", "--initial-branch=main", str(origin))
        git(tmp, "clone", str(origin), str(self.seed))
        for path in ("css", "js"):
            directory = self.seed / "olc_webportalv2" / "static" / path
            directory.mkdir(parents=True)
            (directory / "asset").write_text("asset\n")
        (self.seed / "tracked").write_text("original\n")
        (self.seed / ".gitignore").write_text("GoC-GdC-Root-A.crt\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "initial")
        git(self.seed, "push", "-u", "origin", "main")
        git(tmp, "clone", str(origin), str(self.checkout))
        (self.checkout / "GoC-GdC-Root-A.crt").write_text("original certificate\n")

        self.tools = tmp / "bin"
        self.tools.mkdir()
        docker = self.tools / "docker"
        docker.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
            'if [ "$1 $2" = "compose exec" ] && [ "${FAIL_HTTP:-0}" = 1 ]; '
            'then exit 1; fi\n'
        )
        docker.chmod(0o755)
        sleep = self.tools / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)

        pipeline = (ROOT / "azure-pipelines-update.yml").read_text()
        script = textwrap.dedent(
            pipeline.split("<<'EOF'\n", 1)[1].split("\n        EOF", 1)[0]
        )
        production_cd = 'cd "/home/cfiaadmin/FoodPort/olc_genomics_portal"'
        if script.count(production_cd) != 1:
            raise RuntimeError("Cannot safely redirect the production checkout for testing")
        self.script = script.replace(production_cd, f"cd {shlex.quote(str(self.checkout))}")

    def deploy(self, fail_http=False):
        env = os.environ.copy()
        env.update(
            PATH=f"{self.tools}:{env['PATH']}",
            DOCKER_LOG=str(self.docker_log),
            FAIL_HTTP="1" if fail_http else "0",
            FOODPORT_ROOT_CA_B64=base64.b64encode(b"new certificate\n").decode(),
        )
        return subprocess.run(
            ["bash"], input=self.script, env=env, capture_output=True, text=True
        )

    def make_hotfix_branch(self):
        git(self.seed, "switch", "-c", "foodport")
        git(self.seed, "push", "-u", "origin", "foodport")
        git(self.checkout, "fetch", "origin")
        git(self.checkout, "switch", "--track", "origin/foodport")

    def test_uncommitted_changes_block_deployment(self):
        for change in ("tracked", "untracked"):
            with self.subTest(change=change):
                path = self.checkout / change
                original = path.read_text() if path.exists() else None
                path.write_text("local work\n")
                result = self.deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("uncommitted or untracked changes", result.stderr)
                self.assertFalse(self.docker_log.exists())
                self.assertEqual(
                    (self.checkout / "GoC-GdC-Root-A.crt").read_text(),
                    "original certificate\n",
                )
                if original is None:
                    path.unlink()
                else:
                    path.write_text(original)

    def test_unpushed_hotfix_blocks_deployment(self):
        self.make_hotfix_branch()
        (self.checkout / "tracked").write_text("unpushed hotfix\n")
        git(self.checkout, "commit", "-am", "hotfix")

        result = self.deploy()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("commits not pushed", result.stderr)
        self.assertEqual(git(self.checkout, "branch", "--show-current"), "foodport")
        self.assertFalse(self.docker_log.exists())

    def test_stale_tracking_ref_is_refreshed_before_check(self):
        self.make_hotfix_branch()
        old_commit = git(self.checkout, "rev-parse", "origin/foodport")
        (self.checkout / "tracked").write_text("pushed hotfix\n")
        git(self.checkout, "commit", "-am", "hotfix")
        git(self.checkout, "push")
        git(self.checkout, "update-ref", "refs/remotes/origin/foodport", old_commit)

        result = self.deploy()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.checkout, "branch", "--show-current"), "main")

    def test_deleted_upstream_blocks_deployment(self):
        self.make_hotfix_branch()
        git(self.seed, "push", "origin", "--delete", "foodport")

        result = self.deploy()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("couldn't find remote ref", result.stderr)
        self.assertEqual(git(self.checkout, "branch", "--show-current"), "foodport")
        self.assertFalse(self.docker_log.exists())

    def test_ahead_local_main_blocks_deployment_from_hotfix(self):
        (self.checkout / "tracked").write_text("unpushed main\n")
        git(self.checkout, "commit", "-am", "main work")
        self.make_hotfix_branch()

        result = self.deploy()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Local main has commits not on origin/main", result.stderr)
        self.assertEqual(git(self.checkout, "branch", "--show-current"), "foodport")
        self.assertFalse(self.docker_log.exists())

    def test_detached_or_untracked_branch_blocks_deployment(self):
        git(self.checkout, "switch", "--detach")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checkout is detached", result.stderr)
        git(self.checkout, "switch", "-c", "local-only")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no origin upstream", result.stderr)
        self.assertFalse(self.docker_log.exists())

    def test_clean_hotfix_switches_and_updates_main(self):
        self.make_hotfix_branch()
        git(self.seed, "switch", "main")
        (self.seed / "tracked").write_text("updated main\n")
        git(self.seed, "commit", "-am", "update main")
        git(self.seed, "push")

        result = self.deploy()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.checkout, "branch", "--show-current"), "main")
        self.assertEqual((self.checkout / "tracked").read_text(), "updated main\n")
        self.assertEqual(
            (self.checkout / "GoC-GdC-Root-A.crt").read_text(),
            "new certificate\n",
        )
        self.assertIn(
            "compose up -d --build --wait --wait-timeout 300",
            self.docker_log.read_text(),
        )
        self.assertIn("Production web HTTP check passed.", result.stdout)

    def test_web_http_failure_fails_deployment(self):
        result = self.deploy(fail_http=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production web did not respond successfully", result.stderr)
        self.assertIn("compose ps -a", self.docker_log.read_text())

    def test_http_probe_uses_an_allowed_host(self):
        line = next(line for line in self.script.splitlines() if "HTTPConnection" in line)
        command = shlex.split(line.strip()[3:].removesuffix("; then"))
        probe = command[command.index("-c") + 1]
        prod = types.ModuleType("config.settings.prod")
        connection = mock.Mock()
        connection.getresponse.return_value.status = 200
        with mock.patch.dict(sys.modules, {"config.settings.prod": prod}):
            with mock.patch.object(http.client, "HTTPConnection", return_value=connection):
                for allowed, expected in (
                    (["foodport.example"], "foodport.example"),
                    ([".foodport.example"], "foodport.example"),
                    (["*"], "localhost"),
                ):
                    with self.subTest(allowed=allowed):
                        prod.ALLOWED_HOSTS = allowed
                        exec(probe, {})
                        connection.request.assert_called_with(
                            "GET", "/", headers={"Host": expected}
                        )


if __name__ == "__main__":
    unittest.main()
