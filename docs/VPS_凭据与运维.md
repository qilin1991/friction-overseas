# Friction Hub · VPS 凭据与运维手册

> 更新：2026-10-01 14:18
> 目的：把账号固定下来，避免再混

## 一、SSH 登录（一律用这个）

| 项目 | 值 |
|---|---|
| 账号 | **ubuntu** |
| 密码 | `Lm~~1991cloud` |
| 主机 | `146.56.250.224` |
| root 权限 | ubuntu **可免密 sudo**，直接 `sudo xxx` |

```bash
sshpass -p 'Lm~~1991cloud' ssh -o StrictHostKeyChecking=no ubuntu@146.56.250.224
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
| Basic Auth | `qilin` / `0805` |

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
curl -s -u qilin:0805 'http://127.0.0.1/friction/news?tag=报告' | grep -o '/friction/article/[0-9]*' | sort -u | wc -l
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

---

## 七、文献接口（OA PDF 全文本地化，2026-10-17 上线）

把已抓取的开放获取（OA）论文 PDF 下载到腾讯 COS，并通过站内「文献」栏目直接在线阅读。
**合规底线：拒绝 sci-hub 类途径**，只用 OpenAlex 标 `is_oa=1` 且带 `oa_url` 的合法全文。

### 7.1 数据流

```
paper(is_oa=1, oa_url)
  → fetch_oa_pdfs.py 下载 OA 全文 PDF → 上传 COS（桶 qilin-1449368663，路径 static/papers/paper_<id>.pdf）
  → 回写 paper.pdf_cos_key
  → 站点 /friction/papers/<id>/pdf 从 COS 拉取后流式返回（inline，继承 /friction 的 Basic Auth）
```

- 库表新增列 `paper.pdf_cos_key TEXT`（ALTER 幂等，已上线）。
- COS 路径固定 `static/papers/paper_<id>.pdf`，公开读 URL：
  `https://qilin-1449368663.cos.ap-nanjing.myqcloud.com/static/papers/paper_<id>.pdf`

### 7.2 抓取脚本

`/opt/friction/fetch_oa_pdfs.py`（默认 dry-run，加 `--apply` 才真正下载+上传；`--workers N` 并发，默认 8）

```bash
# 先看会抓哪些（不落库、不上传）
sudo -u archive /opt/friction/venv/bin/python /opt/friction/fetch_oa_pdfs.py
# 真正抓取（后台跑，日志 /tmp/foa_full.log）
sudo -u archive /opt/friction/venv/bin/python -u /opt/friction/fetch_oa_pdfs.py --apply --workers 8 > /tmp/foa_full.log 2>&1 &
```

要点：
- **落地页探测**：部分 `oa_url` 是 HTML 落地页而非直链，脚本用 BeautifulSoup 提取 `.pdf`/`/pdf` 候选链接重试一次。
- **并发安全**：每个 worker 线程独立开 SQLite 连接（SQLite 连接不能跨线程）；上传 COS 用 `_UP_LOCK` 串行化（`CosS3Client.put_object` 不可并发）。
- **校验**：要求 `%PDF-` 魔数 + `8000B ≤ size ≤ 120MB`，否则跳过（不写 `pdf_cos_key`）。
- **现状（2026-10-17）**：5,206 篇 OA 中约 3,738 条有可用 `oa_url`；数据中心 IP 下仅 arXiv + 跟随重定向的
  BMC/Frontiers/Springer 等能真下到，MDPI/Wiley/ScienceDirect/Hindawi 被 WAF 挡（403），属预期跳过。
  已落地 ~240+ 篇到 COS（持续抓取中，**COS失败=0**）。

### 7.3 站内路由

`app/routes/paper.py` 新增 `GET /papers/<int:pid>/pdf`：

```python
p = Paper.by_id(pid)
if not p or not p.get("pdf_cos_key"):
    abort(404)
resp = _cos.get_object(Bucket=_bucket, Key=p["pdf_cos_key"])
# ⚠️ qcloud_cos 的 StreamBody.read() 无参只返回 1024B！必须用带大小的循环读
_stream = resp["Body"]
data = b""
while True:
    _chunk = _stream.read(1024 * 1024)
    if not _chunk:
        break
    data += _chunk
return Response(data, mimetype="application/pdf",
                headers={"Content-Disposition": "inline; filename*=UTF-8''paper_<id>.pdf"})
```

