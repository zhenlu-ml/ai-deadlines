# AI Conference Deadlines

AI 会议投稿截止倒计时，风格类似 aideadlines.org。打开 `index.html` 即可使用，不需要服务器。

```
open index.html                 # 直接用浏览器打开
uv run scripts/update.py        # 刷新数据（约 10 秒）
```

## 数据从哪来

`scripts/update.py` 每次运行会做下面几步：

1. **拉取两个社区维护的数据源**（各下载一个 tarball）
   - [ccfddl/ccf-deadlines](https://github.com/ccfddl/ccf-deadlines)：几乎每天更新
   - [huggingface/ai-deadlines](https://github.com/huggingface/ai-deadlines)：专注 AI，元数据更全
2. **筛选**：只保留 `catalog.yaml` 里列出的会议，并给它们打上分类
   (ML CV NLP RO SP DM AP KR HCI IRSM MISC)。
3. **去官网核对**：每个未过期的 deadline 都会去会议官网抓取页面。首页找不到日期时，会顺着
   "Important dates / Call for papers" 之类的链接再找一层。
   - `✓ on official site`：官网页面上找到了这个日期
   - `○ not confirmed`：官网能打开，但没找到这个日期（可能写在 PDF 或别的页面里）
   - `⚠ sources differ`：两个数据源给的日期不一样。以官网上能找到的那个为准；
     官网上都找不到时用 ccfddl 的（更新更勤）
4. **最后套用 `overrides.yaml`**：这里是手写的修正，优先级最高。

## 常见修改

**加一个会议**：在 `catalog.yaml` 里加一行。名字要和数据源里的写法对得上，对不上就用 `aliases`。
如果官网首页没有日期，可以填一个 `cfp:` 链接专门用来核对。

**手动修正或补充 deadline**（比如 ARR 的某个周期，或者数据源还没更新的会议）：

```yaml
# overrides.yaml
- key: iccv
  year: 2027
  link: https://iccv.thecvf.com/
  deadlines:
    - {type: abstract, date: "2027-03-01 23:59:59", timezone: AoE}
    - {type: paper,    date: "2027-03-08 23:59:59", timezone: AoE}
```

时区支持这些写法：`AoE`、`UTC-8`、`GMT+02`、`PST`、`PT`，以及 IANA 时区名（比如 `Europe/London`）。

## 页面功能

- 实时倒计时：剩 3 天以内显示红色，14 天以内显示黄色
- 时间自动换算成本地时区，同时保留官方写的原始时间
- 分类筛选兼容 aideadlines 的链接格式：`index.html?sub=ML,CV,NLP`
- 搜索（按 `/` 跳到搜索框），"Top-tier only" 只看 CCF-A 或 CORE A*
- "Not announced yet"：还没公布日期的下一届，按上一届的时间推算（能识别 ICCV/ECCV 这种两年一届的会议）
- 导出 `.ics`（自带提前 7 天和 1 天的提醒），也可以直接加到 Google Calendar

## 自动更新

`.github/workflows/update.yml` 会每天运行一次 updater，有变化就提交。
把仓库推到 GitHub 并开启 Pages（Deploy from branch → `main` / root），就能得到一个每天自动刷新的网站。
