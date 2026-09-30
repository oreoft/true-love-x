"""The logged-in WeChat account is the one whose data directory WeChat wrote to most recently."""

import os
import tempfile
import unittest
from pathlib import Path

from true_love_common.wechat_account import WxidNotFound, current_wxid


class CurrentWxidTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def account(self, name, written_at):
        """An account directory whose database was last written at the given time"""
        db = self.root / name / "db_storage" / "message" / "message_0.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"")
        os.utime(db, (written_at, written_at))
        for path in (db.parent, db.parent.parent, self.root / name):
            os.utime(path, (1, 1))

    def test_the_account_written_most_recently_is_logged_in(self):
        self.account("wxid_49m6kifyirmi12_3ab7", written_at=1_000)
        self.account("wxid_oc36wts1bhxr19_4e4f", written_at=2_000)

        self.assertEqual(current_wxid(self.root), "wxid_oc36wts1bhxr19")

    def test_directories_that_are_not_accounts_are_ignored(self):
        self.account("wxid_ii1pon2s4t4h22_8537", written_at=1_000)
        (self.root / "all_users").mkdir()
        (self.root / "wxid_stray.txt").write_text("")

        self.assertEqual(current_wxid(self.root), "wxid_ii1pon2s4t4h22")

    def test_no_account_is_an_error(self):
        (self.root / "all_users").mkdir()

        with self.assertRaisesRegex(WxidNotFound, "wxid_"):
            current_wxid(self.root)

    def test_missing_data_directory_is_an_error(self):
        with self.assertRaises(WxidNotFound):
            current_wxid(self.root / "missing")

    def test_accounts_written_at_the_same_time_are_not_guessed(self):
        self.account("wxid_49m6kifyirmi12_3ab7", written_at=1_000)
        self.account("wxid_oc36wts1bhxr19_4e4f", written_at=1_000)

        with self.assertRaisesRegex(WxidNotFound, "分不出"):
            current_wxid(self.root)


if __name__ == "__main__":
    unittest.main()