> ⚠️ **踩坑**：`qcloud_cos` 的 `get_object()["Body"].read()` 不传大小**只返回前 1024 字节**（已实测），
> 导致首版只吐 1KB 假 PDF。已改为 1MB 循环读，修复后单篇正常返回 3–60MB 真 PDF。

### 7.4 Nginx（关键修复）

`friction_location.inc`（`/etc/nginx/conf.d/`）把 `/friction` 反代到 127.0.0.1:8084，但**此前未被 active server 块 include**，
导致 `/friction/**` 落到 `location /` → 8080（另一个站），文献接口 404。已修复：在
`/etc/nginx/sites-available/archive` 的 include 清单里加一行：

```nginx
    include /etc/nginx/conf.d/friction_location.inc;
```

改完 `sudo nginx -t && sudo systemctl reload nginx`。

- 访问地址：`http://146.56.250.224/friction/papers/<id>/pdf`
- 鉴权：复用 `/friction` 子站密码 **`qilin:0805`**（`.htpasswd_work`，与主站 `qilin:1991` 不同）。
- 列表页「📄 全文」、详情页「本库 PDF ↧」按钮已接该路由（见 `templates/papers.html`、`templates/paper.html`）。

### 7.5 运维

```bash
# 查已落地数量
sqlite3 /opt/friction/data/friction.db "select count(*) from paper where pdf_cos_key is not null"
# 看抓取进度
tail -f /tmp/foa_full.log
# 单篇手动补抓（确认 pdf_cos_key 已写、COS 对象存在）
sudo -u archive /opt/friction/venv/bin/python -c "
import cos_cred, sqlite3, os
from qcloud_cos import CosConfig, CosS3Client
sid,skey=cos_cred.require()
cos=CosS3Client(CosConfig(Region=os.environ.get('COS_REGION','ap-nanjing'),SecretId=sid,SecretKey=skey,Token=None))
h=cos.head_object(Bucket=os.environ.get('COS_BUCKET','qilin-1449368663'),Key='static/papers/paper_5.pdf')
print('size=', h.get('Content-Length'))
"

---

## 八、活动分站 /tour（2026-10-19 上线，同日升级为「全杭州」版）

杭州**亲子出游向**「可参加活动」聚合站：全杭州 13 区县 + 市级 + 文旅的政府门户，外加搜狗微信公众号。
账号密码同 `/friction` 子站（**`qilin:0805`**，`.htpasswd_work`）。

### 8.1 架构

- 代码 `/opt/tour/`，库 `/opt/tour/tour.db`（表 `event`，含 `column` 来源栏目字段），Flask + waitress `127.0.0.1:8090`
- nginx：`/etc/nginx/conf.d/tour_location.inc` → `location /tour/`
  - ⚠️ **必须 include 进 active 的 `/etc/nginx/sites-enabled/archive`**。
    注意 `sites-enabled/archive` 与 `sites-available/archive` 是**两个不同 inode 的文件**（不是软链），
    改 `sites-available` 不生效，必须改 `sites-enabled`。
- systemd：`tour.service`（站点）+ `tour-collect.service` / `tour-collect.timer`（每 3 小时采集，`--only all` = gov→sogou→enrich 顺序）
- 访问：`http://146.56.250.224/tour/`，鉴权 `qilin:0805`；支持 `?family=1` 亲子优先、`?district=` 区域、`?category=` 类型

### 8.2 采集源与机制（关键：JPAAS 接口已打通）

| 源 | 方式 | 现状 |
|---|---|---|
| **政府门户 ×13** | 杭州/上城(hzsc)/拱墅/滨江(hhtz)/萧山/余杭/临平/钱塘/富阳/临安/桐庐/建德 + `wgly.hangzhou.gov.cn`(文旅) | ✅ 稳定 |
| **JPAAS 集约化平台** | 余杭/萧山等用浙江 JPAAS，列表 JS 渲染。逆向出运行时接口：`GET {base}/api-gateway/jpaas-publish-server/front/page/build/unit`，把页面里 `queryData="{...}"`（单引号 JSON）解析后**拆成独立 GET 参数**（jQuery `data:` 行为，不是包成 queryData= 一个参数！），返回 `{success:true,data:{html}}` 含渲染后列表 | ✅ 核心突破口 |
| **老 CMS 静态** | 临平等首页静态 `/art/` 链接直接解析；栏目页跟随 1 层（每门户最多 20 个） | ✅ |
| 搜狗微信 | 指定公众号 + 活动词搜索 | ⚠️ 连查约 15 次触发 IP 验证码，遇拦即退、下轮再试 |
| 本地宝 | 「每月活动」汇总页 | 默认不并入（多为商业活动、易过期） |

