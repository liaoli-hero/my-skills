---
name: netease-music-downloader
slug: netease-music-downloader
version: 1.0.3
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

## 快速开始（推荐：直接跑脚本）

已附可执行脚本 `scripts/download.py`，一条命令完成 搜索 → 反伪歌过滤 → 下载 → 歌词 → 封面 → ID3 写入 全闭环：

```bash
# 依赖（仅需一个）：pip install mutagen
python scripts/download.py "成都 赵雷" "Canon in D"          # 下载指定歌曲
python scripts/download.py "孤勇者" --dir "D:/Music/点歌器曲库" # 指定输出目录
python scripts/download.py "卡农" --no-lyric --no-cover      # 只要音频
python scripts/download.py "来几首纯音乐" --count 50          # 放宽候选数
python scripts/download.py --install-deps --pip-index https://pypi.tuna.tsinghua.edu.cn/simple  # 一键装依赖
python scripts/download.py --playlist songs.txt --dir "D:/Music/曲库"  # 歌单批量下载
python -m unittest discover -s tests -v                     # 运行离线自测
```

歌单文件格式：每行一首歌，`#` 开头为注释、空行自动跳过：
```
# 我的歌单
晴天 周杰伦
光年之外 邓紫棋
```

脚本行为：
- 自动跳过 VIP/版权受限歌曲（返回 HTML 错误页）和试听片段（<2MB）
- 自动下载同名 `.lrc` 歌词（纯音乐无歌词属正常，自动跳过）
- 自动写入 ID3 标签（标题/歌手/专辑 + 内嵌封面 APIC）
- 输出目录默认 `~/Music`，文件名统一「歌手 - 歌名.mp3」，可直接被本地播放器识别
- 多首关键词按顺序处理，每首成功后打印一行报告，失败项清晰列出

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
3. **封面链路（多源换源）**：搜索 API 返回的 album 字段**没有 picUrl**，主源必须用 album.id 调专辑 API 拿。封面拿不到时自动换源，优先级：① 换候选版本（同一首歌多个免费版本，优先挑专辑带图的）② Deezer 公开搜索 API（按「歌手 歌名」搜封面，免 key）③ 占位图兜底（纯标准库生成渐变色 PNG，保证播放器不空白）。图片 URL 加 `?param=500y500` 缩放（原图可能几 MB）。
4. **文件名规范**：`歌手 - 歌名.mp3`（播放器普遍按 ` - ` 分割解析，无 ID3 时兜底显示）。
5. **ID3 写入**：外链 mp3 无任何标签 → 用 mutagen 补 `TIT2/TPE1/TALB/APIC`（encoding=3 UTF-8，APIC type=3）。否则播放器无封面、显示文件名。
6. **歌词编码**：LRC 写 UTF-8（播放器按 utf8 读）。纯音乐（钢琴曲等）无歌词属正常，不要死磕。
7. **路径坑**：curl 写中文路径可能 exit 23，下载先落英文临时路径再 move；脚本本身放英文路径。
8. **实测数据**（2026-08-12，10 首全通）：搜索/下载/歌词/封面/ID3 五步在单脚本内闭环，平均每首约 3-5 秒；封面用专辑 API 链路 100% 拿到；唯一拿不到的是 VIP 原版（如周杰伦《晴天》），自动换歌即可。

## 环境
- Python 3.8+（Windows 下注意：脚本文件本身放英文路径，避免编码问题）
- 依赖：仅 `mutagen`（ID3 写入用；未安装时脚本会提示并跳过标签，不影响音频下载）
- 一键安装：`python scripts/download.py --install-deps`（可加 `--pip-index` 指定国内镜像）
- 离线自测：`python -m unittest discover -s tests -v`（纯标准库，14 个用例覆盖 MP3 头校验/VIP 识别/文件名安全/占位图结构/ID3 mime）

## 推荐执行方式
1. **优先直接运行 `scripts/download.py`**——闭环已实现：搜索 → 过滤（精确匹配+时长）→ 试外链（验证 mp3 头）→ 下载 → 歌词 → 封面 → ID3 写入，一次跑完给报告
2. 需要定制时（如换备用歌单、改过滤规则），改脚本参数或在其基础上扩展
3. 失败项自动换候选歌（脚本按 fee=0 → 热度排序逐个尝试）
4. 修改脚本后跑一遍 `tests/` 自测，防止回归（v1.0.3 曾靠自测抓出占位 PNG 块序 bug）

## FAQ

**Q: 为什么有些歌下载失败？**
A: 绝大多数是 VIP/版权受限——网易云外链对这些歌统一返回 HTML 错误页（特征：106884 字节、非 MP3 头），脚本已自动识别跳过。另外 2026 年起搜索混入大量 AI 伪歌/翻唱，歌名歌手对不上或时长异常属正常现象，换关键词或换歌即可。

**Q: 下载的歌曲有没有版权风险？**
A: 本工具仅利用网易云官方提供的试听外链机制，且只下载标记为免费（fee=0）的歌曲，请仅用于个人播放器测试/学习用途。商用分发请使用公版或 CC 授权音乐源。

**Q: 为什么有的歌没有歌词？**
A: 两种正常情况：纯音乐（钢琴曲、电音等）本就没有歌词；部分翻唱/小众歌曲歌词库未收录。脚本对这两种情况自动跳过，不报错。

**Q: 为什么有的歌没有封面？**
A: 现在基本不会了——脚本内置三层换源：① 网易云专辑 API 拿不到时自动换候选版本（同歌多版本优先挑专辑带图的）② 仍拿不到走 Deezer 公开 API（免 key）③ 全部失败生成渐变色占位图兜底，保证播放器界面不空白。只有 `--no-cover` 时才会完全没有封面。

**Q: 脚本能下 VIP 歌吗？**
A: 不能。网易云外链只放行免费版本，VIP 原版（如周杰伦《晴天》）任何接口都拿不到完整音频。这是平台限制，非本工具问题。

**Q: 网易云接口会不会失效？**
A: 有可能。外链接口（outer/url）历史上被调整过多次，若脚本报大面积失败，说明接口已变更。届时检查脚本头部常量 URL 是否仍有效。
