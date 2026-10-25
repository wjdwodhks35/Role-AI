from pathlib import Path
import re
import unittest


class ProjectMapTests(unittest.TestCase):
    def test_index_paths_exist(self):
        root = Path(__file__).resolve().parents[3]
        document = root / 'docs/CODEX_PROJECT_MAP.md'
        paths = re.findall(r'^\| `([^`]+)` \|', document.read_text(), re.M)
        self.assertGreater(len(paths), 15)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((root / path).exists(), path)
