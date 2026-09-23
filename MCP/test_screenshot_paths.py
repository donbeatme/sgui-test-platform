import tempfile
import unittest
from pathlib import Path

from screenshot_paths import resolve_screenshot_path


class ScreenshotPathsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "case267").mkdir()
        self.image = self.root / "case267" / "step1.png"
        self.image.write_bytes(b"test image")
        self.host = r"D:\SGUI\data\playwright-screenshots"

    def resolve(self, value):
        return resolve_screenshot_path(value, self.root, self.host)

    def test_relative_nested_path(self):
        self.assertEqual(self.resolve("case267/step1.png"), self.image)

    def test_windows_absolute_and_slashes(self):
        for value in [self.host + r"\case267\step1.png", "d:/sgui/data/playwright-screenshots/case267/step1.png"]:
            self.assertEqual(self.resolve(value), self.image)

    def test_container_absolute(self):
        self.assertEqual(self.resolve(str(self.image)), self.image)

    def test_outside_or_traversal_rejected(self):
        for value in ["../step1.png", "case267/../../step1.png", "/etc/passwd", r"C:\Users\secret.png", self.host + r"-other\step1.png"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.resolve(value)

    def test_missing_file_and_nonimage(self):
        with self.assertRaises(FileNotFoundError):
            self.resolve("missing.png")
        with self.assertRaises(ValueError):
            self.resolve("private.env")


if __name__ == "__main__":
    unittest.main()
