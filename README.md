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
