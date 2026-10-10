"""Values the model passes to a saved shell skill are data: they can never run commands or add options."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from true_love_ai.agent.skill_registry import SkillFailed
from true_love_ai.agent.skills import dynamic_skill_manage as dsm


def shell(template: str, value: str, default: str = "d") -> str:
    command, env = dsm._build_command(template, {"w": {"default": default}}, {"w": value} if value is not None else {})
    result = subprocess.run(command, shell=True, capture_output=True, text=True, env={**os.environ, **env}, timeout=5)
    return result.stdout


class ParameterTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.marker = self.dir / "pwned"
        self.addCleanup(lambda: self.marker.unlink(missing_ok=True))

    def test_values_that_try_to_run_commands_stay_plain_text(self):
        payloads = [
            f"x\ntouch {self.marker}",
            f"x; touch {self.marker}",
            f"x && touch {self.marker}",
            f"x | touch {self.marker}",
            f"$(touch {self.marker})",
            f"`touch {self.marker}`",
            f"x' ; touch {self.marker} ; echo '",
            f'x" ; touch {self.marker} ; echo "',
            f"x > {self.marker}",
        ]
        templates = ["echo got-{w}", "echo 'got-{w}'", 'echo "got-{w}"', "echo 'a{w}b'", 'echo "a={w}&b"',
                     "echo {w} | cat"]
        for template in templates:
            for payload in payloads:
                with self.subTest(template=template, payload=payload):
                    out = shell(template, payload)
                    self.assertFalse(self.marker.exists(), f"{template!r} with {payload!r} ran a command")
                    self.assertIn(payload, out)

    def test_values_are_one_argument_whatever_the_quotes_around_the_placeholder(self):
        for template in ("printf '[%s]' {w}", "printf '[%s]' '{w}'", 'printf "[%s]" "{w}"'):
            with self.subTest(template=template):
                self.assertEqual(shell(template, "hello world  *"), "[hello world  *]")

    def test_placeholder_inside_a_quoted_url_still_works(self):
        self.assertEqual(shell("echo 'https://x.test/q?s={w}&n=1'", "a b"), "https://x.test/q?s=a b&n=1\n")
        self.assertEqual(shell('echo "https://x.test/q?s={w}&n=$((1+1))"', "a"), "https://x.test/q?s=a&n=2\n")

    def test_defaults_are_used_when_the_model_passes_nothing(self):
        self.assertEqual(shell("echo hello-{w}", None, default="world"), "hello-world\n")

    def test_option_like_values_are_refused(self):
        with self.assertRaises(ValueError):
            dsm._build_command("curl {w}", {"w": {}}, {"w": "-o/etc/x"})

    def test_escaped_quotes_in_the_template_do_not_confuse_the_quoting(self):
        self.assertEqual(shell("echo \\'{w}", f"; touch {self.marker}"), f"'; touch {self.marker}\n")
        self.assertFalse(self.marker.exists())

    def test_other_braces_in_the_template_are_left_alone(self):
        self.assertEqual(shell("echo {w} {other} ${HOME:+x}", "a"), "a {other} x\n")


class SkillRunTests(unittest.IsolatedAsyncioTestCase):
    async def run_skill(self, command: str, params: dict, parameters=None):
        skill = {"id": "s1", "name": "回声", "command": command,
                 "parameters": json.dumps(parameters or {"word": {"default": "a"}})}
        with patch.object(dsm._ss, "get_skill", return_value=skill), \
                patch.object(dsm, "check_permission", return_value=True), \
                patch.object(dsm._ss, "increment_skill_usage"):
            return await dsm.skill_run({"id": "s1", "params": params}, {})

    async def test_output_comes_back(self):
        self.assertEqual(await self.run_skill("echo got-{word}", {"word": "hi there"}), "got-hi there")

    async def test_injection_through_skill_run_does_nothing(self):
        marker = Path(tempfile.mkdtemp()) / "pwned"
        out = await self.run_skill("echo got-{word}", {"word": f"x\ntouch {marker}"})

        self.assertFalse(marker.exists())
        self.assertEqual(out, f"got-x\ntouch {marker}")

    async def test_refused_parameter_is_told_to_the_model(self):
        self.assertIn("参数错误", await self.run_skill("echo {word}", {"word": "--help"}))

    async def test_failing_command_is_a_skill_failure(self):
        with self.assertRaises(SkillFailed):
            await self.run_skill("exit 3", {})


if __name__ == "__main__":
    unittest.main()