- **待补域名**：西湖区（xihuhangzhou.gov.cn）、淳安县（chunan.gov.cn）从 VPS 解析不到（DNS 不通），域名确认后在 `collect_tour.py` 的 `PORTALS` 里加一行即可。
- 过滤：**强家庭信号**（亲子/花海/市集/展览/演出/重阳/敬老/马拉松/非遗/户外…）+ **强政务噪声**（印发/方案/产业/高质量/开展/关于/调研/会议…约 90 个词）。
- 去重：uid = md5(归一化 URL)，跨栏目/跨门户同文自动合并。
- **详情补全（enrich）**：对缺时间的报名类条目抓原文，抽「活动时间/地点/报名方式」。
- ⚠️ 日期解析坑：文章 URL `/art/2026/art_xxx.html` 只有年没有月，**不能**用 URL 日期做近期过滤（会把全部文章算成 1 月 1 日而误杀），必须取列表项**邻近文本**里的日期。
- ⚠️ **筛选失效坑**：`location = /tour { return 301 /tour/; }` 会在跳转时**丢掉查询串**，导致 `/tour?district=X` 全部退回 `/tour/`。
  已改为 `return 301 /tour/$is_args$args;`，并把 Flask 路由只保留 `/tour/`（`url_for` 才会生成带斜杠的地址）。
- ⚠️ **公众号去重双坑**：① 搜狗链接都是 `weixin.sogou.com/link?url=XXX`，唯一标识在 query 里；若 `norm_url()` 剥掉 query，所有公众号文章会塌成同一个 uid **互相覆盖、只剩最后一条**。② 但搜狗每次返回的 token 又不同，按 URL 去重会把**同一篇**抓成多条。
  最终方案：公众号条目 uid 用 **`md5(标题 + 区域)`** 做稳定去重键（跨查询/跨 token 都稳定），其余条目仍按归一化 URL。
- 招聘类（招聘/就业/岗位/劳动合同制/应届毕业…）已列入噪声词，**不上架**。

### 8.3 运维

```bash
sudo systemctl status tour
sudo systemctl start tour-collect.service        # 手动采集一次
sudo journalctl -u tour-collect -n 50 --no-pager # 看采集日志
sqlite3 /opt/tour/tour.db "select district,count(*) from event group by district"
curl -s -u qilin:0805 http://127.0.0.1/tour/ -o /dev/null -w '%{http_code}\n'
```

2026-10-19 全杭州版首跑：32 条干净活动（超山粉黛花海/良渚文创市集/湘湖马拉松/敬老月/邻居节/千岛湖创意生活周/野趣杭州户外消费季等）。

### 8.4 电脑端公众号采集（关键：搜狗验证码看 IP）

**结论：搜狗微信验证码主要看 IP 信誉，机房 IP 必被拦，住宅 IP 才行。**

| 环境 | 实测 | 结论 |
|---|---|---|
| 腾讯云 VPS | 账号名查询全 0，第 5 个查询即触发验证码 | ❌ 不适合搜狗 |
| 沙箱（海外机房） | 每查询 7–10 条，48–60 查询零验证码 | ✅ 可用于一次性广搜 |
| 用户家宽（杭州住宅 IP） | 最干净、同城 | ✅ 长期首选 |

**架构**：电脑只做"搜狗发现"，VPS 做"入库 + 展示 + 政府门户"。

- 接收接口：`POST /tour/api/ingest`，nginx Basic Auth（`qilin:0805`）+ 头 `X-Ingest-Token: tour_push_7f3a9c2e`（可用环境变量 `TOUR_INGEST_TOKEN` 覆盖）
  - 服务端统一过滤：`clean_title` + `is_joinable` + **近 180 天** + 排除外地城市（德州/北京/上海…）
  - 去重：公众号按 `md5(标题+区域)`，与采集器一致
