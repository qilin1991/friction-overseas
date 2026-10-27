> ⚠️ 安全提示：本文件原版曾在公开仓库 `qilin1991/friction-overseas` 的 `main` 分支泄露真实
> VPS SSH 密码与站点 Basic Auth 明文，已被发现并轮换。下方已脱敏，明文凭据**严禁**写入任何仓库。
>
# Friction Hub · VPS 凭据与运维手册

> 更新：2026-10-01 14:18
> 目的：把账号固定下来，避免再混

## 一、SSH 登录（一律用这个）

| 项目 | 值 |
|---|---|
| 账号 | **ubuntu** |
| 密码 | `‹VPS_SSH_PASSWORD›（已轮换，见私有凭据库）` |
| 主机 | `146.56.250.224` |
| root 权限 | ubuntu **可免密 sudo**，直接 `sudo xxx` |

```bash
sshpass -p '‹VPS_SSH_PASSWORD›（已轮换，见私有凭据库）' ssh -o StrictHostKeyChecking=no ubuntu@146.56.250.224
```

**⚠️ 不要再用 root 登录** —— root 密码未知，且实测被拒。
需要 root 权限时：`sudo <命令>`。

## 二、已实测的账号状态

| 账号 | 能否登录 | 说明 |
|---|---|---|
| **ubuntu** | ✅ 可以，免密 sudo | **唯一入口** |
| root | ❌ 拒绝 | 虽然 sshd 允许 root 登录，但密码非已知值 |
| lighthouse | ❌ 拒绝 | 腾讯云默认账号，密码未知 |

## 三、站点访问

| 项目 | 值 |
|---|---|
| 地址 | http://146.56.250.224/friction/ |
| Basic Auth | `qilin` / `‹SITE_BASIC_AUTH›（已轮换）` |

## 四、GitHub Token（两个，别搞混）

| token | 用途 | 处置 |
|---|---|---|
| `ghp_MfoC8sxS...h5P` | **VPS 拉取专用**（写在 systemd 里） | ✅ **永久保留，绝不能删** |
| `ghp_Moj3Lpr9...lsj2x` | 一次性（建仓库用） | 🗑️ 待删除 |

Token 位置：`/etc/systemd/system/friction-overseas-fetch.service` 的
`Environment=GH_TOKEN=...`

## 五、常用运维

```bash
sudo systemctl status friction                     # 站点状态
sudo systemctl restart friction                    # 重启站点（数据更新后需要）
sudo systemctl start friction-overseas-fetch.service   # 手动同步一次
sudo journalctl -u friction-overseas-fetch.service -n 30 --no-pager  # 看同步日志
systemctl list-timers friction-overseas-fetch.timer --no-pager        # 看下次触发时间
```

## 六、路径

| 项目 | 路径 |
|---|---|
| 代码 | `/opt/friction/` |
| 拉取脚本 | `/opt/friction/fetch_friction_overseas.py` |
| 数据库 | `/opt/friction/data/friction.db` |
| venv | `/opt/friction/venv/` |
| service | `/etc/systemd/system/friction-overseas-fetch.service` |
| 站点 | `friction.service`，监听 `127.0.0.1:8084` |
| Nginx 分片 | `/etc/nginx/conf.d/friction_location.inc` |
| **数据库备份** | 脚本 `/opt/friction/backup_db.py`，本地 `/opt/friction/data/backups/`（留 7 份），COS `friction_backup/`（留 14 份） |
| **备份调度** | `friction-backup.timer`，每日 04:00，`systemctl list-timers friction-backup.timer` 看下次触发 |
| **COS 凭据** | `/opt/friction/.deploy.env`（chmod 600，属主 archive），由 `cos_cred.py` 加载 |
| **流水线** | `/opt/friction/fetch_and_translate.sh`（10 步，见下） |

## 六·补 流水线 10 步（2026-10-05 定稿）

| 步骤 | 脚本 | 备注 |
|---|---|---|
| 1/10 | `pull_media.py` | 合并 Actions 海外结果 |
| 2/10 | `resplit_interleave.py --apply` | 图文混排 |
| 3/10 | `fetch_detail.py --limit 600` | 详情正文补抓 |
| 4/10 | `clean_pseudo.py --apply` | 栏目页清理 |
| 5/10 | `dedup.py --apply` | 去重 |
| 6/10 | `backfill_media.py` | 图片视频发现 |
| 7/10 | `localize_media.py --retry-failed` | 媒体本地化 |
| 8/10 | `cos_helper.py --only-missing` | **图床增量上传** |
| 9/10 | `translate_local.py` | 翻译（LibreTranslate，**已被 9'/10' 取代**） |
| 10/10 | `zh_guard.py` | 中文源守卫 |

## 六·补② 正文翻译改用 GitHub Actions 多源（2026-10-05 上线）

