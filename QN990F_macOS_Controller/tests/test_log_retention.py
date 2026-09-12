"""Test both platforms' actual logging setup without starting either controller."""
import ast
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import plistlib
import re
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).parents[2]


def load_logging(platform, directory):
    path = ROOT / f"QN990F_{platform}_Controller" / "QN990FController.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = []
    in_setup = False
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "BoundedRotatingFileHandler":
            nodes.append(node)
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if any(name.startswith("LOG_MAX_") or name == "LOG_BACKUP_COUNT" for name in names):
                nodes.append(node)
            if "logger" in names:
                in_setup = True
        if in_setup:
            nodes.append(node)
            if isinstance(node, ast.If):
                break
    logger = logging.Logger(f"retention-{platform}")
    namespace = dict(logging=logging, RotatingFileHandler=RotatingFileHandler,
                     os=os, LOG_FILE=directory / "controller.log")
    with mock.patch.object(logging, "getLogger", return_value=logger):
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return logger, namespace


class LogRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def logging_for(self, platform):
        directory = Path(self.temporary.name) / platform
        directory.mkdir(exist_ok=True)
        logger, namespace = load_logging(platform, directory)
        self.addCleanup(lambda: [handler.close() for handler in logger.handlers])
        self.assertEqual(len(logger.handlers), 1)
        self.assertIsInstance(logger.handlers[0], RotatingFileHandler)
        self.assertEqual(logger.handlers[0].maxBytes, 1_000_000)
        self.assertEqual(logger.handlers[0].backupCount, 3)
        return directory, logger, namespace

    def assert_bounded(self, directory):
        files = list(directory.glob("controller.log*"))
        self.assertLessEqual(len(files), 4)
        for path in files:
            self.assertIn(path.name, ("controller.log", "controller.log.1",
                                      "controller.log.2", "controller.log.3"))
            self.assertLessEqual(path.stat().st_size, 1_000_000, path.name)
            path.read_text(encoding="utf-8")  # Truncation must preserve valid UTF-8.
        self.assertLessEqual(sum(path.stat().st_size for path in files), 4_000_000)

    def test_repeated_rollover_keeps_only_the_four_latest_files(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                directory, logger, _ = self.logging_for(platform)
                for index in range(1600):
                    logger.info("record-%04d %s", index, "x" * 8000)
                self.assert_bounded(directory)
                self.assertEqual(len(list(directory.glob("controller.log*"))), 4)
                self.assertIn("record-1599", (directory / "controller.log").read_text())
                self.assertNotIn("record-0000", "".join(p.read_text() for p in directory.glob("controller.log*")))
                for handler in logger.handlers:
                    handler.close()
                _, restarted, _ = self.logging_for(platform)
                restarted.info("after-restart")
                self.assert_bounded(directory)
                self.assertIn("after-restart", (directory / "controller.log").read_text())

    def test_a_single_huge_exception_cannot_create_a_huge_log(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                directory, logger, _ = self.logging_for(platform)
                try:
                    raise RuntimeError("start-of-error " + "x" * 2_000_000 + " end-of-error")
                except RuntimeError:
                    logger.exception("Unexpected controller failure")
                self.assert_bounded(directory)
                output = (directory / "controller.log").read_text()
                self.assertIn("Unexpected controller failure", output)
                self.assertIn("end-of-error", output)
                self.assertIn("[log record truncated]", output)

    def test_utf8_and_multiline_records_obey_the_byte_limit(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                directory, logger, _ = self.logging_for(platform)
                for index in range(200):
                    logger.info("record-%03d %s", index, "\U0001f4fa\n" * 2000)
                    self.assert_bounded(directory)

    def test_windows_crlf_translation_is_counted_before_rollover(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                _, logger, _ = self.logging_for(platform)
                handler = logger.handlers[0]
                record = logger.makeRecord(logger.name, logging.INFO, __file__, 1,
                                           "first\nsecond\n\U0001f4fa", (), None)
                size = len((handler.format(record) + "\n").replace("\n", "\r\n").encode("utf-8"))
                # Simulate a file one byte short of fitting a Windows record.
                stream = mock.Mock()
                stream.tell.return_value = handler.maxBytes - size + 1
                with mock.patch.object(handler, "stream", stream), mock.patch.object(os, "linesep", "\r\n"):
                    self.assertTrue(handler.shouldRollover(record))

    def test_malformed_unicode_is_escaped_without_disabling_logging(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                directory, logger, _ = self.logging_for(platform)
                logger.warning("malformed: %s", "\ud800" * 9000)
                logger.info("still-running")
                self.assert_bounded(directory)
                self.assertIn("still-running", (directory / "controller.log").read_text())

    def test_both_platforms_use_identical_bounding_implementations(self):
        implementations = []
        for platform in ("macOS", "Windows"):
            _, _, namespace = self.logging_for(platform)
            self.assertEqual(namespace["LOG_MAX_RECORD_CHARS"], 8192)
            tree = ast.parse((ROOT / f"QN990F_{platform}_Controller/QN990FController.py").read_text())
            implementations.append(ast.dump(next(n for n in tree.body if isinstance(n, ast.ClassDef)
                                                 and n.name == "BoundedRotatingFileHandler")))
        self.assertEqual(*implementations)

    def test_macos_launch_agents_do_not_append_unbounded_output_logs(self):
        installer = (ROOT / "QN990F_macOS_Controller/Install-QN990FController.sh").read_text()
        plists = re.findall(r"<\?xml.*?</plist>", installer, re.DOTALL)
        self.assertEqual(len(plists), 2)
        for xml in plists:
            plist = plistlib.loads(xml.encode())
            self.assertEqual(plist["StandardOutPath"], "/dev/null")
            self.assertEqual(plist["StandardErrorPath"], "/dev/null")


if __name__ == "__main__":
    unittest.main()
