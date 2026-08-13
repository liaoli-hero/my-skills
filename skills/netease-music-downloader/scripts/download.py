#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云音乐批量下载器（免费可外链版本）

下载免费可外链歌曲到本地曲库，自动附带：
  - 同名 .lrc 歌词（纯音乐除外）
  - ID3 标签（TIT2 标题 / TPE1 歌手 / TALB 专辑 / APIC 内嵌封面）

用法示例：
  python download.py "晴天 周杰伦"
  python download.py "光年之外" "平凡之路" "Canon in D"          # 一次多首
  python download.py "孤勇者" --dir "D:/Music/点歌器曲库"         # 指定输出目录
  python download.py "成都 赵雷" --count 20                       # 放宽搜索数量
  python download.py "卡农" --no-lyric --no-cover                # 只要音频
  python download.py --install-deps --pip-index https://pypi.tuna.tsinghua.edu.cn/simple  # 先装依赖
  python download.py --playlist songs.txt --dir "D:/Music/曲库"   # 歌单批量
  python -m unittest discover -s tests -v                        # 运行自测

依赖：Python 3.8+，仅需 mutagen（pip install mutagen）
注意：仅能下载网易云标记为免费（fee=0）且允许外链的歌曲；VIP 原版会失败并自动跳过。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_SEARCH = "https://music.163.com/api/search/get"
API_LYRIC = "https://music.163.com/api/song/lyric"
API_ALBUM = "https://music.163.com/api/album"
URL_OUTER = "https://music.163.com/song/media/outer/url"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Referer": "https://music.163.com/",
}

# VIP/版权受限歌曲的外链统一返回这个字节数的 HTML 错误页
VIP_ERROR_SIZE = 106884


def http_get(url, timeout=25, binary=False):
    """GET 请求，统一带 UA + Referer，跟随重定向。"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_get_json(url, timeout=25):
    return json.loads(http_get(url, timeout).decode("utf-8", "replace"))


def is_mp3(data):
    """校验字节流是否为真实 MP3：ID3 头 或 MPEG 帧同步 0xFFEx。"""
    if data[:3] == b"ID3":
        return True
    return len(data) > 1000 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def sanitize(name):
    """文件名安全化（Windows 非法字符 + 首尾空白）。"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name.strip().strip(".")


def search_songs(kw, limit=30):
    """搜索歌曲，返回候选列表（按热度降序）。"""
    url = API_SEARCH + "?" + urllib.parse.urlencode(
        {"s": kw, "type": 1, "limit": limit})
    try:
        data = http_get_json(url)
        return data.get("result", {}).get("songs", []) or []
    except Exception as e:
        print(f"  [搜索失败] {kw}: {e}")
        return []


def song_artist(song):
    return (song.get("artists") or [{}])[0].get("name", "")


def _show_progress(done, total):
    """下载进度条：仅终端输出（\r 覆盖），避免污染重定向日志。"""
    if total > 0:
        pct = min(done * 100 // total, 100)
        bar_len = 20
        filled = pct * bar_len // 100
        bar = "█" * filled + "─" * (bar_len - filled)
        sys.stderr.write(f"\r  下载 {bar} {pct:3d}%  ({done // 1024}KB/{total // 1024}KB)")
    else:
        sys.stderr.write(f"\r  下载 {done // 1024}KB…")
    sys.stderr.flush()


def try_download(song_id, min_bytes=2 * 1024 * 1024, progress=False):
    """尝试下载歌曲，返回 (音频字节, 是否完整) 或 (None, False)。"""
    url = f"{URL_OUTER}?id={song_id}.mp3"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=40) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            data = bytearray()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                data += chunk
                if progress and sys.stderr.isatty():
                    _show_progress(len(data), total)
            if progress and sys.stderr.isatty():
                sys.stderr.write("\r" + " " * 60 + "\r")
                sys.stderr.flush()
    except Exception:
        return None, False
    if not is_mp3(bytes(data)):
        return None, False
    # VIP 错误页也是 HTML，头部校验已拦截；再按大小兜底试听片段
    if len(data) < min_bytes:
        return None, False
    return bytes(data), True


def is_valid_mp3_file(path, min_bytes=2 * 1024 * 1024):
    """轻量判定已存在文件是否为有效完整 mp3（只读文件头，不读全文）。"""
    try:
        if not path.is_file() or os.path.getsize(path) < min_bytes:
            return False
        with open(path, "rb") as f:
            head = f.read(3)
        return head[:3] == b"ID3" or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    except Exception:
        return False