**背景**：VPS 出口访问 Google 全线不通；本地 LibreTranslate（Argos/OPUS 通用语料）
在汽车领域必错（caliper→卡路里、washer→洗衣机、nut→坚果、bleed→出血、disc→盘子），
且 317 篇里有 41 篇段落结构塌陷（6 段压成 1 段）。Actions runner 在 Azure，Google 通。

```
VPS  qpush.py push <N> [new|lt|all]  →  data/translate_queue.json
        ↓  (workflow_dispatch friction-translate.yml)
Actions  scripts/translate_body.py   →  data/news_zh.json + data/translate_cache.json
        ↓
VPS  qpush.py merge                  →  回填 article.content_zh / title_zh
```

| 项 | 值 |
|---|---|
| 主力源 | `translate.googleapis.com` / `translate.google.com` / `.com.hk` / `.co.jp`（gtx，均强制 `sl=en`） |
| 兜底源 | MyMemory（不同引擎，可带 email 提额到 5 万字/日） |
| 实测不可用 | 微软 Edge(404)、DeepL 网页端(429)、Papago(404)、有道(非JSON)、百度(空译文)、Reverso(403)、Lingva(原样返回)、LibreTranslate 镜像(400/DNS) |
| 速度 | 45–50 篇/批 ≈ 86–120s（批量合并请求 + 跨轮缓存） |
| 成本 | ¥0 |
| 批量跑 | 沙箱 `python3 run_batches.py <每批篇数> <轮数>`（自动 push→dispatch→merge） |

三条硬规则（写在 `translate_body.py` 头部，改动前先读）：
1. `sl` **绝不设 auto** —— 中文源会被当英文译，产出「评选的(CFSMA)」乱码。
2. `[IMG:n]` 占位符不能丢：整段翻优先，数量对不上就按占位符切开、逐片翻再插回。
3. 429 不许死等 —— 一次失败即换域名/换源，退避重试会把 4 分钟的活拖到 25 分钟。

## 七、重要坑

1. **新增数据后站点不会自动刷新** —— 站点进程有缓存，必须
   `sudo systemctl restart friction` 才能在页面上看到。
2. **SSH 用 ubuntu，不是 root** —— root 密码未知。
3. **两个 token 别搞混** —— 删了 VPS 那个，自动同步立刻断。
4. **`/opt/friction` 属 archive，ubuntu 不能直接 scp 进去** —— 先传 `/tmp`，
   再 `sudo cp` + `sudo chown archive:archive`。
5. **改 friction.db 的脚本要用 archive 跑** —— `sudo -u archive python3 ...`，
   否则 `attempt to write a readonly database`。
6. **`cos_helper.py` 需要 PIL，别用 friction 的 venv** —— friction venv 没装 PIL，
   用 `/opt/study/venv/bin/python3`（或 `/opt/archive/venv/bin/python3`）。
7. **图片必须传 COS 才会在页面上显示** —— 页面 `_media_src` 只认 COS 直链，
   本地有文件不算。2026-10-05 踩过：2801 张 ok 图里 1117 张没传 COS，152 篇裂图。
   用 `verify_cos.py` 对账，缺了就 `cos_helper.py --only-missing` 补传。
8. **文章详情页的标签曾是死的** —— `article.html` 把标签渲染成 `<span class="chip">`，
   点了没反应（首页标签云、资讯页侧栏都是链接，唯独详情页漏了）。
   2026-10-06 已修：改成 `<a>` 跳 `/news?tag=<名>`，`app/static/css/components.css`
   加 `a.chip{text-decoration:none}`。注意 `_macros.html` 卡片里的 chip 在整卡
   `<a>` 内部，不能嵌套 a，别去改它。
9. **静态资源在 `app/static/`，不在 `/opt/friction/static/`** ——
   `/opt/friction/static/` 是媒体文件目录（图片落盘处），页面 CSS 在
   `/opt/friction/app/static/css/components.css`。改样式别改错地方。

---

## 八、海外信源采集流水线（Actions → VPS）`2026-10-01 收尾`

### 8.1 架构（已对齐 AIHOT 解耦）

```
GitHub Actions(friction-overseas.yml, Azure IP)
  ├─ scripts/collect_overseas.py      ← 纯采集器：抓列表 + 翻译 + url去重 + 摩擦相关性初筛
  ├─ scripts/collect_via_aggregator.py← WAF站兜底：Google/Bing News RSS(site:限定)
  └─ 提交 data/news_overseas.json
            │  (VPS 每 3h 定时拉取)
            ▼
/opt/friction/fetch_friction_overseas.py   ← 英文 Jaccard 聚簇(cluster_by_english)
            │
            ▼
pipeline.ingest()  →  scoring.py(领域评分 LEXICON + cluster_id 落库)  →  friction.db
```

