# 杭州亲子·活动汇（/tour）

VPS 上的活动聚合子站：**http://146.56.250.224/tour/**（口令 `qilin` / `0805`）。
汇总全杭州 13 区县 + 文旅的**政府门户**，外加**微信公众号**（搜狗）里"可报名参加"的亲子活动。

## 目录
```
tour/
  app.py                 Flask 站点（列表 / 数据源页 / 推送接口 / 查询清单接口）
  collect_tour.py        采集器（政府门户 JPAAS + 搜狗微信 + 详情补全 + 过期清理）
  sources.py             数据源清单（122 个公众号 + 20 个网站）
  templates/index.html   活动列表
  templates/sources.html 数据源清单页
  static/style.css
  tour.service           systemd 站点（waitress 127.0.0.1:8090）
  tour-collect.service   定时采集（gov + enrich，含过期清理）
  tour-collect.timer     每 3 小时
  tour_location.inc      nginx location /tour/
  pc_client/             电脑端"搜狗发现"脚本（住宅 IP 跑，推送到 VPS）
```

## 关键机制
- **JPAAS 接口**：余杭/萧山等区县用浙江集约化平台，列表 JS 渲染。逆向出
  `GET {base}/api-gateway/jpaas-publish-server/front/page/build/unit`，
  把页面 `queryData='{...}'` 解析后**拆成独立 GET 参数**（不是包成 queryData=），返回 `data.html`。
- **搜狗在电脑跑**：搜狗验证码看 IP，机房 IP 必被拦、住宅 IP 才行；电脑跑 `pc_client/push_wechat.py` 推送到
  `POST /tour/api/ingest`（Basic Auth + `X-Ingest-Token`）。
- **过期自动删除**：**只按活动日期**删（报名截止过了不删，用户可能已报名）；无日期按发布 45 天兜底。
- 详细运维见 `docs/VPS_凭据与运维.md` §八。
