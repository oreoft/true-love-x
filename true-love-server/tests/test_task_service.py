"""Scheduled tasks push a chosen job from one bot to a list of receivers, once or every day, and live in that bot's database."""

import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytz

from server_env import ServerCase
from true_love_server.services import reminder_service, scheduler_service, task_service

BOT = "wxid_m8s"
OTHER = "wxid_ser"


class TaskServiceCase(ServerCase):
    def setUp(self):
        super().setUp()
        self.register(BOT)
        self.register(OTHER)
        # A stand-in job_process: its own functions can be run by name, imported helpers and private ones cannot.
        self.calls = []
        job_process = types.ModuleType("true_love_server.jobs.job_process")
        job_process.Mock = Mock

        def notice_moyu_schedule(bot_id, room_id):
            self.calls.append(("notice_moyu_schedule", bot_id, room_id))
            if room_id == "坏群":
                raise RuntimeError("wechat busy")

        def notice_usa_moyu_schedule(bot_id, room_id):
            self.calls.append(("notice_usa_moyu_schedule", bot_id, room_id))

        def download_moyu_file():
            self.calls.append(("download_moyu_file", None, None))

        def _private(bot_id, room_id):
            self.calls.append(("_private", bot_id, room_id))

        for func in (notice_moyu_schedule, notice_usa_moyu_schedule, download_moyu_file, _private):
            func.__module__ = job_process.__name__
            setattr(job_process, func.__name__, func)
        for patcher in (patch.object(task_service, "_job_process", lambda: job_process),
                        patch.object(task_service.time, "sleep")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tasks = task_service

    @staticmethod
    def daily(at="09:05", tz="Asia/Shanghai"):
        return {"mode": "daily", "time": at, "timezone": tz}

    @staticmethod
    def next_run(task):
        return datetime.fromisoformat(task["next_run_time"])

    def run_job(self, task_id, bot_id=BOT):
        job = scheduler_service.get_job(bot_id, task_id)
        job.func(**job.kwargs)


class TaskTests(TaskServiceCase):
    def test_daily_task_runs_next_at_that_time_in_its_timezone(self):
        for at, tz in (("09:05", "Asia/Shanghai"), ("08:00", "America/Chicago")):
            with self.subTest(tz=tz):
                task = self.tasks.add_task(BOT, "notice_moyu_schedule", ["委员会"], self.daily(at, tz))

                local = self.next_run(task).astimezone(pytz.timezone(tz))
                self.assertEqual(local.strftime("%H:%M"), at)
                self.assertLess(self.next_run(task) - datetime.now(timezone.utc), timedelta(days=1))

    def test_once_task_runs_at_the_chosen_moment(self):
        run_at = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)

        task = self.tasks.add_task(BOT, "notice_moyu_schedule", ["委员会"], {"mode": "once", "run_at": run_at.isoformat()})

        self.assertEqual(self.next_run(task), run_at)

    def test_tasks_belong_to_one_bot(self):
        self.tasks.add_task(BOT, "notice_usa_moyu_schedule", [" 湾区群 ", "", "委员会", "湾区群"],
                            self.daily("08:00", "America/Chicago"))

        [task] = self.tasks.list_tasks(BOT)
        self.assertEqual(task["receivers"], ["湾区群", "委员会"])
        self.assertEqual(self.tasks.list_tasks(OTHER), [])
        with self.assertRaises(ValueError):
            self.tasks.delete_task(OTHER, task["task_id"])

    def test_reminders_are_not_listed_as_tasks(self):
        reminder_service.add_reminder(BOT, "reminder_委员会_1", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                                      "委员会", "开会")

        self.assertEqual(self.tasks.list_tasks(BOT), [])

    def test_invalid_forms_are_refused(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        cases = {
            "unknown job": ("no_such_job", ["委员会"], self.daily()),
            "private function": ("_private", ["委员会"], self.daily()),
            "imported helper": ("Mock", ["委员会"], self.daily()),
            "no receiver": ("notice_moyu_schedule", ["", " "], self.daily()),
            "receivers not a list": ("notice_moyu_schedule", "委员会", self.daily()),
            "bad time": ("notice_moyu_schedule", ["委员会"], self.daily("25:00")),
            "bad timezone": ("notice_moyu_schedule", ["委员会"], self.daily("09:05", "Mars/Olympus")),
            "past moment": ("notice_moyu_schedule", ["委员会"], {"mode": "once", "run_at": past}),
            "moment without timezone": ("notice_moyu_schedule", ["委员会"], {"mode": "once", "run_at": future[:19]}),
            "no mode": ("notice_moyu_schedule", ["委员会"], {}),
        }
        for name, args in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    self.tasks.add_task(BOT, *args)

        self.assertEqual(self.tasks.list_tasks(BOT), [])

    def test_console_offers_every_function_of_job_process(self):
        self.assertEqual(self.tasks.job_names(), ["notice_moyu_schedule", "notice_usa_moyu_schedule", "download_moyu_file"])

    def test_function_without_parameters_needs_no_receiver_and_no_bot(self):
        task = self.tasks.add_task(BOT, "download_moyu_file", [], self.daily("08:30"))

        self.run_job(task["task_id"])

        self.assertEqual(self.calls, [("download_moyu_file", None, None)])

    def test_changing_a_task_keeps_its_id_and_replaces_everything_else(self):
        task = self.tasks.add_task(BOT, "notice_moyu_schedule", ["委员会"], self.daily())

        self.tasks.update_task(BOT, task["task_id"], "notice_usa_moyu_schedule", ["湾区群"],
                               self.daily("08:00", "America/Chicago"))

        [changed] = self.tasks.list_tasks(BOT)
        self.assertEqual(changed["task_id"], task["task_id"])
        self.assertEqual((changed["job_name"], changed["receivers"]), ("notice_usa_moyu_schedule", ["湾区群"]))

    def test_deleted_task_is_gone(self):
        task = self.tasks.add_task(BOT, "notice_moyu_schedule", ["委员会"], self.daily())

        self.tasks.delete_task(BOT, task["task_id"])

        self.assertEqual(self.tasks.list_tasks(BOT), [])
        with self.assertRaises(ValueError):
            self.tasks.delete_task(BOT, task["task_id"])

    def test_reminder_cannot_be_changed_through_the_task_page(self):
        reminder_service.add_reminder(BOT, "reminder_委员会_1", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                                      "委员会", "开会")

        for call in (lambda: self.tasks.delete_task(BOT, "reminder_委员会_1"),
                     lambda: self.tasks.run_now(BOT, "reminder_委员会_1")):
            with self.assertRaises(ValueError):
                call()

    def test_running_now_starts_an_extra_run_and_keeps_the_schedule(self):
        task = self.tasks.add_task(BOT, "notice_moyu_schedule", ["委员会"], self.daily())
        extra = []
        with patch.object(self.tasks, "_start", extra.append):
            self.tasks.run_now(BOT, task["task_id"])

        self.assertEqual([(run["job_name"], run["receivers"], run["bot_id"]) for run in extra],
                         [("notice_moyu_schedule", ["委员会"], BOT)])
        self.assertEqual(self.tasks.list_tasks(BOT)[0]["next_run_time"], task["next_run_time"])

    def test_ai_trigger_runs_every_task_of_that_job_on_its_bot(self):
        self.tasks.add_task(BOT, "notice_moyu_schedule", ["委员会"], self.daily())
        self.tasks.add_task(BOT, "notice_moyu_schedule", ["家人群"], self.daily("12:00"))
        self.tasks.add_task(OTHER, "notice_moyu_schedule", ["别的号的群"], self.daily())
        self.tasks.add_task(BOT, "notice_usa_moyu_schedule", ["湾区群"], self.daily("08:00", "America/Chicago"))
        extra = []
        with patch.object(self.tasks, "_start", extra.append):
            started = self.tasks.run_by_job_name(BOT, "notice_moyu_schedule")

        self.assertEqual(len(started), 2)
        self.assertEqual(sorted(run["receivers"][0] for run in extra), ["委员会", "家人群"])
        with self.assertRaises(ValueError):
            self.tasks.run_by_job_name(BOT, "no_such_job")

    def test_scheduled_run_calls_the_function_once_per_receiver_in_order_from_its_bot(self):
        task = self.tasks.add_task(OTHER, "notice_moyu_schedule", ["委员会", "家人群"], self.daily())

        self.run_job(task["task_id"], OTHER)

        self.assertEqual(self.calls, [("notice_moyu_schedule", OTHER, "委员会"), ("notice_moyu_schedule", OTHER, "家人群")])

    def test_one_failing_receiver_does_not_stop_the_rest(self):
        task = self.tasks.add_task(BOT, "notice_moyu_schedule", ["坏群", "家人群"], self.daily())

        with self.assertLogs("TaskService", level="ERROR"):
            self.run_job(task["task_id"])

        self.assertEqual(self.calls[-1], ("notice_moyu_schedule", BOT, "家人群"))


class ReminderEditTests(TaskServiceCase):
    """The console edits every field of a reminder, and AI still finds it by its receiver afterwards."""

    def setUp(self):
        super().setUp()
        self.later = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0).isoformat()
        reminder_service.add_reminder(BOT, "reminder_委员会_1", self.later, "委员会", "去开会", "alice")

    def test_every_field_can_be_changed(self):
        even_later = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(microsecond=0).isoformat()

        reminder_service.edit_reminder(BOT, "reminder_委员会_1", "委员会", "去吃饭", even_later, "bob")

        [reminder] = reminder_service.list_all_reminders(BOT)
        self.assertEqual((reminder["job_id"], reminder["content"], reminder["at_user"]),
                         ("reminder_委员会_1", "去吃饭", "bob"))
        self.assertEqual(datetime.fromisoformat(reminder["next_run_time"]), datetime.fromisoformat(even_later))

    def test_new_receiver_is_where_ai_looks_for_it(self):
        reminder_service.edit_reminder(BOT, "reminder_委员会_1", "家人群", "去开会", self.later)

        self.assertEqual(reminder_service.query_reminders(BOT, "委员会"), [])
        [moved] = reminder_service.query_reminders(BOT, "家人群")
        self.assertEqual(moved["content"], "去开会")
        self.assertEqual(len(reminder_service.list_all_reminders(BOT)), 1)

    def test_missing_reminder_or_task_id_is_refused(self):
        task = self.tasks.add_task(BOT, "notice_moyu_schedule", ["委员会"], self.daily())

        for bot_id, job_id in ((BOT, "reminder_nobody_1"), (BOT, task["task_id"]), (OTHER, "reminder_委员会_1")):
            with self.subTest(job_id=job_id):
                with self.assertRaises(ValueError):
                    reminder_service.edit_reminder(bot_id, job_id, "委员会", "去开会", self.later)


if __name__ == "__main__":
    unittest.main()
