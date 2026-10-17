import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

// 角色迁移：给 users.json 里缺 role 的老账号补角色，避免老管理员被降级锁死企业。
// 纯函数，零依赖，不读盘不发请求，不改入参。
// 导出：ROLE_ADMIN / ROLE_MEMBER 常量，assignMissingRoles(users) 补角色，
//       migrationSummary(result) 生成中文摘要。
// 兜底：created 缺失/非数字时按用户名排序，见 expected-exc 注释。

export const ROLE_ADMIN = 'admin';
export const ROLE_MEMBER = 'member';

const VALID_ROLES = new Set([ROLE_ADMIN, ROLE_MEMBER]);

// 取账号创建时间；拿不到时返回 null，由调用方走用户名兜底。
function createdAt(u) {
  const c = u && u.created;
  return typeof c === 'number' && Number.isFinite(c) ? c : null;
}

// 判断账号是否缺有效角色。
function needsRole(u) {
  return !u || !VALID_ROLES.has(u.role);
}

// 在缺角色的账号里挑出“最早”的那个用户名。
// 规则：created 可比较时取最小；否则按用户名字典序最小。
function pickEarliest(names, users) {
  let best = null;
  let bestCreated = null;
  for (const name of names) {
    const c = createdAt(users[name]);
    if (c === null) continue;
    if (bestCreated === null || c < bestCreated) {
      bestCreated = c;
      best = name;
    }
  }
  if (best !== null) return best;
  // expected-exc: 所有缺角色账号都没有可比较的 created，无法按时间判定，
  // 退化为按用户名字典序取最小者当最早，保证迁移可确定、可复现。
  return [...names].sort()[0];
}

export function assignMissingRoles(users) {
  const src = users && typeof users === 'object' ? users : {};
  const next = {};
  for (const name of Object.keys(src)) {
    const u = src[name];
    next[name] = u && typeof u === 'object' ? { ...u } : u;
  }

  const missing = Object.keys(next).filter((name) => needsRole(next[name]));
  if (missing.length === 0) {
    return { users: next, changed: false, assigned: {} };
  }

  const adminName = pickEarliest(missing, next);
  const assigned = {};
  for (const name of missing) {
    const role = name === adminName ? ROLE_ADMIN : ROLE_MEMBER;
    next[name] = { ...next[name], role };
    assigned[name] = role;
  }
  return { users: next, changed: true, assigned };
}

export function migrationSummary(result) {
  const assigned = result && result.assigned ? result.assigned : {};
  const names = Object.keys(assigned);
  if (names.length === 0) return '无需补角色';
  const parts = names.map((name) => {
    const label = assigned[name] === ROLE_ADMIN ? '管理员' : '普通成员';
    return `${name}→${label}`;
  });
  return `补角色 ${names.length} 个：${parts.join('，')}`;
}

// ---- 自检 ----
function runSelfCheck() {
  const fail = (msg) => {
    console.error(`SELFCHECK FAIL: ${msg}`);
    process.exit(1);
  };

  // 1. 两个老账号，created 1 与 2 → 早的补 admin
  {
    const input = { a: { created: 1 }, b: { created: 2 } };
    const r = assignMissingRoles(input);
    if (!r.changed) fail('case1 changed 应为 true');
    if (r.users.a.role !== ROLE_ADMIN) fail('case1 a 应为 admin');
    if (r.users.b.role !== ROLE_MEMBER) fail('case1 b 应为 member');
    if (r.assigned.a !== ROLE_ADMIN || r.assigned.b !== ROLE_MEMBER) fail('case1 assigned 不符');
  }

  // 2. 已有 member + 一个无 role → 无 role 的补 admin
  {
    const input = { x: { role: ROLE_MEMBER, created: 1 }, y: { created: 2 } };
    const r = assignMissingRoles(input);
    if (!r.changed) fail('case2 changed 应为 true');
    if (r.users.y.role !== ROLE_ADMIN) fail('case2 y 应为 admin');
    if (r.users.x.role !== ROLE_MEMBER) fail('case2 x 不应被改');
    if (Object.keys(r.assigned).length !== 1) fail('case2 assigned 应只有 1 个');
  }

  // 3. 全是 role → changed=false
  {
    const input = { a: { role: ROLE_ADMIN }, b: { role: ROLE_MEMBER } };
    const r = assignMissingRoles(input);
    if (r.changed) fail('case3 changed 应为 false');
    if (Object.keys(r.assigned).length !== 0) fail('case3 assigned 应为空');
    if (migrationSummary(r) !== '无需补角色') fail('case3 摘要应为 无需补角色');
  }

  // 4. 幂等：对结果再跑一次 → changed=false
  {
    const input = { a: { created: 1 }, b: { created: 2 } };
    const once = assignMissingRoles(input);
    const twice = assignMissingRoles(once.users);
    if (twice.changed) fail('case4 幂等 changed 应为 false');
  }

  // 5. 不修改传入对象
  {
    const input = { a: { created: 1 }, b: { created: 2 } };
    const before = JSON.stringify(input);
    assignMissingRoles(input);
    if (JSON.stringify(input) !== before) fail('case5 入参被修改');
  }

  // 6. created 缺失 → 按用户名排序兜底
  {
    const input = { zed: {}, abc: {} };
    const r = assignMissingRoles(input);
    if (!r.changed) fail('case6 changed 应为 true');
    if (r.users.abc.role !== ROLE_ADMIN) fail('case6 abc 应为 admin');
    if (r.users.zed.role !== ROLE_MEMBER) fail('case6 zed 应为 member');
  }

  // 7. 摘要格式
  {
    const r = assignMissingRoles({ admin: { created: 1 }, demo: { created: 2 } });
    const s = migrationSummary(r);
    if (s !== '补角色 2 个：admin→管理员，demo→普通成员') fail(`case7 摘要不符: ${s}`);
  }

  console.log('SELFCHECK PASS');
  process.exit(0);
}

// 跨平台判断“被当作脚本直接运行”：把 import.meta.url 与 argv[1] 都归一化为本地绝对路径再比较。
// Windows 下 argv[1] 是反斜杠路径，import.meta.url 是 file:///D:/...，直接字符串比较永远不相等。
function isMainModule() {
  if (!process.argv[1]) return false;
  return resolve(fileURLToPath(import.meta.url)) === resolve(process.argv[1]);
}

if (isMainModule()) runSelfCheck();