"""Values the model passes to a saved shell skill are data: they can never run commands or add options."""

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from true_love_ai.agent.skill_registry import SkillFailed
from true_love_ai.agent.skills import dynamic_skill_manage as dsm


def run(command: str, env: dict) -> str:
    return subprocess.run(command, shell=True, capture_output=True, text=True, env={**os.environ, **env},
                          timeout=5).stdout


def shell(template: str, value: str | None, default: str = "d") -> str:
    command, env = dsm._build_command(template, {"w": {"default": default}}, {"w": value} if value is not None else {})
    return run(command, env)


def raw_shell(template: str, value: str) -> str:
    """Only the quoting layer: the value goes straight into the environment, skipping the value filter"""
    return run(dsm._reference_vars(template, {"w": "TL_ARG_0"}), {"TL_ARG_0": value})


def alive(pid: int) -> bool:
    """Running, not a zombie waiting to be reaped; /proc where there is one (Linux), signal 0 elsewhere (macOS)"""
    stat = Path(f"/proc/{pid}/stat")
    if Path("/proc/self/stat").exists():
        try:
            return stat.read_text().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
        except FileNotFoundError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class QuotingTests(unittest.TestCase):
    """The outer shell never parses a value, whatever is in it"""

    def setUp(self):
        self.marker = Path(tempfile.mkdtemp()) / "pwned"

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
                    out = raw_shell(template, payload)
                    self.assertFalse(self.marker.exists(), f"{template!r} with {payload!r} ran a command")
                    self.assertIn(payload, out)

    def test_values_are_one_argument_whatever_the_quotes_around_the_placeholder(self):
        for template in ("printf '[%s]' {w}", "printf '[%s]' '{w}'", 'printf "[%s]" "{w}"'):
            with self.subTest(template=template):
                self.assertEqual(shell(template, "hello world  *"), "[hello world  *]")

    def test_placeholder_inside_a_quoted_url_still_works(self):
        self.assertEqual(shell("echo 'https://x.test/q?s={w}&n=1'", "a b"), "https://x.test/q?s=a b&n=1\n")
        self.assertEqual(shell('echo "https://x.test/q?s={w}&n=$((1+1))"', "a"), "https://x.test/q?s=a&n=2\n")

    def test_escaped_quotes_in_the_template_do_not_confuse_the_quoting(self):
        self.assertEqual(raw_shell("echo \\'{w}", f"; touch {self.marker}"), f"'; touch {self.marker}\n")
        self.assertFalse(self.marker.exists())

    def test_other_braces_in_the_template_are_left_alone(self):
        self.assertEqual(shell("echo {w} {other} ${HOME:+x}", "a"), "a {other} x\n")