> **关键边界**：Actions 侧**只采集不评分/不聚簇**（早期曾重复实现 score_article/cluster_articles，
> 已撤回，避免与 VPS scoring.py 两套 cluster 体系冲突）。聚簇与评分只在 VPS 发生，单一事实源。

### 8.2 信源状态（17 个，Actions 实测 2026-10-01）

| 状态 | 源 | 说明 |
|---|---|---|
| ✅ 直连正常 | Counterman / Fleet Owner / Underhood Service / Aftermarket News / BodyShop Business / ImportCar / Tomorrow's Technician / Repairer Driven News / Brake & Front End / Miba AG / Bosch 新闻室 | 命中 4~40 条 |
| 🟡 直连脆弱+聚合兜底 | **Ratchet+Wrench** | 首页 `/` 稳定 1 条；列表页 `/home/latest`·`/news` 404/403；已加 AGG_TARGETS 聚合兜底 |
| 🟡 直连 0 + 聚合兜底 | **US EPA** | Drupal views，新闻列表 AJAX 渲染，静态 `<a>` 无文章链接（直连恒 0）；靠 `AGG_SPA_SITES` 聚合（已入库 5 条） |
| 🔴 WAF 拦截→聚合兜底 | Tire Business / CCJ Digital / SAE International / TMD Friction | 直连被 Cloudflare/Akamai 拦；走聚合 RSS |

**结论**：17 源无"完全失效"。直连 13 个稳定；4 个 WAF 站 + EPA + R+W 共 6 个靠聚合兜底补齐覆盖。

### 8.3 聚簇修复（VPS 侧，已部署）

- **问题**：VPS `cluster_key` 取"最长中文领域词+前8字符"，对英文 overseas 标题完全失效 →
  711 条文章几乎全是孤立簇（860 文章 / 711 簇）。
- **修复**：`fetch_friction_overseas.py` 新增 `cluster_by_english()`（英文 token 集合去停用词，
  并集 Jaccard ≥ 0.34 归并，cluster_id=`ovs-<hash>` 幂等稳定）+ `recluster_overseas.py` 一次性重算。
- **效果**：overseas 68 条 → 50 簇；跨站同事件归并出 18 个多源簇；热点榜事件簇 94 个。
- **垃圾标题过滤**：`_bad_title` 黑名单挡 `Latest News` / `[Translate to English:]` / 纯英文≤15字符 等。

### 8.4 EPA / R+W 收尾（本次）

| 源 | 动作 | 验证 |
|---|---|---|
| US EPA | 删无效 `?page=1` 重定向入口；直连保留为文档化占位(预期0)；真实覆盖交聚合 | 诊断 `正则0命中` ✅ 符合预期 |
| Ratchet+Wrench | `list_url` 改首页 `/`（原 `home/latest` 404）；补进 AGG_TARGETS 聚合兜底 | 诊断 `OK(1)` ✅ 修复生效 |

> 仓库：本地 `master` → 远程 `main`（沙箱直连 GitHub 不稳，改用 Contents API 直更文件）。
> 工作流 `friction-overseas.yml` 支持 `workflow_dispatch` 输入 `diag_sources` 跑逐源诊断。

---

## 本轮改动速记（2026-10-06）

| 项 | 状态 | 说明 |
|---|---|---|
| 「报告」标签 | ✅ | scoring.py 加 31 条研报词条 + `TITLE_ONLY_TAGS={"报告"}`（仅标题命中） |
| 全库补标签 | ✅ | 新脚本 `/opt/friction/tag_backfill.py`，只加不删；287 篇 / 517 个 |
| 配图廊折叠 | ✅ | `article.html` 默认 12 张 + 展开按钮；id=104 从 267 张降到 12 张 |
| 污染译文清理 | ✅ | id=1996 title_zh、id=311 content_zh 清空重翻 |
| 全库补标签（三轮） | ✅ | 无标签 587 → 101；新加 16 类标签（企业动态/维修技术/商用车队/公司公告/产销数据/站点导航…） |
| 事件类标签 TITLE_ONLY | ✅ | 报告/企业动态/公司公告/产销数据/乘用车市场/专利/商用车队/站点导航 只在标题命中才打 |

运维命令：
```bash
sudo -u archive /opt/friction/venv/bin/python /opt/friction/tag_backfill.py          # dry-run
sudo -u archive /opt/friction/venv/bin/python /opt/friction/tag_backfill.py --apply  # 写库
sudo systemctl restart friction
curl -s -u qilin:‹SITE_BASIC_AUTH›（已轮换） 'http://127.0.0.1/friction/news?tag=报告' | grep -o '/friction/article/[0-9]*' | sort -u | wc -l
```
⚠️ VPS 上改 `/opt/friction` 下的文件必须用 `sudo -u archive`，否则 archive 用户后续写不动；
   跑 python 要用 `/opt/friction/venv/bin/python`（系统 python3 没装 flask）。