def fetch_lyric(song_id):
    """返回 LRC 文本；无歌词（纯音乐等）返回 None。"""
    url = (f"{API_LYRIC}?id={song_id}&lv=1&kv=1&tv=-1")
    try:
        data = http_get_json(url)
        lrc = (data.get("lrc") or {}).get("lyric") or ""
        return lrc if lrc.strip() else None
    except Exception:
        return None


def fetch_cover_netease(song_id, album_id, size=500):
    """主源：网易云专辑 API。搜索 API 的 album 字段无 picUrl，须经 album.id 调专辑 API。"""
    try:
        if album_id:
            data = http_get_json(f"{API_ALBUM}/{album_id}")
            pic = (data.get("album") or {}).get("picUrl") or ""
            if pic:
                pic += f"?param={size}y{size}"
                return http_get(pic, timeout=20)
    except Exception:
        pass
    return None


def fetch_cover_deezer(artist, title, size="cover_big"):
    """备源：Deezer 公开搜索 API（免 key），按「歌手 歌名」搜专辑封面。"""
    try:
        q = urllib.parse.quote(f'{artist} "{title}"')
        data = http_get_json(
            f"https://api.deezer.com/search?q={q}&limit=3", timeout=10)
        for r in data.get("data", []) or []:
            url = (r.get("album") or {}).get(size) or ""
            if url:
                return http_get(url, timeout=15)
    except Exception:
        pass
    return None


def fetch_cover(song_id, album_id, artist, title):
    """封面多源链：网易云专辑 → Deezer → None。"""
    cover = fetch_cover_netease(song_id, album_id)
    if cover:
        return cover, "jpeg"
    cover = fetch_cover_deezer(artist, title)
    if cover:
        return cover, "jpeg"
    return None, None


def make_placeholder_png(size=500, seed="cover"):
    """兜底：纯标准库生成渐变色 PNG（无文字渲染依赖）。"""
    import struct
    import zlib

    def _hsl(h, s, l):
        c = (1 - abs(2 * l - 1)) * s
        x = c * (1 - abs((h / 60) % 2 - 1))
        m = l - c / 2
        if h < 60:
            r, g, b = c, x, 0
        elif h < 120:
            r, g, b = x, c, 0
        elif h < 180:
            r, g, b = 0, c, x
        elif h < 240:
            r, g, b = 0, x, c
        elif h < 300:
            r, g, b = x, 0, c
        else:
            r, g, b = c, 0, x
        return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)

    h1 = sum(seed.encode("utf-8", "replace")) * 37 % 360
    h2 = (h1 + 80) % 360
    rows = bytearray()
    for y in range(size):
        t = y / max(size - 1, 1)
        r1, g1, b1 = _hsl(h1, 0.55, 0.40)
        r2, g2, b2 = _hsl(h2, 0.55, 0.40)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        rows += b"\x00" + bytes((r, g, b)) * size

    def chunk(tag, data):
        # PNG 块 = length(4) + type(4) + data + crc(4)
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + chunk(b"IEND", b""))


def write_id3(mp3_path, title, artist, album, cover_bytes, cover_mime="jpeg"):
    """用 mutagen 写入 ID3 标签。"""
    try:
        from mutagen.id3 import APIC, TALB, TIT2, TPE1, ID3
    except ImportError:
        print("  [警告] 未安装 mutagen，跳过 ID3 写入。"
              "安装：pip install mutagen")
        return False
    try:
        tags = ID3(str(mp3_path))
    except Exception:
        tags = ID3()
    tags.delall("TIT2")
    tags.delall("TPE1")
    tags.delall("TALB")
    tags.delall("APIC")
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    if album:
        tags.add(TALB(encoding=3, text=album))
    if cover_bytes:
        mime = "image/png" if cover_mime == "png" else "image/jpeg"
        tags.add(APIC(encoding=3, mime=mime, type=3,
                      desc="Cover", data=cover_bytes))
    tags.save(str(mp3_path))
    return True


