# friction-overseas

摩擦材料 / 制动器行业 —— **海外信源采集**（GitHub Actions 版）。

## 这是干什么的

给摩擦材料情报站（`http://146.56.250.224/friction/`）补充海外信源。

主站跑在腾讯云 VPS 上，出口 IP 被 **Cloudflare / Akamai** 拦截，
导致这批美国行业媒体抓不到：

- Counterman、Fleet Owner、CCJ Digital、Underhood Service、Aftermarket News
- Tire Business、Automotive News Europe
- TMD Friction、UNECE WP.29、Espacenet

本仓库把这些采集挪到 **GitHub Actions（Azure 机房）**，用不同的出口 IP 去试。

## 工作原理

```
GitHub Actions (Azure IP)
   ├── ① 探针     → 先验证 IP 能不能过 WAF（不做采集）
   ├── ② 直采     → 抓列表页 + Google 免费翻译
   └── ③ 聚合兜底 → 直连失败的站，改走 Google/Bing News RSS
            ↓
     friction/news_overseas.json  (自动提交)
            ↓
     VPS 每 3 小时拉取 → 合并进 friction.db
```

## 目录

```
.github/workflows/friction-overseas.yml   # 工作流（每天 08:30 / 20:30 北京时间）
scripts/probe_overseas.py                 # ① 可达性探针
scripts/collect_overseas.py               # ② 直采 + 翻译
scripts/collect_via_aggregator.py         # ③ 聚合兜底
friction/news_overseas.json               # 产出（自动生成）
```

## 第一次用：先跑探针

**不要直接跑完整流程**，先确认 IP 行不行：

1. 打开仓库的 **Actions** 标签页
2. 左侧选 **friction-overseas**
3. 右侧点 **Run workflow**
4. **勾选 `probe_only`** → 点绿色按钮
5. 等约 1 分钟，点进这次运行，看下面的结论

### 探针怎么看

它会输出一行判定：

| 输出 | 含义 | 下一步 |
|---|---|---|
| `WAF 组突破 ≥3` | ✅ Azure IP 有效 | 不勾 probe_only 跑完整流程 |
| `突破 1~2` | 🔶 部分有效 | 在 `collect_overseas.py` 里只留能通的站 |
| `突破 0` | ⛔ Azure 也被拦 | 只能靠聚合兜底，属正常结果 |

同时会打印**出口 IP**（对比腾讯云的，看是不是换了段）。

探针结论会落盘到 `data/probe_result.json`，
可以直接在仓库里查看，不必下载 artifact。

## 实测结论（2026-10-01）

**出口 IP：`134.33.76.4`（Azure）**

| 站点 | 腾讯云 VPS | GitHub Actions |
|---|---|---|
| Counterman | ⛔ CF 挑战 | ✅ 200 |
| Fleet Owner | ⛔ CF 挑战 | ✅ 200 |
| Underhood Service | ⛔ CF 挑战 | ✅ 200 |
| Aftermarket News | ⛔ CF 挑战 | ✅ 200 |
| Bosch 新闻室 | ✅ | ✅ 200 |
| SAE / US EPA | ✅ | ✅ 200 |
| Tire Business | ⛔ | ⛔ 403（Akamai） |
| CCJ Digital | ⛔ | ⛔ 403 |
| TMD Friction | ⛔ | ⛔ 403 |
| UNECE / Automotive News EU | ⛔ | ⛔ 403 |

**结论**：Azure IP 对 Cloudflare 系有效（4/10 突破），对 Akamai 无效。

### 两个坑（已在代码里处理）

1. **探针 200 ≠ 能采集**
   探针拿的是**首页**；文章列表页/详情页 WAF 更严。
   例：Counterman 首页 200，但文章页命中率低。

2. **Google News RSS 会限流**
   高频 `site:` 查询会返回 **503**（临时限流，非永久封禁）。
   代码里已加**指数退避重试 + 任务间 2s 节流**，不加会随机丢数据。

### SPA 站说明

SAE、US EPA 的列表页是 **JS 动态渲染**，`requests` 拿不到文章链接。
这类站走聚合通道兜（`AGG_SPA_SITES`），只拿到标题级线索。

## 数据分级

采集结果分两级，`level` 字段标识：

| level | 含义 | 来源 |
|---|---|---|
| （无 / `full`） | 有正文 | 直采 |
| `headline` | 仅标题+链接 | 聚合兜底 |

下游 VPS 侧可按级别做不同展示（标题级只做线索，不做正文渲染）。

## 信源普查（找可用源的工具）

沙箱和国内 VPS 的出口 IP 被大面积 WAF 封禁，本地测不准。
想知道某个站**在 Azure IP 下到底通不通**，用信源普查：

```bash
# Actions → Run workflow → 勾上 source_scan
```

结论写入 `data/source_scan.json`。

**2026-10-01 普查结果：8/20 可直连**

| ✅ 可直连 | 用途 |
|---|---|
| Ratchet+Wrench | 售后技术（50 篇文章链接） |
| Auto Care Association | 协会（35） |
| Repairer Driven News | 售后（31） |
| BodyShop Business | 售后（21） |
| Miba AG | 摩擦材料（12） |
| Tomorrow's Technician | 售后技术（5） |
| Brake & Front End | 售后技术（3） |
| ImportCar | 售后技术（3） |

已加入 `collect_overseas.py` 直采。

## 代理阅读器不可用（已实测）

试过 `r.jina.ai` / `allorigins` 绕 WAF，**全军覆没**：

| 通道 | 结果 | 原因 |
|---|---|---|
| r.jina.ai | 0/5，全部 403 | 免费版出口 IP 已被目标站拉黑 |
| allorigins | 0/5，520/522 | 服务端不可达 |

