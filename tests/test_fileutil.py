"""fileutil 的测试：文本预览（UTF-8 / GBK / 二进制 / 大文件）。"""
import os
import unittest

from support import TempDir

from ggit import fileutil


class FileUtilTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)

    def w(self, name, data):
        p = self.tmp.sub(name)
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
        p = self.w("m.txt", b"ascii\n" + "新的".encode() + b"\n" + "旧的".encode("gbk") + b"\n")
        self.assertEqual(fileutil.read_text_file(p).lines, ["ascii", "新的", "旧的"])

    def test_ascii_label(self):
        self.assertEqual(fileutil.read_text_file(self.w("x.txt", "abc\n")).encoding, "ASCII")

    def test_binary_empty_missing_dir(self):
        self.assertTrue(fileutil.read_text_file(self.w("b.bin", b"\x00\x01\x02abc")).is_binary)
        self.assertEqual(fileutil.read_text_file(self.w("e.txt", b"")).message, "空文件。")
        self.assertIn("不存在", fileutil.read_text_file(self.tmp.sub("nope")).message)
        self.assertIn("目录", fileutil.read_text_file(self.tmp.path).message)

    def test_big_file_truncated(self):
        line = b"x" * 99 + b"\n"
        p = self.w("big.txt", line * (fileutil.MAX_PREVIEW_BYTES // 100 + 5000))
        fc = fileutil.read_text_file(p)
        self.assertTrue(fc.truncated)
        self.assertGreater(fc.size, fileutil.MAX_PREVIEW_BYTES)
        self.assertTrue(all(len(ln) == 99 for ln in fc.lines))       # 被截断的半行已丢弃
        self.assertLessEqual(len(fc.lines), fileutil.MAX_PREVIEW_BYTES // 100)


if __name__ == "__main__":
    unittest.main()
