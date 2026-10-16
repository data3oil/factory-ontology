// 文案清洁门：禁止对外文案出现"演示/示例/demo"字样。
// 用法: node scripts/check_no_demo_wording.mjs [--write-baseline] [--allow-grow]
// 规则: 扫 web/src 与 web/server 下 .svelte/.js，命中 演示|示例|独立单词 demo/Demo/DEMO。
// 允许清单: 目录名路径白名单行、过滤测试残渣 kb 行、ontology.js 的 data_ 目录常量行。
// 棘轮: 与 web/scripts/no_demo_baseline.json 对比，只降不涨；无基线报 FAIL 提示建基线。
// 退出码: 有违规 1，全清或持平 0。

import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, dirname, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
function findRoot(start) {
  let d = start;
  while (true) {
    if (existsSync(join(d, 'package.json'))) return d;
    const p = dirname(d);
    if (p === d) throw new Error('未找到含 package.json 的项目根');
    d = p;
  }
}
const ROOT = findRoot(HERE);
const SCAN_DIRS = [join(ROOT, 'src'), join(ROOT, 'server')];
const BASELINE = join(ROOT, 'scripts', 'no_demo_baseline.json');
const SKIP = new Set(['node_modules', '__pycache__']);

// 命中词：演示/示例，或独立单词 demo（大小写不敏感，单词边界，不匹配 demo_power）
const HIT = /演示|示例|\bdemo\b/i;

// 允许清单：命中但不计违规
function isAllowed(line, relPath) {
  // 1. 目录名路径白名单行：含 data_valve 且形如路径列举
  if (/data_valve/.test(line) && /["'`/]/.test(line)) return true;
  // 2. 过滤测试残渣 kb 的行：含带下划线的 demo_ 标识符
  if (/\bdemo_\w+/.test(line)) return true;
  // 3. ontology.js 里以 data_ 开头的目录字符串常量行
  if (relPath.endsWith('server/ontology.js') && /["'`]data_/.test(line)) return true;
  return false;
}

function walk(dir, out) {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name)) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) walk(full, out);
    else if (/\.(svelte|js)$/.test(name)) out.push(full);
  }
}

function scan() {
  const files = [];
  for (const d of SCAN_DIRS) walk(d, files);
  const sites = [];
  for (const f of files) {
    const rel = relative(ROOT, f).split(sep).join('/');
    const lines = readFileSync(f, 'utf8').split('\n');
    lines.forEach((line, i) => {
      if (!HIT.test(line)) return;
      if (isAllowed(line, rel)) return;
      sites.push({ key: `${rel}:${i + 1}`, snippet: line.trim().slice(0, 120) });
    });
  }
  return sites;
}

// 分辨力自检：假片段必须被命中
function selfCheck() {
  const probe = '这是演示数据';
  if (!HIT.test(probe)) throw new Error('自检失败：假片段未被命中');
  if (HIT.test('demo_power')) throw new Error('自检失败：demo_power 被误命中');
}

const args = process.argv.slice(2);
const writeBaseline = args.includes('--write-baseline');
const allowGrow = args.includes('--allow-grow');

selfCheck();
const sites = scan();
const count = sites.length;

if (writeBaseline) {
  let prev = null;
  if (existsSync(BASELINE)) {
    try { prev = JSON.parse(readFileSync(BASELINE, 'utf8')); } catch { prev = null; }
  }
  if (prev && count > prev.count && !allowGrow) {
    console.error(`拒绝写基线：新 count ${count} > 旧 ${prev.count}（只降不涨，需 --allow-grow）`);
    process.exit(1);
  }
  const data = {
    count,
    sites: sites.map(s => s.key),
    note: '文案清洁门基线：只降不涨',
  };
  writeFileSync(BASELINE, JSON.stringify(data, null, 2) + '\n');
  console.log(`已写基线：${count} 处`);
  process.exit(0);
}

if (!existsSync(BASELINE)) {
  for (const s of sites) console.log(`违规明细 ${s.key}: ${s.snippet}`);
  console.log(`未清理 ${count} 处（无基线）`);
  console.error('基线文件不存在，先跑 --write-baseline 建立基线');
  process.exit(1);
}

let base;
try {
  base = JSON.parse(readFileSync(BASELINE, 'utf8'));
} catch (e) {
  console.error(`基线文件损坏：${e.message}`);
  process.exit(1);
}

const baseSet = new Set(base.sites || []);
const problems = [];

// ③ 基线自洽
if (base.count !== (base.sites || []).length) {
  problems.push(`基线自洽失败：count=${base.count} != sites.length=${(base.sites || []).length}`);
}
// ① 当前违规数 ≤ 基线
if (count > base.count) {
  problems.push(`违规数 ${count} > 基线 ${base.count}`);
}
// ② 每个当前违规点都在基线里
for (const s of sites) {
  if (!baseSet.has(s.key)) problems.push(`新增违规点：${s.key}`);
}

for (const s of sites) console.log(`违规明细 ${s.key}: ${s.snippet}`);
console.log(`未清理 ${count} 处（基线 ${base.count}）`);

if (problems.length) {
  for (const p of problems) console.error(p);
  process.exit(1);
}
process.exit(0);