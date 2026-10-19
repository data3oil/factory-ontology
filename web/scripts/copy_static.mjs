// copy_static.js — vite build 后补拷贝未被打包的静态资产进 public/
// 背景（2026-10-19 断链排查）：admin.html 与 food_app/ 放在 web/ 根下，vite publicDir:false
// 不会把它们带进 public/，而 server 只服务 public/ —— 两者一直被 SPA fallback 用首页 HTML
// 假活（HTTP 200 但内容是主页），admin 后台与移动端 PWA 实际从未可达。
// 另：admin.html 引 bootcdn CDN echarts，内网部署即挂；改引构建产物内的本地副本。
// 用法：由 package.json 的 build 脚本串行执行（vite build && node scripts/copy_static.js）
import { cpSync, mkdirSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const pub = join(webRoot, 'public');

if (!existsSync(pub)) {
  console.error('[copy_static] public/ 不存在，请先运行 vite build');
  process.exit(1);
}

const jobs = [
  { from: join(webRoot, 'admin.html'), to: join(pub, 'admin.html') },
  { from: join(webRoot, 'food_app'), to: join(pub, 'food_app') },
  { from: join(webRoot, 'src', 'lib', 'echarts.cjs'), to: join(pub, 'assets', 'echarts.min.js') },
];

let n = 0;
for (const j of jobs) {
  if (!existsSync(j.from)) {
    console.warn(`[copy_static] 跳过（源不存在）: ${j.from}`);
    continue;
  }
  mkdirSync(dirname(j.to), { recursive: true });
  cpSync(j.from, j.to, { recursive: true });
  const kind = statSync(j.to).isDirectory() ? 'dir ' : 'file';
  console.log(`[copy_static] ${kind} -> ${j.to.replace(webRoot, '.')}`);
  n++;
}

// 自检：拷贝后必须真实存在于 public/ 且不是 HTML 假活
const mustExist = [
  join(pub, 'admin.html'),
  join(pub, 'food_app', 'index.html'),
  join(pub, 'food_app', 'manifest.json'),
  join(pub, 'food_app', 'icon.svg'),
  join(pub, 'food_app', 'sw.js'),
  join(pub, 'assets', 'echarts.min.js'),
];
const missing = mustExist.filter((p) => !existsSync(p));
if (missing.length) {
  console.error(`[copy_static] 自检失败，缺失: ${missing.map((p) => p.replace(webRoot, '.')).join(', ')}`);
  process.exit(1);
}
console.log(`[copy_static] OK ${n} 项已拷贝，自检 ${mustExist.length}/${mustExist.length} 通过`);