- 查询清单接口：`GET /tour/api/queries` 下发 `SOGOU_QUERIES`（账号名 + 街道 + 全区县亲子 + 活动类型 + "活动报名/招募"高信号词，共 60 条），保证电脑端与 VPS 同步
- 电脑端脚本：`push_wechat.py`（仅依赖 requests）+ `start_wechat_push.bat`（双击循环，首轮自动装 requests，每轮间隔 50 分钟，日志 `push_wechat.log`）
  - 开机自启：`Win+R` → `shell:startup` → 放入 bat 快捷方式
  - 换 VPS/口令：改脚本顶部 `VPS_BASE` / `AUTH` / `TOKEN`

- 搜狗 `tsn` 时间筛选（tsn=1/2/3）返回空页，**不可用**；只能按相关度取回后在服务端按日期过滤。
- 2026-10-19 首轮广搜（沙箱，48 查询）：467 条原始 → 过滤后 **26 条近期公众号**（10 月），加上政府 32 条共 **58 条**。

### 8.5 数据源清单页 `/tour/sources`

- **122 个公众号**（按 政府发布 / **街道·社区** / 共青团·青春 / 青少年·少年宫 / 妇联 / 工会 / 文旅 / 图书馆 / 博物馆 / 志愿服务 / 教育 / 本地媒体 分组）+ **20 个相关网站**（政府门户 / 文旅 / 少年宫 / 图书馆 / 博物馆 / 景区）
- **街道·社区 78 个**，排序 星桥→临平→上城→拱墅→西湖→滨江→钱塘→余杭/萧山：
  - 星桥：星桥发布、星桥社区居委会、星桥街道南星社区/贾家社区/班荆社区、掌上星仪、星桥街道发布、贾家社区发布
  - 临平其他：温暖乔司、今日东湖、临平南苑文化、临平街道总工会、崇贤新城生活圈、看塘栖、共富运河
  - 上城：幸福清波、潮起望江、采荷发布、乐居凯旋、福泽四季青、笕桥发布、彭埠之声、九堡社区 + 9 个社区号（运新/明桂/云峰/普新/茶亭苑/建华/魅力城/丽江/八堡）
  - 拱墅：长庆发布、武林微公众、爱天水、你好朝晖、米市巷发布、湖墅发布、大美小河、和睦红、微大关、上塘发布、微祥符、半山发布、乐活康桥、西苑第一社区
  - 西湖：北山发布、杭州西溪发布、灵隐事、翠苑发布、文新发布、古荡发布、转塘生活报、留下发布、蒋村发布、杭州三墩发布、美丽双浦、西溪庐海社区
  - 滨江：西兴发布、长河发布、浦沿发布
  - 钱塘：下沙发布、魅力白杨、新湾新城、大美临江、杭铣社区、云水社区
  - 余杭/萧山：仓前发布、天下径山、今日城厢、萧山北干/闻堰/宁围/盈丰、掌上瓜沥、萧山临浦、新街派
- **未挖到**：上城南星/紫阳/丁兰/湖滨/小营、拱墅拱宸桥、钱塘义蓬/河庄/前进（裸词歧义或无独立号），后续可按需定向补
- 街道/社区号名字无规律（"XX发布"/"XX街道"/"XX微社区"/"掌上XX"/"今日XX"/"微XX"），**探测法**：按街道名搜，取"账号名含街道名"的真实账号；裸词歧义时加"街道/社区"后缀
- 数据集中在 `/opt/tour/sources.py` 的 `ACCOUNTS`（名称, 体系, 地区）和 `SITES`（名称, 类型, url, 备注）；改完 `systemctl restart tour` 生效
- 公众号链接为**搜狗 type=2 搜索入口**（微信无公开主页，这是最稳的查看方式）
- 实测发现（搜狗 type=1 账号搜索已被限制，改用 type=2 反查真实账号）：
  - 西湖区的发布号是 **「美丽西湖」**（不是"西湖发布"）
  - 杭州市妇联是 **「西子女性」**
  - 杭州文旅是 **「杭州文广旅游发布」**
  - 萧山共青团就叫 **「萧山共青团」**（不是"青春萧山"）
  - 青少年/少年宫是 **「杭州青少年活动中心」**，另有「杭州市第二课堂」「Do都城」