class ValueFilterTests(unittest.TestCase):
    """A template may hand the value to another interpreter (bash -c, ssh, eval), so risky characters are refused"""

    def test_characters_that_could_start_a_new_command_are_refused(self):
        for value in ("a;b", "a&b", "a|b", "`id`", "$(id)", "$HOME", "a>b", "a<b", "a\\b", "a\nb", "a\rb"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dsm._build_command("bash -c 'echo {w}'", {"w": {}}, {"w": value})

    def test_a_nested_interpreter_gets_ordinary_values(self):
        self.assertEqual(shell("bash -c 'echo got-{w}'", "hello world"), "got-hello world\n")

    def test_quotes_are_allowed_and_stay_literal_in_the_outer_shell(self):
        self.assertEqual(shell("echo {w}", "McDonald's \"big\" mac"), "McDonald's \"big\" mac\n")

    def test_option_like_values_are_refused_but_negative_numbers_are_fine(self):
        with self.assertRaises(ValueError):
            dsm._build_command("curl {w}", {"w": {}}, {"w": "-o/etc/x"})
        self.assertEqual(shell("echo {w}", "-3"), "-3\n")
        self.assertEqual(shell("echo {w}", "-2.5"), "-2.5\n")

    def test_defaults_are_trusted_and_used_when_the_model_passes_nothing(self):
        self.assertEqual(shell("echo hello-{w}", None, default="world"), "hello-world\n")
        self.assertEqual(shell("echo {w}", None, default="-n5"), "-n5\n")


class ParameterNameTests(unittest.TestCase):
    """The model cannot pick names that rewrite braces the template already had"""

    def test_a_made_up_name_cannot_replace_braces_in_the_template(self):
        with self.assertRaises(ValueError):
            dsm._build_command("curl {url} | awk '{print $1}'", {"url": {}}, {"url": "x", "print $1": "evil"})

    def test_only_declared_names_are_accepted_when_the_skill_declares_some(self):
        with self.assertRaises(ValueError):
            dsm._build_command("echo {url} {other}", {"url": {}}, {"other": "x"})
        command, env = dsm._build_command("echo {url}", {"url": {}}, {"url": "x"})
        self.assertEqual(run(command, env), "x\n")

    def test_skills_saved_without_definitions_still_take_plain_names(self):
        command, env = dsm._build_command("echo pkg={package}", {}, {"package": "requests"})
        self.assertEqual(run(command, env), "pkg=requests\n")
        for name in ("print $1", "a b", "x}", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dsm._build_command("awk '{print $1}' {package}", {}, {name: "evil"})

    def test_parameter_definitions_that_are_not_an_object_are_an_argument_error(self):
        with self.assertRaises(ValueError):
            dsm._build_command("echo {w}", [], {})


class SaveParametersTests(unittest.TestCase):
    def test_definitions_must_be_a_json_object(self):
        from true_love_ai.memory.dynamic_skill_service import normalize_parameters
        for bad in ("[]", "null", "1", '"x"', [], ["a"], 3):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_parameters(bad)
        for bad_name in ('{"print $1": {}}', {"a b": {}}, {"x}": {}}):
            with self.subTest(bad_name=bad_name), self.assertRaises(ValueError):
                normalize_parameters(bad_name)
        self.assertIsNone(normalize_parameters("{}"))
        self.assertIsNone(normalize_parameters(None))
        self.assertEqual(json.loads(normalize_parameters('{"w": {"default": "a"}}')), {"w": {"default": "a"}})


class SkillRunTests(unittest.IsolatedAsyncioTestCase):
    async def run_skill(self, command: str, params: dict, parameters=None):
        if parameters is None:
            parameters = {"word": {"default": "a"}}
        skill = {"id": "s1", "name": "回声", "command": command,
                 "parameters": json.dumps(parameters) if parameters != "" else None}
        with patch.object(dsm._ss, "get_skill", return_value=skill), \
                patch.object(dsm, "check_permission", return_value=True), \
                patch.object(dsm._ss, "increment_skill_usage"):
            return await dsm.skill_run({"id": "s1", "params": params}, {})

    async def test_output_comes_back(self):
        self.assertEqual(await self.run_skill("echo got-{word}", {"word": "hi there"}), "got-hi there")

    async def test_injection_through_skill_run_is_refused(self):
        marker = Path(tempfile.mkdtemp()) / "pwned"
        for payload in (f"x\ntouch {marker}", f"x; touch {marker}"):
            with self.subTest(payload=payload):
                out = await self.run_skill("bash -c 'echo got-{word}'", {"word": payload})

                self.assertFalse(marker.exists())
                self.assertIn("参数错误", out)

    async def test_refused_parameter_is_told_to_the_model(self):
        self.assertIn("参数错误", await self.run_skill("echo {word}", {"word": "--help"}))

    async def test_an_undeclared_parameter_is_told_to_the_model(self):
        out = await self.run_skill("echo {word} | awk '{print $1}'", {"word": "a", "print $1": "evil"})

        self.assertIn("参数错误", out)

    async def test_broken_stored_definitions_are_an_argument_error_not_a_crash(self):
        self.assertIn("参数错误", await self.run_skill("echo {w}", {}, parameters=[]))

    async def test_a_command_that_runs_too_long_is_killed_with_its_children(self):
        pid_file = Path(tempfile.mkdtemp()) / "child.pid"
        with patch.object(dsm, "_EXEC_TIMEOUT", 0.5), self.assertRaises(SkillFailed):
            await self.run_skill(f"sleep 30 & echo $! | tee {pid_file}; wait", {}, parameters="")

        child = int(pid_file.read_text())
        self.assertFalse(alive(child), "the child the shell started is still running")

    async def test_a_cancelled_skill_kills_its_command_too(self):
        """when tl-ai stops, the task running a skill is cancelled; its shell and children must not outlive it"""
        pid_file = Path(tempfile.mkdtemp()) / "child.pid"
        task = asyncio.create_task(self.run_skill(f"sleep 30 & echo $! | tee {pid_file}; wait", {}, parameters=""))
        for _ in range(100):
            if pid_file.exists() and pid_file.read_text().strip():
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertFalse(alive(int(pid_file.read_text())), "the command kept running after the skill was cancelled")

    async def test_a_strange_declared_name_cannot_be_passed_by_the_model(self):
        """skills saved before names were checked may still declare one"""
        out = await self.run_skill("awk '{print $1}' {w}", {"print $1": "evil"}, parameters={"print $1": {}, "w": {}})

        self.assertIn("参数错误", out)

    async def test_failing_command_is_a_skill_failure(self):
        with self.assertRaises(SkillFailed):
            await self.run_skill("exit 3", {})


if __name__ == "__main__":
    unittest.main()
