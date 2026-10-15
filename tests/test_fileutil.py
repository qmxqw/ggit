"""fileutil 的测试：文本预览（UTF-8 / GBK / 二进制 / 大文件）与文件遍历。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ggit import fileutil  # noqa: E402


class FileUtilTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def w(self, name, data):
        p = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def test_utf8_and_crlf(self):
        fc = fileutil.read_text_file(self.w("a.py", "print('你好')\r\nx = 1\r\n"))
        self.assertEqual(fc.lines, ["print('你好')", "x = 1"])
        self.assertEqual(fc.encoding, "UTF-8")
        self.assertFalse(fc.message)

    def test_gbk_auto_and_forced(self):
        p = self.w("g.txt", "你好，世界\n第二行\n".encode("gbk"))
        fc = fileutil.read_text_file(p)
        self.assertEqual(fc.lines, ["你好，世界", "第二行"])
        self.assertTrue(fc.encoding.startswith("GBK"))
        bad = fileutil.read_text_file(p, "utf-8")
        self.assertNotEqual(bad.lines, fc.lines)
        self.assertEqual(bad.encoding, "UTF-8")

    def test_mixed_encoding_lines(self):
        p = self.w("m.txt", "ascii\n".encode() + "新的".encode() + b"\n" + "旧的".encode("gbk") + b"\n")
        self.assertEqual(fileutil.read_text_file(p).lines, ["ascii", "新的", "旧的"])

    def test_ascii_label(self):
        self.assertEqual(fileutil.read_text_file(self.w("x.txt", "abc\n")).encoding, "ASCII")

    def test_binary_empty_missing_dir(self):
        self.assertTrue(fileutil.read_text_file(self.w("b.bin", b"\x00\x01\x02abc")).is_binary)
        self.assertEqual(fileutil.read_text_file(self.w("e.txt", b"")).message, "空文件。")
        self.assertIn("不存在", fileutil.read_text_file(os.path.join(self.tmp, "nope")).message)
        self.assertIn("目录", fileutil.read_text_file(self.tmp).message)

    def test_big_file_truncated(self):
        line = b"x" * 99 + b"\n"
        p = self.w("big.txt", line * (fileutil.MAX_PREVIEW_BYTES // 100 + 5000))
        fc = fileutil.read_text_file(p)
        self.assertTrue(fc.truncated)
        self.assertGreater(fc.size, fileutil.MAX_PREVIEW_BYTES)
        self.assertTrue(all(len(l) == 99 for l in fc.lines))       # 被截断的半行已丢弃
        self.assertLessEqual(len(fc.lines), fileutil.MAX_PREVIEW_BYTES // 100)

    def test_walk_files(self):
        self.w("a.txt", "1")
        self.w("src/b.py", "2")
        self.w("src/deep/c.py", "3")
        self.w(".git/config", "x")
        self.w(".gitignore", "y")
        self.assertEqual(fileutil.walk_files(self.tmp),
                         [".gitignore", "a.txt", "src/b.py", "src/deep/c.py"])
        self.assertEqual(len(fileutil.walk_files(self.tmp, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