- 注意：搜狗结果里真实账号名在 `<span class="all-time-y2">`，不是 `.account`（早期抓错导致 account 字段全落成查询词）。

### 8.6 采集查询矩阵 & 过期自动删除

- **查询矩阵自动派生**：`collect_tour.py` 从 `sources.py` 的 `ACCOUNTS` 里把**所有「街道·社区」账号**自动追加进 `SOGOU_QUERIES`（当前 **134 条查询** = 56 条关键词 + 78 个街道·社区号）。以后加号**只改 sources.py 一处**，查询表自动跟上。
- **VPS 定时任务改为只跑政府门户 + 详情补全**（`--only gov` + `--only enrich`），搜狗交给用户电脑（`push_wechat.py`），避免机房 IP 被验证码拖死。
- **过期自动删除**（`expire_sweep`，每次采集末尾执行）：
  - ✅ **只按「活动日期」删除**：`event_date` 已过今天 → 删除
  - ❌ **报名截止过了不删**（`signup_deadline` 不参与删除判断）——用户可能已经报名，活动仍在，删掉就是误删
  - 无活动日期的，仅按 `published_at` 兜底 TTL（**45 天**）→ 删除；同样不看报名截止
  - 日期猜解 `guess_event_date()`：取摘要里的「M月D日」，**仅当与发布时间相差 -7~+180 天**才采信；只有「11/12月发布 → 1/2月活动」才跨年，避免把文中旧日期误当活动日
  - 前端状态徽标 `_status` 同步：只有活动日期过了才显示"已结束"，报名截止过了不显示已结束
- ✅ 对照自测：插入「活动=明天、报名截止=昨天」→ 保留；插入「活动=昨天」→ 删除。
- **去重（`dedup_duplicates`，每轮执行）**：政府 CMS 会把同一篇按栏目各生成一个 `art_id`（URL 不同），
  且列表里有截断标题（带"..."）。所以 URL 去重抓不到，改为**同区域 + 标题归一化（去空格/标点/省略号）+ 截断前缀**判重，保留标题最完整的一条；
  同时 `norm_url` 对 `/art/` 与 `/col/colN/art/` 两种形式统一按 `art_<id>.html` 归一。
- **重抓不再清空补全字段**：`upsert` 改为 `INSERT ... ON CONFLICT(uid) DO UPDATE`，新值为空则沿用旧的 `event_date/location/signup_method/summary`（否则每次重跑 gov 都会把 enrich 的成果洗掉）。
- ⚠️ 公众号文章正文抓不到（搜狗 `/link` 一跟就跳反爬页），所以活动时间**从搜索结果摘要**（`txt-info`）里抽，而非点进原文。
- 功能自测：插入一条 `event_date=昨天` 的记录 → 跑 `--only expire` → 该记录被删（已验证）。

### 8.7 多来源（政府 / 公众号 / 豆瓣同城）与左侧导航

- 来源分三类，左侧栏切换（`?stype=gov|wechat|douban`）：
  - **政府发布**：13 区县门户 + 文旅（`source` 不是"公众号/豆瓣同城"的）
  - **公众号**：搜狗微信（`source='公众号'`）
  - **豆瓣同城**：`source='豆瓣同城'`
- **豆瓣同城采集**（`douban()`，VPS 可直接抓，无需登录）：
  - 列表 `https://www.douban.com/location/hangzhou/events/?start=0/20/40/60/80`
  - 条目 `li.list-entry`：标题 `.title a`、`<time itemprop=startDate/endDate>`、地点 `meta[itemprop=location]`
  - **用结束日期做 event_date**（多日活动不被提前删）；`published_at` 留空
  - 当前杭州近期约 10 条（脱口秀/沉浸式剧/手作/评弹/读书会等）
- **排序**：活动日 >= 今天的按活动日**由近到远**排在前，其余按发布时间倒序（"近期可去"优先）。
- VPS 定时任务现为 `gov → douban → enrich` 三步（均含过期清理）。

### 8.3 运维

```bash
sudo systemctl status tour
sudo systemctl start tour-collect.service        # 手动采集一次
sudo journalctl -u tour-collect -n 50 --no-pager # 看采集日志
sqlite3 /opt/tour/tour.db "select source,count(*) from event group by source"
curl -s -u qilin:0805 http://127.0.0.1/tour/ -o /dev/null -w '%{http_code}\n'
```
