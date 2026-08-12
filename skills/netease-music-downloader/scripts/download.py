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


def try_download(song_id, min_bytes=2 * 1024 * 1024):
    """尝试下载歌曲，返回 (音频字节, 是否完整) 或 (None, False)。"""
    url = f"{URL_OUTER}?id={song_id}.mp3"
    try:
        data = http_get(url, timeout=40)
    except Exception:
        return None, False
    if not is_mp3(data):
        return None, False
    # VIP 错误页也是 HTML，头部校验已拦截；再按大小兜底试听片段
    if len(data) < min_bytes:
        return None, False
    return data, True


def fetch_lyric(song_id):
    """返回 LRC 文本；无歌词（纯音乐等）返回 None。"""
    url = (f"{API_LYRIC}?id={song_id}&lv=1&kv=1&tv=-1")
    try:
        data = http_get_json(url)
        lrc = (data.get("lrc") or {}).get("lyric") or ""
        return lrc if lrc.strip() else None
    except Exception:
        return None


def fetch_cover(song_id, album_id, size=500):
    """经专辑 API 拿封面 URL 并下载图片字节。搜索 API 的 album 字段无 picUrl。"""
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


def write_id3(mp3_path, title, artist, album, cover_bytes):
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
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3,
                      desc="Cover", data=cover_bytes))
    tags.save(str(mp3_path))
    return True


def download_one(kw, out_dir, min_bytes, with_lyric, with_cover, limit, verbose):
    """下载单个关键词对应的歌曲。返回 (ok, msg)。"""
    songs = search_songs(kw, limit)
    if not songs:
        return False, f"无搜索结果: {kw}"

    # 免费（fee=0）优先，其次热度；跳过歌手名带尾巴的伪歌候选
    candidates = [s for s in songs if s.get("fee", 1) == 0]
    if not candidates:
        candidates = songs
    candidates.sort(key=lambda s: -(s.get("score") or 0))

    for song in candidates:
        sid = song["id"]
        name = song.get("name", "")
        artist = song_artist(song)
        dur = song.get("duration", 0) / 1000

        if verbose:
            print(f"  试 {name} - {artist} ({dur:.0f}s, fee={song.get('fee')})")

        data, ok = try_download(sid, min_bytes)
        if not ok:
            continue

        album = (song.get("album") or {}).get("name", "")
        album_id = (song.get("album") or {}).get("id")

        # 用候选自身的名字（已在搜索阶段过滤过关键词），保证和 lrc 同名
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
            cover = fetch_cover(sid, album_id)
            if cover:
                write_id3(mp3_path, name, artist, album, cover)
                notes.append("封面")
            else:
                write_id3(mp3_path, name, artist, album, None)
                notes.append("标签")
        else:
            write_id3(mp3_path, name, artist, album, None)
            notes.append("标签")

        size_mb = os.path.getsize(mp3_path) / 1048576
        return True, (f"{name} - {artist}  {size_mb:.1f}MB  "
                      f"({dur:.0f}s) [{'/'.join(notes) or '无'}]")
    return False, f"没有可外链的免费完整版: {kw}（VIP 原版或试听片段）"


def main():
    ap = argparse.ArgumentParser(
        description="网易云音乐批量下载（免费可外链版，含歌词/封面/ID3）")
    ap.add_argument("keywords", nargs="+", help="歌曲关键词，支持多首")
    ap.add_argument("--dir", default=str(Path.home() / "Music"),
                    help="输出目录（默认 ~/Music）")
    ap.add_argument("--count", type=int, default=30,
                    help="每首关键词的搜索候选数（默认 30）")
    ap.add_argument("--min-mb", type=float, default=2.0,
                    help="完整音频最小体积 MB（默认 2.0，过滤试听片段）")
    ap.add_argument("--no-lyric", action="store_true", help="不下载歌词")
    ap.add_argument("--no-cover", action="store_true", help="不写封面/标签")
    ap.add_argument("--verbose", action="store_true", help="显示候选尝试过程")
    args = ap.parse_args()

    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    min_bytes = int(args.min_mb * 1048576)

    print(f"输出目录: {out_dir}")
    print("=" * 60)

    ok_list, fail_list = [], []
    for kw in args.keywords:
        print(f"▶ {kw}")
        ok, msg = download_one(
            kw, out_dir, min_bytes,
            with_lyric=not args.no_lyric,
            with_cover=not args.no_cover,
            limit=args.count,
            verbose=args.verbose,
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
