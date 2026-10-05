# Friction 摩擦材料站 · 接手指南（给电脑版 WorkBuddy）

> 写于 2026-10-06 凌晨。目标：任何一个新的 WorkBuddy 会话（云端沙箱或电脑版）
> 读完这份文件，就能直接接手继续干活，不用重新踩一遍坑。

---

## 0. 一句话说清这个项目

一个 **摩擦材料 / 制动器行业的中文资讯聚合站**，跑在腾讯云 VPS `146.56.250.224`，
路径 `/opt/friction`，对外 `http://146.56.250.224/friction`（Basic Auth 保护）。
英文原文采集 → 翻译成中文 → 配图（存腾讯 COS）→ 建站展示。

**核心矛盾：VPS 在国内，出不去；内容源和翻译源都在国外。**
整个架构都是围绕这个矛盾设计的：能出去的活（采集、翻译）搬到 GitHub Actions 上做，
VPS 只做"拉回来 + 存 + 展示"。

---

## 1. 架构总图

```
┌─ GitHub Actions（Azure 机房，能出国外）────────────────────────┐
│  qilin1991/friction-overseas                                   │
│   ├ friction-overseas.yml  海外信源采集 → data/news_overseas.json│
│   ├ friction-translate.yml 正文多源翻译 → data/news_zh.json      │
│   └ fetch-media.yml        媒体抓取                             │
└────────────────────────┬──────────────────────────────────────┘
                         │  VPS 侧定时 / 手动 pull（GitHub Contents API）
                         ▼
┌─ VPS 146.56.250.224 ──────────────────────────────────────────┐
│  /opt/friction                                                 │
│   ├ data/friction.db        SQLite，站点唯一数据源              │
│   ├ fetch_and_translate.sh  流水线（11 步）                     │
│   ├ qpush.py                翻译队列 push / merge 入口          │
│   └ app/                    Flask（waitress 127.0.0.1:8084）    │
│  nginx /friction → 反向代理 + Basic Auth                        │
└────────────────────────┬──────────────────────────────────────┘
                         │  图片只认 COS 直链
                         ▼
        腾讯 COS 桶 qilin-1449368663（ap-nanjing）
        static/friction_media/  ← 图床
        friction_backup/        ← 每日 04:00 数据库备份（留 14 天）
```

---

## 2. 凭据清单

| 项目 | 位置 / 值 | 说明 |
|---|---|---|
| VPS SSH | `ubuntu@146.56.250.224`，密码 `Lm~~1991cloud`，sudo 免密 | **不要**用 root |
| 站点 Basic Auth | `qilin` / `0805` | nginx `.htpasswd_work` |
| DB 文件 | `/opt/friction/data/friction.db` | 属主 archive |
| COS 凭据 | `/opt/friction/.deploy.env`（chmod 600） | 由 `cos_cred.py` 加载，代码里已无 AKID |
| GitHub Token | `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` | 登录 `qilin1991` |
| 沙箱推送工具 | `/workspace/gh_push.py`、`gh_dispatch_translate.py` | Contents API 直更文件（git 直连不稳） |

---

## 3. 日常运维速查

```bash
# 连 VPS（沙箱里用 sshpass，电脑版直接用系统 ssh / Termius）
sshpass -p 'Lm~~1991cloud' ssh -o StrictHostKeyChecking=no ubuntu@146.56.250.224

sudo systemctl restart friction         # 改了数据后必须重启，页面才刷新（有缓存）
sudo systemctl status friction archive-backup.timer friction-backup.timer
sudo -u archive python3 /opt/friction/healthcheck.py     # 健康体检
cd /opt/friction && sudo -u archive /opt/friction/venv/bin/python3 qpush.py push 50 all
cd /opt/friction && sudo -u archive /opt/friction/venv/bin/python3 qpush.py merge
```

改 DB 的脚本**必须** `sudo -u archive` 跑，否则 `attempt to write a readonly database`。
写不进 `/opt/friction` 就先 scp 到 `/tmp`，再 `sudo cp` + `sudo chown archive:archive`。

---

## 4. 正文翻译链路（2026-10-05 刚换过，最重要）

### 4.1 为什么换

| 指标 | LibreTranslate（旧） | Google 多源（新） |
|---|---|---|
| 行业术语 | caliper→**卡路里**、washer→**洗衣机**、nut→**坚果**、bleed→**出血** | 卡钳 / 密封垫圈 / 螺母 / 排气 |
| 段落结构 | 317 篇里 41 篇塌陷（6 段压成 1 段） | 实测 100% 对齐 |
| 速度 | 15 篇 263s | 15 篇 85s |
| 成本 | ¥0 | ¥0 |

**根因判断**：不是切分/处理器的问题，是引擎本身（Argos / OPUS 通用语料，
不懂汽车领域，且不支持 glossary）。所以换引擎，而不是继续补术语表。
（`fix_terms.py` 那种译后回改永远在追漏网字。）