def _enrich_existing(mp3_path, song, out_dir, with_lyric, with_cover):
    """已存在文件的补全：缺歌词补歌词、缺封面/标签补写 ID3。返回补全说明列表。"""
    name = song.get("name", "")
    artist = song_artist(song)
    album = (song.get("album") or {}).get("name", "")
    fname = mp3_path.stem
    notes = []

    lrc_path = Path(out_dir) / f"{fname}.lrc"
    if with_lyric and not lrc_path.is_file():
        lrc = fetch_lyric(song["id"])
        if lrc:
            lrc_path.write_text(lrc, encoding="utf-8")
            notes.append("补歌词")

    if with_cover:
        has_apic = False
        try:
            from mutagen.id3 import ID3
            has_apic = bool(ID3(str(mp3_path)).getall("APIC"))
        except Exception:
            pass
        if not has_apic:
            cover, mime = fetch_cover(
                song["id"], (song.get("album") or {}).get("id"), artist, name)
            if not cover:
                cover, mime = fetch_cover_deezer(artist, name)
            if not cover:
                cover = make_placeholder_png(seed=name or "cover")
                mime = "png"
            write_id3(mp3_path, name, artist, album, cover, mime)
            notes.append("补封面" if mime != "png" else "补占位封面")
    return notes


def download_one(kw, out_dir, min_bytes, with_lyric, with_cover, limit, verbose,
                 progress=False):
    """下载单个关键词对应的歌曲。返回 (ok, msg)。"""
    songs = search_songs(kw, limit)
    if not songs:
        return False, f"无搜索结果: {kw}"

    # 免费（fee=0）优先，其次热度；跳过歌手名带尾巴的伪歌候选
    candidates = [s for s in songs if s.get("fee", 1) == 0]
    if not candidates:
        candidates = songs
    candidates.sort(key=lambda s: -(s.get("score") or 0))

    # 优先找「音频+封面」都齐的候选；封面失败不锁定音频，继续换下一个版本
    best = None  # (song, data) 音频成功但封面未齐的第一个候选，作兜底

    def _finalize(song, data, cover, cover_mime):
        """写文件 + 歌词 + ID3，返回汇报消息。"""
        sid = song["id"]
        name = song.get("name", "")
        artist = song_artist(song)
        dur = song.get("duration", 0) / 1000
        album = (song.get("album") or {}).get("name", "")

        fname = sanitize(f"{artist or '未知艺术家'} - {name or '未知歌曲'}")
        mp3_path = Path(out_dir) / f"{fname}.mp3"
        # 中文路径安全：先落临时英文名再改名（Windows + 部分 curl 环境）
        tmp_path = mp3_path.with_suffix(".tmp.mp3")
        tmp_path.write_bytes(data)
        tmp_path.replace(mp3_path)

        notes = []
        if with_lyric:
            lrc = fetch_lyric(sid)
            if lrc:
                (Path(out_dir) / f"{fname}.lrc").write_text(lrc, encoding="utf-8")
                notes.append("歌词")
        if with_cover:
            write_id3(mp3_path, name, artist, album, cover, cover_mime)
            if cover_mime == "png":
                notes.append("占位封面")
            elif cover:
                notes.append("封面")
            else:
                notes.append("标签")
        else:
            write_id3(mp3_path, name, artist, album, None)
            notes.append("标签")

        size_mb = os.path.getsize(mp3_path) / 1048576
        return (f"{name} - {artist}  {size_mb:.1f}MB  "
                f"({dur:.0f}s) [{'/'.join(notes) or '无'}]")

    for song in candidates:
        sid = song["id"]
        name = song.get("name", "")
        artist = song_artist(song)
        dur = song.get("duration", 0) / 1000

        if verbose:
            print(f"  试 {name} - {artist} ({dur:.0f}s, fee={song.get('fee')})")

        # 幂等：目标文件已存在且是有效完整 mp3 → 跳过下载，只补缺失的歌词/封面
        fname = sanitize(f"{artist or '未知艺术家'} - {name or '未知歌曲'}")
        mp3_path = Path(out_dir) / f"{fname}.mp3"
        if is_valid_mp3_file(mp3_path, min_bytes):
            if verbose:
                print(f"  = 已存在，补全缺失项…")
            notes = _enrich_existing(mp3_path, song, out_dir,
                                     with_lyric, with_cover)
            return True, (f"{name} - {artist}  已存在"
                          + (f"（{'、'.join(notes)}）" if notes else "，无缺失"))

        data, ok = try_download(sid, min_bytes, progress=progress)
        if not ok:
            continue

        if best is None:
            best = (song, data)

        if not with_cover:
            return True, _finalize(song, data, None, None)

        cover, mime = fetch_cover(sid, (song.get("album") or {}).get("id"),
                                  artist, name)
        if cover:
            return True, _finalize(song, data, cover, mime)
        # 封面失败 → 换下一个候选版本（可能专辑带图）
        if verbose:
            print(f"  - {name} 无封面，继续换候选…")

    # 所有候选都没有封面：用第一首音频成功的，走 Deezer 备源 + 占位图兜底
    if best:
        song, data = best
        cover = mime = None
        if with_cover:
            cover, mime = fetch_cover_deezer(song_artist(song),
                                             song.get("name", ""))
            if not cover:
                cover = make_placeholder_png(seed=song.get("name", "cover"))
                mime = "png"
        return True, _finalize(song, data, cover, mime)
    return False, f"没有可外链的免费完整版: {kw}（VIP 原版或试听片段）"