### 标签体系速查（49 个标签 / 10 组）

| 组 | 标签（篇数） |
|---|---|
| 应用 | 刹车片 242、新能源车 233、维修技术 121、商用车队 105 |
| 工艺 | 关键工序 228 |
| 行业 | 企业动态 187、行业展会 48、站点导航 26 |
| 性能 | 摩擦系数 92、磨损 72、NVH 32 |
| 市场 | 售后市场 60、公司公告 51、整车配套 27、产销数据 25、乘用车市场 32 |
| 研报 | 报告 39 |
| 标准 | 法规认证 51、SAE标准 29 |
| 材料 | 摩擦材料 62、配方组分 25 |
| 法规 | 限铜法规 34、制动排放 24 |

⚠️ 改 `scoring.py` 的 LEXICON 后必须跑：
```bash
sudo -u archive /opt/friction/venv/bin/python /opt/friction/tag_backfill.py           # 先看误伤
sudo -u archive /opt/friction/venv/bin/python /tmp/_mis.py                            # 误伤核查脚本
sudo -u archive /opt/friction/venv/bin/python /opt/friction/tag_backfill.py --apply
```

---

## 库存健康度（2026-10-06 东财补抓后）

| 指标 | 数值 |
|---|---|
| 文章总数 | 945（已删 28 篇导航页） |
| 有标签 | 918（97.1%） |
| 正文 ≥800 字符（可正常阅读） | **385 篇（40.7%）**（补抓前 327 / 35%） |
| 正文 200–800（摘要级） | 111 篇 |
| 正文 <200（空壳） | 449 篇 |

**这 449 篇要按下表分别处理，别一刀切**（完整论证见《接手指南》8.2）：

| 桶 | 数量 | 真相 | 处置 |
|---|---|---|---|
| A. Google 跳转壳 | 284 | VPS 连 news.google.com 恒 **000**，从未定位到真地址 | 走 Actions 出海（方案已上线） |
| B. Bing 搜索页 URL | 20 | 采集 bug —— 存的是 `bing.com/news/search?q=标题` | 同上，一并反查 |
| C. 原文就这么短 | ~150 | **不是空壳**，一句话企业新闻，300–500 字就是全部 | 保留，别反复重抓 |
| D. 富源没挖（东财公告） | 60 | JS 壳，正文在 PDF | ✅ **已救 60/60，净增 41.5 万字** |

> ⚠️ **别被「90 篇全部失败」骗了**：那批大多是 **HTTP 200**。先归因（C 还是 D）
> 再谈取舍 —— 东财就是这么从失败清单里挖出来的。

常用运维：
```bash
# ── 通用正文重抓（国内源；默认 dry 行为、--apply 才写库的多半不适用，这里不用加）
sudo -u archive /opt/friction/venv/bin/python fetch_detail.py --limit 400
# 加了 --skip-full 才会扫全量 raw_content<800 的（否则只挑已标 full 的，见下注）
sudo -u archive /opt/friction/venv/bin/python fetch_detail.py --skip-full --limit 200
# 重抓后必须跑，否则 content_level 不更新
sudo -u archive /opt/friction/venv/bin/python fix_level.py

# ── 东财公告专用：PDF 原文补抓（默认 dry-run，--apply 写库）
sudo -u archive /opt/friction/venv/bin/python fetch_eastmoney.py
sudo -u archive /opt/friction/venv/bin/python fetch_eastmoney.py --apply

# ── 归因诊断：某个源为什么抓不到？输出 A-E 五档
sudo -u archive /opt/friction/venv/bin/python diag_source.py --limit 10 --host nasn-auto
sudo -u archive /opt/friction/venv/bin/python diag_source.py --url 'https://...'

# ── 出海补抓：导出跳转壳清单 → GitHub Actions 解析 → pull 回结果 → 落库
sudo -u archive /opt/friction/venv/bin/python google_sync.py export
sudo -u archive /opt/friction/venv/bin/python google_sync.py import            # dry-run
sudo -u archive /opt/friction/venv/bin/python google_sync.py import --apply

# ── 清导航页（dry-run / 写库）
sudo -u archive /opt/friction/venv/bin/python clean_nav.py
sudo -u archive /opt/friction/venv/bin/python clean_nav.py --apply
```
⚠️ fetch_detail.py 的失败清单 `/opt/friction/fetch_detail_failed.json` 属主要求是 archive，
被 root 碰过就要 `sudo chown archive:archive` 回来。
⚠️ `fetch_detail.py` 的 `select_rows` 里有条 `content_level='full'`，语义是
**「只挑已标 full 的」**而非「跳过已完整的」——原意不可考，想全量扫记得加 `--skip-full`。