### 4.2 怎么跑

```bash
# 沙箱里一键跑完剩余批次（push → dispatch → merge 自动循环）
cd /workspace && python3 -u run_batches.py 50 10
```

单轮三步：
1. VPS `qpush.py push <N> <new|lt|all>` → `data/translate_queue.json`
2. 触发 `friction-translate.yml`（`probe_only=false`）
3. VPS `qpush.py merge` → 回填 `article.content_zh / title_zh`

`scope` 含义：`new`=只补无译文缺口；`lt`=重翻有 LT 译文但未标记的；
`all`=new 优先再 lt；`lt-force`=忽略标记强制重翻。
**日常流水线只用 `new`**，否则会反复重翻同一批。

### 4.3 三条硬规则（改代码前必读，写在 translate_body.py 头部）

1. **`sl` 绝不设 `auto`** —— 中文源会被当英文译，产出「评选的(CFSMA)」乱码。
   VPS 侧只推 `lang='en'`，脚本再按停用词判 es/fr/de/it/pt 并显式指定。
2. **`[IMG:n]` 占位符绝不能丢** —— 整段翻优先，占位符数量对不上就把段落按占位符
   切开、逐片翻再插回。（第一版没这么做，22 段整段回退成英文。）
3. **429 不许死等** —— 一次失败立刻换域名/换源。第一版在 429 上退避重试，
   把 4 分钟的活拖到 25 分钟还没完，只能手动取消。

### 4.4 翻译源梯队（2026-10-05 在 Actions runner 上实测）

| 梯队 | 源 | 状态 |
|---|---|---|
| 主力 | `translate.googleapis.com` | ✅ |
| 备用 | `translate.google.com` / `.com.hk` / `.co.jp` | ✅（不同域名抗单点限流） |
| 兜底 | MyMemory（不同引擎，带 email 可提到 5 万字/日） | ✅（占位符会被改成 `[IMG: 1]`，已归一） |
| 不可用 | 微软 Edge(404)、DeepL 网页端(429)、Papago(404)、有道(非 JSON)、百度(空译文)、Reverso(403)、Lingva(原样返回)、LibreTranslate 镜像(400/DNS) | ❌ |

---

## 5. 工作方法与思路（给下一个接手的 AI / 人）

这是用户明确要求留下的部分——**不是命令清单，是判断方式**。

### 5.1 先证因，再动手
翻译质量差时，第一反应不该是"再补几个术语映射"。我先写了 `lt_probe.py`：
把句子**孤立**输入、**带上下文**输入、真实库里句子三种方式喂给 LT，
三种全错 → 证明是引擎的错，不是切分/上下文丢失。然后才决定换引擎。
**打补丁永远追不上根因。**

### 5.2 探针先行，默认只读
新增 workflow 时 `probe_only` 默认 `true`。先在目标环境（runner）上把可达性、
可用性、质量测一遍，拿到数据再决定要不要写实施代码。
沙箱到不了的 Google，就用 Actions 跑探针——**换环境验证，而不是猜**。

### 5.3 dry-run → 小批量 → 全量
`clean_pseudo.py` / `dedup.py` / `fix_terms.py` 全部带 `--apply` 开关，
默认只打印。全量翻译战役先跑 15 篇做 LT vs Google 对照，
确认段落结构、占位符、术语都对，才放行 300+ 篇。

### 5.4 门禁比修复更重要
不指望引擎永远正确，而是在管线里设卡：
占位符数量、段落数、`out == input`（没翻）、长度塌缩（被摘要）。
触发就换源，换不了就保底留原文让 `zh_guard.py` 兜。
**宁可留一段英文，也不要产出结构坏掉的中文。**

### 5.5 成本敏感，先算账
用户在意"长回答单次 1 元都嫌贵"。所以换翻译方案时先算：
全站重翻 308 篇 ≈ 162 万输入 + 211 万输出 token，LongCat ≈ ¥20、Google 官方 API ≈ ¥900。
结论是**一分钱不花也能解决**（Actions + gtx 免费端点），就没有必要上 LLM。

### 5.6 用架构绕开环境限制，而不是硬碰
VPS 出口访问 Google 全线不通（实测 Network unreachable），这是死结，改不了一行代码。
但 GitHub Actions runner 在 Azure，本来就通——把翻译搬过去，
用 GitHub 当"海外出口 + 存储中转"，VPS 只管拉。

### 5.7 每一步留回滚
- 每日 04:00 备份 DB → COS（留 14 天），带 `PRAGMA integrity_check` 门禁
- 不动 `article` 表结构，新增需求用**侧表**（`translate_done`）
- 改脚本前先 `.bak_*`

