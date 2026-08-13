#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netease-music-downloader 离线自测（纯标准库，无需网络与第三方依赖）

运行方式：
  python -m unittest discover -s tests -v     # 从 skill 根目录
  python tests/test_download.py               # 直接运行
"""

import io
import os
import socket
import struct
import sys
import tempfile
import unittest
import urllib.error
import zlib
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import download as dl  # noqa: E402


class TestIsMp3(unittest.TestCase):
    def test_id3_header(self):
        self.assertTrue(dl.is_mp3(b"ID3" + b"\x04\x00\x00" + b"\x00" * 20))

    def test_mpeg_frame_sync(self):
        # 0xFF 0xFB = MPEG1 Layer3 帧头；补齐长度 >1000
        data = b"\xff\xfb" + b"\x00" * 1200
        self.assertTrue(dl.is_mp3(data))

    def test_html_error_page(self):
        """VIP 错误页（HTML）必须被识别为非法音频。"""
        data = b"<html><body>error</body></html>"
        self.assertFalse(dl.is_mp3(data))

    def test_short_junk(self):
        self.assertFalse(dl.is_mp3(b"\xff\xfb\x00"))
        self.assertFalse(dl.is_mp3(b""))

    def test_vip_error_size_constant(self):
        """VIP 错误页特征字节数常量保持 106884（文档与代码一致）。"""
        self.assertEqual(dl.VIP_ERROR_SIZE, 106884)


class TestNormalizeWorkers(unittest.TestCase):
    """worker 数规范化：非法值一律回落为 1（串行安全模式）。"""

    def test_normal(self):
        self.assertEqual(dl._normalize_workers(4), 4)
        self.assertEqual(dl._normalize_workers(1), 1)

    def test_zero_and_negative(self):
        self.assertEqual(dl._normalize_workers(0), 1)
        self.assertEqual(dl._normalize_workers(-3), 1)

    def test_bad_type(self):
        self.assertEqual(dl._normalize_workers("abc"), 1)
        self.assertEqual(dl._normalize_workers(None), 1)


class TestFriendlyNetworkError(unittest.TestCase):
    """网络异常应被转成人话，避免向用户暴露原始栈或 errno。"""

    def test_http_error(self):
        e = urllib.error.HTTPError(
            "https://music.163.com/", 503, "Service Unavailable", {}, None)
        msg = dl._friendly_network_error(e)
        self.assertIn("服务器", msg)
        self.assertIn("503", msg)

    def test_url_error_timeout(self):
        e = urllib.error.URLError(socket.timeout("timed out"))
        msg = dl._friendly_network_error(e)
        self.assertIn("超时", msg)

    def test_socket_timeout(self):
        msg = dl._friendly_network_error(socket.timeout("timed out"))
        self.assertIn("超时", msg)

    def test_no_raw_errno(self):
        """提示语中不应出现 [Errno X] 这类原始错误号。"""
        e = urllib.error.URLError(OSError(11001, "getaddrinfo failed"))
        msg = dl._friendly_network_error(e)
        self.assertNotIn("Errno", msg)


class TestSanitize(unittest.TestCase):
    def test_illegal_windows_chars(self):
        self.assertEqual(dl.sanitize('a/b\\c:d*e?f"g<h>i|j'),
                         "a_b_c_d_e_f_g_h_i_j")

    def test_strip_dots_and_space(self):
        self.assertEqual(dl.sanitize("  歌手 - 歌名  "), "歌手 - 歌名")
        self.assertEqual(dl.sanitize("...歌名..."), "歌名")

    def test_normal_name_kept(self):
        self.assertEqual(dl.sanitize("周杰伦 - 晴天"), "周杰伦 - 晴天")


class TestPlaceholderPng(unittest.TestCase):
    def test_valid_png_magic(self):
        png = dl.make_placeholder_png(seed="test")
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_ihdr_size(self):
        png = dl.make_placeholder_png(size=200, seed="x")
        # 8 签名 + 4 length + 4 "IHDR" + 13 data；宽高在 data 前 8 字节
        w, h = struct.unpack(">II", png[16:24])
        self.assertEqual((w, h), (200, 200))

    def test_idat_decompressible(self):
        png = dl.make_placeholder_png(size=64, seed="歌")
        # 解析所有块，解压 IDAT 验证无损坏
        pos = 8
        idat = b""
        while pos < len(png):
            length = struct.unpack(">I", png[pos:pos + 4])[0]
            tag = png[pos + 4:pos + 8]
            data = png[pos + 8:pos + 8 + length]
            if tag == b"IDAT":
                idat += data
            pos += 12 + length
        raw = zlib.decompress(idat)
        # 64x64 RGB + 每行 1 字节 filter = 64 * (1 + 64*3)
        self.assertEqual(len(raw), 64 * (1 + 64 * 3))

    def test_deterministic_by_seed(self):
        a = dl.make_placeholder_png(seed="同一首歌")
        b = dl.make_placeholder_png(seed="同一首歌")
        c = dl.make_placeholder_png(seed="别的歌")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class TestSongNameParsing(unittest.TestCase):
    def test_filename_format(self):
        """文件名统一「歌手 - 歌名.mp3」，供无 ID3 时兜底解析。"""
        fname = dl.sanitize(f"{'周杰伦'} - {'晴天'}")
        self.assertEqual(fname, "周杰伦 - 晴天")
        self.assertIn(" - ", fname)


class TestWriteId3Mime(unittest.TestCase):
    """有 mutagen 时验证 APIC mime 映射（jpeg/png），无则跳过。"""

    @unittest.skipUnless(
        __import__("importlib.util").util.find_spec("mutagen"),
        "需要 mutagen 才能运行")
    def test_mime_mapping(self):
        import tempfile
        from mutagen.id3 import ID3

        with tempfile.TemporaryDirectory() as tmp:
            mp3 = os.path.join(tmp, "t.mp3")
            with open(mp3, "wb") as f:
                f.write(b"ID3\x04\x00\x00" + b"\x00" * 20 + b"\xff\xfb" + b"\x00" * 1200)

            dl.write_id3(mp3, "歌名", "歌手", "专辑",
                         b"\xff\xd8\xff\xe0" + b"\x00" * 10, "jpeg")
            t = ID3(mp3)
            self.assertEqual(t.getall("APIC")[0].mime, "image/jpeg")

            dl.write_id3(mp3, "歌名", "歌手", "专辑",
                         dl.make_placeholder_png(seed="x"), "png")
            t = ID3(mp3)
            self.assertEqual(t.getall("APIC")[0].mime, "image/png")


class TestPlaylistParsing(unittest.TestCase):
    def test_parse_basic(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# 我的歌单\n晴天 周杰伦\n\n光年之外 邓紫棋\n")
            path = f.name
        try:
            self.assertEqual(dl.parse_playlist(path),
                             ["晴天 周杰伦", "光年之外 邓紫棋"])
        finally:
            os.unlink(path)

    def test_parse_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            dl.parse_playlist("/nonexistent/songs.txt")

    def test_parse_only_comments(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("# 全是注释\n\n")
            path = f.name
        try:
            self.assertEqual(dl.parse_playlist(path), [])
        finally:
            os.unlink(path)


class TestProgressFormat(unittest.TestCase):
    def test_percent_capped(self):
        """进度条百分比不超过 100。"""
        out = io.StringIO()
        old = sys.stderr
        sys.stderr = out
        try:
            dl._show_progress(50, 100)
            dl._show_progress(100, 100)
        finally:
            sys.stderr = old
        text = out.getvalue()
        self.assertIn("100%", text)
        self.assertNotIn("101%", text)
        self.assertIn("下载", text)

    def test_no_total_branch(self):
        out = io.StringIO()
        old = sys.stderr
        sys.stderr = out
        try:
            dl._show_progress(2048, 0)  # 未知总量走 KB 计数
        finally:
            sys.stderr = old
        self.assertIn("KB", out.getvalue())


class TestValidMp3File(unittest.TestCase):
    def test_valid_id3_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a.mp3")
            with open(p, "wb") as f:
                f.write(b"ID3\x04\x00\x00" + b"\x00" * 30)
            # 头是 ID3 但文件太小（<min_bytes）→ 判为无效（试听片段）
            self.assertFalse(dl.is_valid_mp3_file(Path(p), min_bytes=1000))

    def test_valid_mpeg_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "b.mp3")
            with open(p, "wb") as f:
                f.write(b"\xff\xfb" + b"\x00" * 3000)
            self.assertTrue(dl.is_valid_mp3_file(Path(p), min_bytes=1000))

    def test_invalid_text_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "fake.mp3")
            with open(p, "wb") as f:
                f.write(b"<html>" + b"x" * 3000)
            self.assertFalse(dl.is_valid_mp3_file(Path(p), min_bytes=1000))

    def test_missing_file(self):
        self.assertFalse(dl.is_valid_mp3_file(Path("/no/such.mp3")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
