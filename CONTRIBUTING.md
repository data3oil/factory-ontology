# Contributing

欢迎提 issue 和 PR。这是工厂本体驱动的数据问答框架，目标是让中小企业把台账变成可问的资产。

## Bug 报告
- 附复现命令（数据文件 + 问题 + 期望/实际输出）
- 标注环境（Python 版本 / 是否用本地 Ollama / 操作系统）

## PR 要求
- 代码 + 对应测试；跑通 `cd codes && python -m pytest tests/`（后端 29 项）
- 改前端（`web/`）跑通 `cd web && node node_modules/vite/bin/vite.js build`
- 涉及问答能力的改动，附 benchmark 复现结果（`python benchmark.py data/<数据集>.csv`）
- **示例数据一律用虚构数据**：仓库不接收任何真实工厂数据、客户数据、人名
- **保持词典驱动**：不要在规则引擎里硬编码具体中文词，字段语义走 `config/lexicon_*.json`
- 新增行业：加 `data_<行业>/` + kbs.json 条目 + 词典，规则引擎与检索链路不动
- 方法论借鉴需在 NOTICE 声明（哪怕只借鉴机制，Apache-2.0 也要求署名）

## 本地化约束
- 核心路径保持零第三方依赖（Python 标准库）；MySQL/PostgreSQL/Excel 驱动放 `requirements-optional.txt`
- 数据与知识在本地闭环，公开仓库不含任何企业私有数据

## 提交规范
- 提交信息：`feat:` / `fix:` / `chore:` / `docs:` 前缀 + 简述
- 版本号递增：改 `codes/api_server.py` / `codes/run.py` / `web/package.json` / README 徽标 / CHANGELOG 五处保持一致
- 写中文注释，面向国内中小企业使用者

## 版权与贡献授权（DCO）

本仓库著作权归**北京三油科技有限责任公司**所有，以 Apache License 2.0 授权。
提交 PR 即表示你同意：

1. 你的贡献以 Apache License 2.0 授权给本项目及所有下游使用者（inbound = outbound）；
2. 你确认自己有权提交这段代码（自有原创，或已获原权利人许可），并已按 `NOTICE`
   要求标注任何借鉴/移植来源；
3. 贡献不包含第三方专有代码、真实工厂/客户数据、个人信息或未授权的密钥。

请在每个提交末尾加一行签署（DCO 1.1 简式），用 `git commit -s` 自动生成：

```
Signed-off-by: 你的名字 <你的邮箱>
```

未签署的 PR 会在合并前请补齐。若你是代表雇主提交，请确认雇主已授权该贡献。

> 若未来本项目需要**双许可**（开源 + 商业授权并行），届时会另行公告贡献者协议（CLA）；
> 在此之前，DCO 即为全部要求。

