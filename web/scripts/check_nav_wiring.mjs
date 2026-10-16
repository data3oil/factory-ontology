// 导航接线检查器。在 D:/factory-ontology/web 下运行: node scripts/check_nav_wiring.mjs
import { readdirSync, statSync, readFileSync, existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join, relative, sep, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// 以脚本自身位置定位 web 根，保证从任意 cwd 运行都一致
const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const srcRoot = join(webRoot, 'src');
const appPath = join(srcRoot, 'App.svelte');

const results = [];
function record(id, name, ok, detail) {
  results.push({ id, name, ok, detail });
}

function readText(p) {
  try {
    return readFileSync(p, 'utf8');
  } catch {
    return null;
  }
}

// 递归收集 srcRoot 下的 .svelte/.js 文件（相对 webRoot 的 POSIX 路径）
function collectSourceFiles(root) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(root);
  } catch {
    // 目录不存在或无权限：返回空数组，交由调用方按缺失处理
    return out;
  }
  for (const name of entries) {
    const full = join(root, name);
    let st;
    try {
      st = statSync(full);
    } catch {
      continue;
    }
    if (st.isDirectory()) {
      out.push(...collectSourceFiles(full));
    } else if (name.endsWith('.svelte') || name.endsWith('.js')) {
      out.push(relative(webRoot, full).split(sep).join('/'));
    }
  }
  return out;
}

const appSrc = readText(appPath);

