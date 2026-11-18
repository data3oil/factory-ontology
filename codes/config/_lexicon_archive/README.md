# 已归档的孤儿词典（2026-11-18）

本目录存放未被注册、且全仓零引用的 `lexicon_*.json`。
**归档而非删除**：搬回 `codes/config/` 并在 `kbs.json` 注册即可恢复。

## 判定口径

一个词典判为孤儿，需同时满足三条：

1. 未被 `codes/config/kbs.json` 与 `kbs.dev.json` 任一注册；
2. 全仓没有按库名拼路径加载它的硬引用（`run.py` / `*_demo.py` / `benchmark*` / `api_server` 等）；
3. `scripts/verify_lexicon_align.py` 报为孤儿。

复现：

```bash
python scripts/verify_lexicon_align.py --verbose                    # 列孤儿与缺词典
python scripts/verify_lexicon_align.py --archive-orphans --dry-run  # 只看会搬哪些
```

第 2 条是唯一无法静态完全证伪的一条：如果某处用字符串拼出库名动态加载，
静态扫描看不到。删前请按下面的清单扫一眼自己是否认得这些名字。

## 已归档 16 份

「推断来源」一列按文件名推断，仅作人工复核的线索，不作事实主张。

| 文件 | 推断来源 | 说明 |
|---|---|---|
| `lexicon_ai4i_v4.json` | ai4i | 旧版本，现行注册用的是 `lexicon_ai4i.json` |
| `lexicon_library_inventory.json` | library | 旧名，现行注册用的是 `lexicon_library.json` |
| `lexicon_seis_db.json` | seismic | 试验分支，现行注册用的是 `lexicon_seismic.json` |
| `lexicon_seis_teams.json` | seismic | 同上 |
| `lexicon_qipei.json` | 汽配 | 早期试验；曾因 `qipei` 前缀与 `qipei_2` 撞车 |
| `lexicon_ent_a.json` | 测试 | 成对的实体测试样例 |
| `lexicon_ent_b.json` | 测试 | 同上 |
| `lexicon_erp_auto.json` | 测试 | ERP 单表试验 |
| `lexicon_erp_multi.json` | 测试 | ERP 多表试验 |
| `lexicon_tp3p_a1.json` | 测试 | 三方件 3P 试验 A1 |
| `lexicon_tp3p_db.json` | 测试 | 三方件 3P 试验 |
| `lexicon_tp3p_multi2.json` | 测试 | 三方件 3P 多表试验 |
| `lexicon_tp_valve_a1.json` | 测试 | 阀门试验 A1 |
| `lexicon_tp_valve_multi.json` | 测试 | 阀门多表试验 |
| `lexicon_aas_example.json` | 示例 | 示例文件，本就不该进注册表 |
| `lexicon_factory_multi_1792136654867.json` | 残留 | 自动建模留下的带时间戳文件，此前未入库 |

## 保留、未归档的 4 份

这 4 份同样未注册，但存在硬引用，列在 `scripts/verify_lexicon_align.py`
的 `_KEEP_ORPHANS` 白名单里，仍留在 `codes/config/`：

| 文件 | 硬引用位置 |
|---|---|
| `lexicon_factory.json` | `run.py` 默认 `table=factory`，建库按 `lexicon_{table}.json` 读写 |
| `lexicon_food_products.json` | `benchmark_graphrag` / `logical` 的 `--lexicon` 默认值、`api_server` 的 fallback |
| `lexicon_valve_products.json` | `valve_demo.py` 硬编码路径 |
| `lexicon_precision.json` | 有 `codes/data_precision/` 数据目录（未注册的建库试作） |

## 为什么归档而不是直接删除

本地 git 能恢复，但换 clone 或换机器之后，`codes/config/` 下只剩十几个文件凭空消失，
谁也说不清少了什么。归档成 rename 之后，diff 里能看出「从哪移到哪」，
日后要查「这个库当年到底有没有词典」还有据可查。

## 恢复方式

```bash
git mv codes/config/_lexicon_archive/lexicon_X.json codes/config/
# 然后在 codes/config/kbs.json（或 kbs.dev.json）里注册对应库
python scripts/verify_lexicon_align.py     # 确认不再是孤儿
```

## 已知未处理

- `kbs.dev.json` 里 `onb_251` / `onb_446` / `onb_814` / `onb_979` 已注册但没有词典文件。
  这是「注册了没词典」，与「有词典没注册」是两回事，本笔不动。
- 含中文实体名的 IRI 形如 `u5df2u5165u5e93`，可移植但难读。改成拼音需要引入拼音表，
  影响面大于本笔范围，留待单独评估。