def parse_playlist(path):
    """解析歌单文件：每行一首，# 开头为注释、空行跳过。返回关键词列表。"""
    pl_path = Path(path)
    if not pl_path.is_file():
        raise FileNotFoundError(f"歌单文件不存在: {path}")
    lines = []
    for ln in pl_path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            lines.append(ln)
    return lines


def main():
    ap = argparse.ArgumentParser(
        description="网易云音乐批量下载（免费可外链版，含歌词/封面/ID3）")
    ap.add_argument("keywords", nargs="*", help="歌曲关键词（与 --playlist 二选一或并用）")
    ap.add_argument("--dir", default=str(Path.home() / "Music"),
                    help="输出目录（默认 ~/Music）")
    ap.add_argument("--count", type=int, default=30,
                    help="每首关键词的搜索候选数（默认 30）")
    ap.add_argument("--min-mb", type=float, default=2.0,
                    help="完整音频最小体积 MB（默认 2.0，过滤试听片段）")
    ap.add_argument("--no-lyric", action="store_true", help="不下载歌词")
    ap.add_argument("--no-cover", action="store_true", help="不写封面/标签")
    ap.add_argument("--verbose", action="store_true", help="显示候选尝试过程")
    ap.add_argument("--install-deps", action="store_true",
                    help="先自动安装依赖（pip install mutagen）再下载")
    ap.add_argument("--pip-index", metavar="URL",
                    help="pip 镜像源（--install-deps 时使用，"
                         "国内可填 https://pypi.tuna.tsinghua.edu.cn/simple）")
    ap.add_argument("--playlist", metavar="FILE",
                    help="歌单文件路径：每行一首歌，# 开头为注释/空行跳过，"
                         "与位置参数合并处理")
    args = ap.parse_args()

    if args.install_deps:
        import subprocess
        print("[依赖] 正在安装 mutagen…")
        cmd = [sys.executable, "-m", "pip", "install", "-q", "mutagen"]
        if args.pip_index:
            cmd += ["-i", args.pip_index]
        subprocess.check_call(cmd)
        try:
            import mutagen  # noqa: F401
            print("[依赖] mutagen 安装成功")
        except ImportError:
            print("[依赖] 安装失败，请手动执行: pip install mutagen")
            sys.exit(1)

    keywords = list(args.keywords)
    if args.playlist:
        try:
            lines = parse_playlist(args.playlist)
        except FileNotFoundError as e:
            print(f"✗ {e}")
            sys.exit(1)
        print(f"[歌单] 从 {Path(args.playlist).name} 读取 {len(lines)} 首")
        keywords += lines
    if not keywords:
        print("✗ 没有要下载的歌（位置参数与 --playlist 均为空）")
        sys.exit(1)

    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    min_bytes = int(args.min_mb * 1048576)

    print(f"输出目录: {out_dir}")
    print("=" * 60)

    ok_list, fail_list = [], []
    for kw in keywords:
        print(f"▶ {kw}")
        ok, msg = download_one(
            kw, out_dir, min_bytes,
            with_lyric=not args.no_lyric,
            with_cover=not args.no_cover,
            limit=args.count,
            verbose=args.verbose,
            progress=True,
        )
        if ok:
            print(f"  ✓ {msg}")
            ok_list.append(msg)
        else:
            print(f"  ✗ {msg}")
            fail_list.append(msg)
        time.sleep(0.5)  # 礼貌限速

    print("=" * 60)
    print(f"完成：成功 {len(ok_list)} 首，失败 {len(fail_list)} 项")
    for f in fail_list:
        print(f"  ✗ {f}")
    sys.exit(1 if fail_list else 0)


if __name__ == "__main__":
    main()