### 5.8 幂等必须有显式标记（2026-10-06 刚踩）
重翻战役跑了 9 轮，回填 468 条，**实际只翻了 52 篇**——
因为"已重翻"没有标记，`ORDER BY id DESC` 每轮都取到同样 50 篇。
教训：**任何"取一批来处理"的循环，都必须有"处理完"的落库标记**，
否则就是空转。修法是加侧表 `translate_done(url PK, ts, engine)`。

---

## 6. 已踩过的坑（按痛感排序）

1. **改了数据页面不刷新** —— 必须 `sudo systemctl restart friction`（进程有缓存）。
2. **图片必须传 COS 才显示** —— `_media_src()` 只认 COS 直链，本地有文件不算。
   2026-10-05 踩过：2801 张 ok 图里 1117 张没传 COS，152 篇裂图。
   用 `verify_cos.py` 对账，缺了就 `cos_helper.py --only-missing` 补传。
3. **`/opt/friction` 属 archive** —— ubuntu 不能直接 scp 进去，先 `/tmp` 再 sudo cp。
4. **改 DB 要 `sudo -u archive`** —— 否则 readonly。
5. **`cos_helper.py` 要 PIL** —— friction 的 venv 没装，用 `/opt/study/venv/bin/python3`。
6. **`sl="auto"` 会把中文源译烂** —— 见 4.3 第 1 条。
7. **重翻循环没有标记会空转** —— 见 5.8。
8. **EN→ZH 字符比 0.30 左右是正常的** —— 不要拿 1:1 当标准去判"被摘要"，
   真正的反摘要检查是**段落数是否一致**。

---

## 7. 当前状态（2026-10-06 00:30）

| 项 | 值 |
|---|---|
| 文章总数 | 973（full 588） |
| 已用 Google 翻译并标记 | 进行中（目标 300+ 篇 LT 译文全量重翻 + 41 篇缺口） |
| 每日备份 | ✅ 已上线（04:00，COS `friction_backup/`） |
| 伪文章清理 | ✅ 39 篇栏目页已删 |
| 去重 | ✅ 29 篇已删 |
| COS 密钥 | ✅ 已移出代码到 `.deploy.env` |
| `/friction/api/kb` 鉴权 | ✅ 外部 401（nginx 挡），仅 loopback 免鉴——虚惊一场 |

---

## 8. 未决 / 待办

- [ ] 重翻战役收尾后：跑 `fix_terms.py --apply` + `zh_guard.py` + `healthcheck.py`，
      并 `restart friction` 后抽查 3-5 篇页面
- [ ] 把 `translate_local.py`（LT）从 `fetch_and_translate.sh` 第 9 步摘掉，
      改成 `qpush.py merge`（拉回）+ `qpush.py push 50 new`（推新），
      并给 `friction-translate.yml` 加 schedule 形成自动闭环
- [ ] MyMemory 提额邮箱（填 `mymemory_email` 可 5 千 → 5 万字/日）
- [ ] `/opt/archive/.deploy.env` 权限 666 → 改 600
- [ ] 504 张失败图片（下轮自动重试）、77 篇有图无占位符
- [ ] 歧义词待人工确认：转子(137) / 垫(600) / 鼓(102) / 脚(87) / 电流(61)
- [ ] NAS 备份 `\\192.168.31.240\Home1\archive_backup` SMB 445/139 不通（VPS 侧无解）

---

## 9. 电脑版 WorkBuddy 怎么接手

1. **建目录**：在本机建一个工作目录（比如 `D:\friction`），把
   `/workspace/friction-vps-sync/` 下这几个文件放进去：`接手指南_WorkBuddy.md`、
   `VPS_凭据与运维.md`，以及 `/workspace` 下的 `run_batches.py`、`gh_push.py`、
   `gh_dispatch_translate.py`、`cmp_lt_google.py`。
2. **克隆仓库**：`git clone https://github.com/qilin1991/friction-overseas`
   （或本机已有 `D:\workbuddy` 就在旁边建子目录）。
3. **直接 SSH 操作**：电脑版可以装 OpenSSH / 用 Termius，
   `ssh ubuntu@146.56.250.224`（密码见第 2 节）。本机能直连的话比沙箱稳。
4. **让电脑版先读这份文件**：新会话第一句就说
   「读一下 `D:\friction\接手指南_WorkBuddy.md`，然后继续未完成的事」。
5. **电脑版更适合的活**（沙箱做不了的）：
   - 用本机 Chromium 登录态抓知乎/小红书/微信（沙箱 headless 拿不到评论）
   - 直接看截图反馈 UI 问题
   - 处理本地 Excel / Word / PDF（材料测试数据、文献转 PPT）
6. **沙箱更适合**：长时间后台跑批、http 服务预览、批量 API 调用。
   两边都读同一份文档 + 同一个 GitHub 仓库，就不会各干各的。