// ---------- C1 tab 分支完整性 ----------
const REQUIRED_TABS = [
  'model', 'selfmodel', 'query', 'dashboard', 'eval',
  'knowledge', 'assets', 'lexasset', 'evolve', 'orch',
];
{
  const found = new Set();
  if (appSrc) {
    const re = /activeTab\s*===\s*['"]([A-Za-z0-9_]+)['"]/g;
    let m;
    while ((m = re.exec(appSrc)) !== null) found.add(m[1]);
  }
  const missing = REQUIRED_TABS.filter((k) => !found.has(k));
  const ok = appSrc !== null && missing.length === 0;
  record('C1', 'tab 分支完整性', ok,
    ok ? `${REQUIRED_TABS.length}/${REQUIRED_TABS.length}`
       : `缺失: ${missing.join(', ') || '(App.svelte 不可读)'}`);
}

// ---------- C2 组件完整性 ----------
const COMPONENTS = [
  'AnalysisResult', 'AssetPanel', 'DashboardPanel', 'EvalPanel', 'EvolvePanel',
  'KnowledgePanel', 'LexiconAssetPanel', 'ModelGraph', 'SelfModelPanel', 'WelcomeModel',
];
{
  const problems = [];
  for (const c of COMPONENTS) {
    const p = join(srcRoot, 'components', `${c}.svelte`);
    const src = readText(p);
    if (src === null) { problems.push(`${c}: 文件缺失`); continue; }
    if (src.trim().length === 0) { problems.push(`${c}: 文件为空`); continue; }
    if (!appSrc) { problems.push(`${c}: App.svelte 不可读`); continue; }
    const importRe = new RegExp(`import\\s+${c}\\s+from\\s+['"]\\./components/${c}\\.svelte['"]`);
    if (!importRe.test(appSrc)) problems.push(`${c}: 未 import`);
    const tagRe = new RegExp(`<${c}[\\s/>]`);
    if (!tagRe.test(appSrc)) problems.push(`${c}: 未使用标签`);
  }
  const ok = problems.length === 0;
  record('C2', '组件完整性', ok, ok ? `${COMPONENTS.length}/${COMPONENTS.length}` : problems.join('; '));
}

// ---------- C3 新导航接线 ----------
{
  const problems = [];
  if (!appSrc) {
    problems.push('App.svelte 不可读');
  } else {
    for (const c of ['SideNav', 'AppTopBar']) {
      const importRe = new RegExp(`import\\s+${c}\\s+from\\s+['"][^'"]*${c}\\.svelte['"]`);
      if (!importRe.test(appSrc)) problems.push(`${c}: 未 import`);
      const tagRe = new RegExp(`<${c}[\\s/>]`);
      if (!tagRe.test(appSrc)) problems.push(`${c}: 未使用标签`);
    }
  }
  const ok = problems.length === 0;
  record('C3', '新导航接线', ok, ok ? 'SideNav + AppTopBar 已接线' : problems.join('; '));
}

// ---------- C4 导航映射完整 ----------
{
  const problems = [];
  if (!appSrc) {
    problems.push('App.svelte 不可读');
  } else {
    const navMatch = appSrc.match(/NAV_MAP\s*=\s*\{([\s\S]*?)\}/);
    if (!navMatch) {
      problems.push('缺少 NAV_MAP');
    } else {
      const body = navMatch[1];
      const keys = new Set();
      const kvRe = /([A-Za-z0-9_]+)\s*:\s*['"]([A-Za-z0-9_]+)['"]/g;
      let m;
      while ((m = kvRe.exec(body)) !== null) keys.add(m[1]);
      for (const k of ['ask', 'dashboard', 'delivery']) {
        if (!keys.has(k)) problems.push(`NAV_MAP 缺键 ${k}`);
      }
      const valRe = /:\s*['"]([A-Za-z0-9_]+)['"]/g;
      while ((m = valRe.exec(body)) !== null) {
        if (!REQUIRED_TABS.includes(m[1])) problems.push(`NAV_MAP 值非法: ${m[1]}`);
      }
    }
    const setMatch = appSrc.match(/SETTINGS_ENTRIES\s*=\s*\[([\s\S]*?)\]/);
    if (!setMatch) {
      problems.push('缺少 SETTINGS_ENTRIES');
    } else {
      const body = setMatch[1];
      const keys = new Set();
      const kRe = /['"]([A-Za-z0-9_]+)['"]/g;
      let m;
      while ((m = kRe.exec(body)) !== null) keys.add(m[1]);
      for (const k of ['model', 'selfmodel', 'evolve', 'knowledge', 'eval', 'orch', 'enterprise']) {
        if (!keys.has(k)) problems.push(`SETTINGS_ENTRIES 缺 ${k}`);
      }
    }
  }
  const ok = problems.length === 0;
  record('C4', '导航映射完整', ok, ok ? 'NAV_MAP + SETTINGS_ENTRIES 完整' : problems.join('; '));
}

// ---------- C5 无 API 断链 ----------
{
  const problems = [];
  let repoRoot = null;
  try {
    repoRoot = execFileSync('git', ['rev-parse', '--show-toplevel'], { cwd: webRoot, encoding: 'utf8' }).trim();
  } catch (e) {
    problems.push(`git rev-parse 失败: ${e.message}`);
  }

  if (repoRoot) {
    let baselineFiles = [];
    try {
      const out = execFileSync('git', ['ls-tree', '-r', '--name-only', 'HEAD'], { cwd: repoRoot, encoding: 'utf8' });
      baselineFiles = out.split('\n').map((s) => s.trim()).filter(Boolean)
        .filter((p) => p.startsWith('web/src/') && (p.endsWith('.svelte') || p.endsWith('.js')));
    } catch (e) {
      problems.push(`git ls-tree 失败: ${e.message}`);
    }

    const endpointRe = /\/api\/[A-Za-z0-9_/{}.\-$]+/g;
    const baseline = new Map(); // endpoint -> Set(file)
    for (const f of baselineFiles) {
      let content;
      try {
        content = execFileSync('git', ['show', `HEAD:${f}`], { cwd: repoRoot, encoding: 'utf8' });
      } catch (e) {
        problems.push(`git show HEAD:${f} 失败: ${e.message}`);
        continue;
      }
      let m;
      while ((m = endpointRe.exec(content)) !== null) {
        if (!baseline.has(m[0])) baseline.set(m[0], new Set());
        baseline.get(m[0]).add(f);
      }
    }

    const workFiles = collectSourceFiles(srcRoot);
    const workText = workFiles.map((f) => readText(join(webRoot, f)) || '').join('\n');

    const missing = [];
    for (const [ep, files] of baseline) {
      if (!workText.includes(ep)) {
        missing.push(`${ep} (原于 ${[...files].join(', ')})`);
      }
    }
    if (missing.length > 0) {
      problems.push(`断链端点 ${missing.length} 个: ${missing.join(' | ')}`);
    }
  }

  const ok = problems.length === 0;
  record('C5', '无 API 断链', ok, ok ? '全部端点仍在' : problems.join('; '));
}

// ---------- C6 旧平铺导航已下线 ----------
{
  const LEGACY_CLASSES = new Set(['tabbar', 'tab', 'tab-icon']);
  const problems = [];
  if (!appSrc) {
    problems.push('App.svelte 不可读');
  } else {
    const classRe = /class\s*=\s*["']([^"']*)["']/g;
    let m;
    while ((m = classRe.exec(appSrc)) !== null) {
      const value = m[1];
      for (const token of value.split(/\s+/)) {
        if (!LEGACY_CLASSES.has(token)) continue;
        const start = Math.max(0, m.index - 40);
        const end = Math.min(appSrc.length, m.index + m[0].length + 40);
        const ctx = appSrc.slice(start, end).replace(/\s+/g, ' ');
        problems.push(`命中 token "${token}" @ ...${ctx}...`);
      }
    }
  }
  const ok = problems.length === 0;
  record('C6', '旧平铺导航已下线', ok, ok ? '无旧 class' : problems.join('; '));
}

// ---------- C7 可达性完整 ----------
{
  const problems = [];
  const reachable = new Set();
  if (!appSrc) {
    problems.push('App.svelte 不可读');
  } else {
    // NAV_MAP 取值
    const navMatch = appSrc.match(/NAV_MAP\s*=\s*\{([\s\S]*?)\}/);
    if (navMatch) {
      const valRe = /:\s*['"]([A-Za-z0-9_]+)['"]/g;
      let m;
      while ((m = valRe.exec(navMatch[1])) !== null) reachable.add(m[1]);
    }
    // SETTINGS_ENTRIES 项（排除非 tab 项 'enterprise'）
    const setMatch = appSrc.match(/SETTINGS_ENTRIES\s*=\s*\[([\s\S]*?)\]/);
    if (setMatch) {
      const kRe = /['"]([A-Za-z0-9_]+)['"]/g;
      let m;
      while ((m = kRe.exec(setMatch[1])) !== null) {
        if (m[1] !== 'enterprise') reachable.add(m[1]);
      }
    }
    // DELIVERY_TABS 取值（不存在按空对象处理）
    const delMatch = appSrc.match(/DELIVERY_TABS\s*=\s*\{([\s\S]*?)\}/);
    if (delMatch) {
      const valRe = /:\s*['"]([A-Za-z0-9_]+)['"]/g;
      let m;
      while ((m = valRe.exec(delMatch[1])) !== null) reachable.add(m[1]);
    }
  }
  const unreachable = REQUIRED_TABS.filter((k) => !reachable.has(k));
  const ok = appSrc !== null && unreachable.length === 0;
  record('C7', '可达性完整', ok,
    ok ? `${REQUIRED_TABS.length}/${REQUIRED_TABS.length}`
       : unreachable.map((k) => `${k} 不在 NAV_MAP / SETTINGS_ENTRIES / DELIVERY_TABS 任一中`).join('; '));
}

// ---------- 输出 ----------
const pad = (s, n) => (s + ' '.repeat(n)).slice(0, n);
for (const r of results) {
  const status = r.ok ? 'PASS' : 'FAIL';
  console.log(`${pad(r.id + ' ' + r.name, 30)} ${status}  ${r.detail}`);
}
const anyFail = results.some((r) => !r.ok);
if (anyFail) {
  process.exitCode = 1;
} else {
  console.log('NAV WIRING OK');
}

// ---------- 自检输出（真实运行记录） ----------
// 运行: cd D:/factory-ontology/web && node scripts/check_nav_wiring.mjs
// 期望: C1/C2/C5 PASS, C3/C4/C6 FAIL, 退出码 1
//
// 实际输出（本机执行）:
// C1 tab 分支完整性               PASS  10/10
// C2 组件完整性                   PASS  10/10
// C3 新导航接线                   FAIL  SideNav: 未 import; SideNav: 未使用标签; AppTopBar: 未 import; AppTopBar: 未使用标签
// C4 导航映射完整                 FAIL  缺少 NAV_MAP; 缺少 SETTINGS_ENTRIES
// C5 无 API 断链                  PASS  全部端点仍在
// C6 旧平铺导航已下线             FAIL  仍存在 class="tabbar"; 仍存在 class="tab"
// 退出码: 1