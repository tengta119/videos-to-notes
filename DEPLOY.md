# 发布到 GitHub Pages 教程（个人仓库版）

本教程说明如何把 VideoBook 流水线产出的电子书发布到**你自己的** GitHub Pages 站点。
以本仓库实际环境为例：目标仓库为 `https://github.com/tengta119/videos-to-notes`，
Pages 站点地址为 `https://tengta119.github.io/videos-to-notes/`。

> 前提：`origin` 指向上游工具仓库（Luke-Evan/videobook），我们不动它；
> 自己的仓库另起远程名 `mine`，两者互不影响。

## 第 1 步：本地生成分支（纯本地，不联网）

```bash
python src/publish.py <video_id>        # 单个视频
python src/publish.py --all             # output/ 下所有含 book.html 的视频
```

- `<video_id>` 是 `output/` 下的目录名，如 `BV1cSbi62Eu5_p1`。
- 成功输出 `>> pages 分支已更新: xxxxxxxx`。
- 脚本只写入本地 `pages` 分支 ref（首次运行自动创建 orphan 分支，从零开始）：
  - 每本书一个目录，目录名 = 视频标题，内含 `book.html / book.md / images/*.png`；
  - 若存在 `transcript.corrected.txt` 会一并发布，书架卡片自动附"字幕对照"链接；
  - 同时生成书架落地页 `index.html` 与 `manifest.json`；
  - transcript.json、tagged 稿等中间物不进公开仓库；不触碰 main 工作区与 `output/`。
- 内容无变化时自动跳过提交（幂等，可放心重跑）。

## 第 2 步：添加自己的仓库为远程并推送

```bash
git remote add mine https://github.com/tengta119/videos-to-notes.git

# 推 pages 分支（成品 = Pages 站点内容）
git push mine pages

# 可选：把工具代码也备份到自己仓库
git push mine main
```

- 首次推送会弹出 **Git Credential Manager** 的 GitHub 登录窗口，按提示授权即可——
  这是全流程中唯一需要"填"的东西。
- 若报网络错误（如 `schannel: server closed abruptly`），先给 git 挂代理再推：

  ```bash
  git config --global http.proxy  http://127.0.0.1:7890
  git config --global https.proxy http://127.0.0.1:7890
  ```

  端口按你的代理客户端实际修改；不想全局设置可把 `--global` 换成 `--local`（仅当前仓库生效）。

## 第 3 步：在 GitHub 网页上启用 Pages（一次性）

打开 `https://github.com/tengta119/videos-to-notes/settings/pages`：

1. **Source** 选 **Deploy from a branch**；
2. **Branch** 选 `pages`，目录选 **`/ (root)`**；
3. 点 **Save**。

## 第 4 步：验证

等约 1 分钟部署完成，访问：

- 书架落地页：**https://tengta119.github.io/videos-to-notes/**
- 本书直达：在落地页点卡片即可（目录名含中文，URL 自动百分号编码，无需手拼）。

## 日常更新

每做完一本新书，重复两步即可：

```bash
python src/publish.py <video_id>
git push mine pages
```

GitHub 会自动重新部署。

## 可选：把落地页缎带指向自己的仓库

`src/publish.py` 生成的书架页右上角 GitHub Corner 缎带硬编码指向上游仓库
（`https://github.com/Luke-Evan/videobook`，见 `build_index_html` 中的链接）。
希望点它跳转到你自己的仓库，在执行第 1 步前把该 URL 改成
`https://github.com/tengta119/videos-to-notes` 即可。



# 一条龙的"新机器初始化"清单
```bash
git clone <你的仓库> && cd videobook
pip install -r requirements.txt
python src/capture_frames.py --setup-profile   # 扫码登录一次
```

之后就能正常用：把视频链接发给 AI 助手，按 instructions.md 流水线走。
