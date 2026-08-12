---
name: netease-music-downloader
slug: netease-music-downloader
version: 1.0.0
displayName: 网易云音乐批量下载
summary: 从网易云音乐批量下载免费可外链歌曲（含同名 LRC 歌词 + 内嵌封面 ID3 标签），供本地播放器/点歌器测试曲库使用。
description: 从网易云音乐批量下载免费可外链歌曲（含同名 LRC 歌词 + 内嵌封面 ID3 标签），供本地播放器/点歌器测试曲库使用。触发词：下载歌曲、下载音乐、搞几首歌、点歌器测试曲库、音乐下载、点歌、听歌。适用国内网络环境（archive.org/GitHub 直连均不可用时）。
tags:
  - music
  - download
  - netease
  - 网易云
  - 点歌
license: MIT
---

# 网易云音乐批量下载（含歌词 + 封面）

## 适用场景
- 给本地播放器（如 Electron 点歌器）准备测试曲库
- 下载免费可外链歌曲 + 歌词 + 封面
- 国内网络环境，archive.org / GitHub 直连不可用时的兜底方案

## 标准点歌指令（用户复制即用）

用户把《歌名》换成想听的歌，把下面整段发给 AI 即可自动完成下载：

> 点歌：请把《歌名》下载到我的点歌器曲库。
> 要求：文件名「歌手 - 歌名.mp3」，配同名 .lrc 歌词，写入 ID3 标签和内嵌封面，
> 完成后告诉我曲库里新增了哪些歌。

- 支持一次多首：《歌名1》《歌名2》或"来 5 首 XX 风格的歌"
- 曲库目录默认 C:\Users\42237\Music，用户指定其他目录时以用户为准
- VIP 原版下不到时，自动换免费版本或同风格替代，并在回报中说明

## 核心 API（全部需要 UA + Referer: https://music.163.com/ 请求头）

| 用途 | 接口 |
|---|---|
| 搜索 | `GET https://music.163.com/api/search/get?s=<关键词>&type=1&limit=30` |
| 下载 | `GET https://music.163.com/song/media/outer/url?id=<songId>.mp3`（302 跟随） |
| 歌词 | `GET https://music.163.com/api/song/lyric?id=<songId>&lv=1&kv=1&tv=-1` → `lrc.lyric` |
| 封面 | 搜索结果 `album.id` → `GET https://music.163.com/api/album/<albumId>` → `album.picUrl` |

## 关键规则（踩坑沉淀）

1. **VIP 识别**：外链对 VIP/版权受限歌曲返回 **106884 字节的 HTML 错误页**（不是 mp3）。判断方法：文件头不是 `ID3` 且不是 `0xFFEx` 帧同步 → 丢弃。可外链的完整歌一般 >2MB。
2. **反伪歌**：2026 年起网易云搜索混入大量 AI 伪歌/翻唱（歌名带"伤感版"、歌手名带尾巴如"周杰伦-"、时长异常）。匹配策略：**歌名+歌手精确匹配 + 时长接近原版**（如晴天 269s）。VIP 原版拿不到时直接换歌，不要硬试。
3. **封面链路**：搜索 API 返回的 album 字段**没有 picUrl**，必须用 album.id 调专辑 API 拿。图片 URL 加 `?param=500y500` 缩放（原图可能几 MB）。
4. **文件名规范**：`歌手 - 歌名.mp3`（播放器普遍按 ` - ` 分割解析，无 ID3 时兜底显示）。
5. **ID3 写入**：外链 mp3 无任何标签 → 用 mutagen 补 `TIT2/TPE1/TALB/APIC`（encoding=3 UTF-8，APIC type=3）。否则播放器无封面、显示文件名。
6. **歌词编码**：LRC 写 UTF-8（播放器按 utf8 读）。纯音乐（钢琴曲等）无歌词属正常，不要死磕。
7. **路径坑**：curl 写中文路径可能 exit 23，下载先落英文临时路径再 move；脚本本身放英文路径。
8. **实测数据**（2026-08-12，10 首全通）：搜索/下载/歌词/封面/ID3 五步在单脚本内闭环，平均每首约 3-5 秒；封面用专辑 API 链路 100% 拿到；唯一拿不到的是 VIP 原版（如周杰伦《晴天》），自动换歌即可。

## 环境
- Python: managed `C:\Users\42237\.workbuddy\binaries\python\versions\3.13.12\python.exe`
- venv: `C:\Users\42237\.workbuddy\binaries\python\envs\default`（已装 mutagen）
- pip 走清华镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`

## 推荐执行方式（一次闭环，不要分步）
1. 一个 Python 脚本内完成：搜索 → 过滤（精确匹配+时长）→ 试外链（验证 mp3 头）→ 下载 → 歌词 → 封面 → ID3 写入
2. 脚本内置验证输出（每首歌：大小/时长/封面/歌词状态），一次跑完直接给报告
3. 失败项自动换候选歌（备选歌单提前列好）
