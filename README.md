# 行业手续费总览

独立静态发布的期货公司黄金手续费总览。页面只读取 `site/data/fees.json`，访问流量不经过原阿里云服务器。

- GitHub Pages 提供公开页面。
- GitHub Actions 每周一 04:17（Asia/Shanghai）检查完整 150 家名录。
- 只有全量采集和数据校验都通过时才提交新快照；失败时保留上一版。
- `FEE_ORGANIZATION` 与 `FEE_CONTACT_EMAIL` 必须配置为仓库 Actions secrets。

本地校验：

```bash
python -X utf8 collector/scripts/verify_snapshot.py
python -m http.server 4173 --directory site
node scripts/verify_ui.cjs
```