连在 Actions 里直连正常的 Counterman 都 403 —— 说明不是网络问题，
是这些代理服务自己进了目标站的黑名单。**此路不通，勿再重试。**

留 `scripts/probe_proxy.py` 备查（将来若有付费代理通道可复用）。

## 已知边界

以下站点**无法突破**，只能靠聚合拿标题：

- **Akamai 系**：Tire Business、CCJ Digital、TMD Friction
- **Cloudflare 企业版**：The BRAKE Report、Tyrepress、MEMA、ISO、Automotive World
- **JS 渲染（SPA）**：SAE、US EPA（列表页动态加载，requests 拿不到）

这些是 IP 层封锁，非技术手段可解。


## 抓 0 条时的排查方法（这次用上的）

**别猜，让 Azure 自己把链接打出来。**

`fetch_source()` 在"入口可达但正则 0 命中"时，会把页面里的站内链接
**路径样本**写进错误信息：

```
Ratchet+Wrench :: 入口可达但正则 0 命中
  | https://www.ratchetandwrench.com/ -> HTTP200 links=163 hit=0
  |    路径样本: https://www.ratchetandwrench.com/home/article/55387461/2026-ratchetwre...
```

看到样本，一眼就知道 URL 是 `/home/article/<8位ID>/<slug>`，
正则改一行就通了 —— 比反复猜快得多。

**这个方法区分了三种"抓 0 条"**（以前混在一起看不清）：

| 症状 | 含义 | 处理 |
|---|---|---|
| `HTTP 403` | IP 被 WAF 拦 | 无解，转聚合 |
| `HTTP200 links=163 hit=0` | **规则失效**（站点改版） | 看路径样本 → 改正则 ✅ |
| `HTTP200 links=0` | JS 渲染（SPA） | 无解，转聚合 |

**本次修正记录**：

| 源 | 原因 | 处理 |
|---|---|---|
| Repairer Driven News | WordPress 日期式 URL `/2026/09/30/slug/`，旧规则要纯 slug | 改正则 → 抓 12 条 ✅ |
| Ratchet+Wrench | Informa CMS `/home/article/<id>/<slug>` | 改正则 → 已入库 ✅ |
| Miba AG | 首页链接全是图片/德语页，文章链接 JS 加载 | 保持，转聚合 |
| SAE / US EPA | `links=0` / `links=154` 但无文章链接 | SPA，转聚合 |


## 设置

**必须开启写权限**，否则无法自动提交结果：

```
仓库 Settings → Actions → General → Workflow permissions
选 "Read and write permissions" → Save
```

## 定时

已内置，无需配置：

```
cron: '30 0 * * *'    → 北京时间 08:30
cron: '30 12 * * *'   → 北京时间 20:30
```

## 本地测试

```bash
pip install requests beautifulsoup4
python scripts/probe_overseas.py      # 探针
python scripts/collect_overseas.py    # 直采
```

> 注意：在**被墙的环境**（如国内 VPS）本地测试，探针结果会和线上一样全红 ——
> 这恰恰说明探针工作正常。真正的验证必须在 Actions 里跑。

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 工作流列表里看不到 | `.github/workflows/` 不在仓库根目录 | 确认目录层级 |
| 提交失败 | 缺写权限 | 见上面「设置」 |
| 探针全红 | Azure IP 也被 WAF 识别 | 预期结果之一，走聚合兜底 |
| 采集 0 条 | 站点改版，`link_re` 失效 | 改 `collect_overseas.py` 里对应源的规则 |

---

## 📘 接手指南（2026-10-06）

**新会话 / 电脑版 WorkBuddy 先看这个**：[`docs/接手指南_WorkBuddy.md`](docs/接手指南_WorkBuddy.md)

包含：架构总图、凭据清单、日常运维速查、正文翻译链路（2026-10-05 从 LibreTranslate
切到 GitHub Actions 多源 Google）、**工作方法与思路**、已踩过的坑、当前状态与待办、
电脑版接手的具体步骤。

⚠️ 三条改翻译代码前必读的硬规则：`sl` 绝不设 `auto`；`[IMG:n]` 占位符不能丢；
429 不许死等（退避重试会把 4 分钟的活拖到 25 分钟）。

### 🔁 工作约定（2026-10-06，云端沙箱与电脑版 WorkBuddy 都要遵守）

**每次对话的任何改动，收尾时同步三处**：
`接手指南_WorkBuddy.md` + `VPS_凭据与运维.md` + 本仓库（`docs/` 与本 README）。
改完不写回来 = 没改完。

### 📌 最近一次改动（2026-10-06 晚）

| 项 | 状态 | 落在哪 |
|---|---|---|
| 新增「报告」标签（市场研报/财报/ESG） | ✅ | VPS `app/services/scoring.py` + 新脚本 `tag_backfill.py` |
| 全库补标签 | ✅ | 287 篇补了 517 个标签，含「报告」39 篇 |
| 配图廊默认折叠 | ✅ | `app/templates/article.html` + `components.css` |
| 清理翻译报错污染译文 | ✅ | id=1996 `title_zh`、id=311 `content_zh` |

两个关键设计（别在后续改动里改回去）：
1. **「报告」只在标题命中才打**（`TITLE_ONLY_TAGS`）——按正文匹配会把
   4~179 万字符的联邦法规全文整片误判成研报（实测 58 命中里 29 篇是误报）。
2. **「报告」保底进 top6 标签**——长正文里热衰退/制动粉尘是 10 分词，
   不保底会被挤出 6 个标签上限，标签就筛不出来。
