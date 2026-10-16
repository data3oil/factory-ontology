// roles.js — 工厂本体知识问答 Web 应用的纯角色模块。
// 用途: 定义角色常量与判定/可见性纯函数, 供 web/server 复用。
// 导出: ROLES, DEFAULT_ROLE, normalizeRole, roleForNewUser, isAdmin,
//       SETTINGS_GROUPS, visibleSettingsEntries,
//       canManageEnterprise, canManageUsers, canResetEnterprise, describeRole。
// 纯函数、零依赖、无副作用、不读文件不发请求。

export const ROLES = { ADMIN: 'admin', MEMBER: 'member' };
export const DEFAULT_ROLE = ROLES.MEMBER;

export function normalizeRole(v) {
  // fail-closed: 认不出就当普通成员, 不抛错。
  return v === ROLES.ADMIN ? ROLES.ADMIN : ROLES.MEMBER;
}

export function roleForNewUser(existingUserCount) {
  // fail-closed: 非 >=1 的数字一律视为首个用户 → 管理员。
  const n = typeof existingUserCount === 'number' && existingUserCount >= 1
    ? existingUserCount
    : 0;
  return n === 0 ? ROLES.ADMIN : ROLES.MEMBER;
}

export function isAdmin(user) {
  if (!user || typeof user !== 'object') return false;
  return normalizeRole(user.role) === ROLES.ADMIN;
}

export const SETTINGS_GROUPS = [
  { title: '建本体与演进', items: ['model', 'selfmodel', 'evolve'] },
  { title: '知识库', items: ['knowledge'] },
  { title: '模型与质量', items: ['eval'] },
  { title: '系统', items: ['orch', 'enterprise'] },
];

const ALL_ENTRIES = ['model', 'selfmodel', 'evolve', 'knowledge', 'eval', 'orch', 'enterprise'];
const MEMBER_ENTRIES = ['knowledge'];

export function visibleSettingsEntries(user) {
  return isAdmin(user) ? ALL_ENTRIES.slice() : MEMBER_ENTRIES.slice();
}

export function canManageEnterprise(user) {
  return isAdmin(user);
}

export function canManageUsers(user) {
  return isAdmin(user);
}

export function canResetEnterprise(user) {
  return isAdmin(user);
}

export function describeRole(user) {
  return isAdmin(user) ? '管理员' : '普通成员';
}

// ---- 直接运行自检 ----
const isDirectRun = typeof process !== 'undefined' && process.argv && process.argv[1] &&
  import.meta.url.endsWith(process.argv[1].replace(/\\/g, '/'));

if (isDirectRun) {
  const checks = [];
  const eq = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    checks.push({ name, ok, got, want });
  };

  eq('roleForNewUser(0)', roleForNewUser(0), 'admin');
  eq('roleForNewUser(3)', roleForNewUser(3), 'member');
  eq("normalizeRole('admin')", normalizeRole('admin'), 'admin');
  eq("normalizeRole('root')", normalizeRole('root'), 'member');
  eq('normalizeRole(undefined)', normalizeRole(undefined), 'member');
  eq("isAdmin({role:'admin'})", isAdmin({ role: 'admin' }), true);
  eq('isAdmin({})', isAdmin({}), false);
  eq('isAdmin(null)', isAdmin(null), false);

  const adminEntries = visibleSettingsEntries({ role: 'admin' });
  eq('visibleSettingsEntries(admin).length', adminEntries.length, 7);
  eq('visibleSettingsEntries(admin).order', adminEntries,
    ['model', 'selfmodel', 'evolve', 'knowledge', 'eval', 'orch', 'enterprise']);
  eq('visibleSettingsEntries(member)', visibleSettingsEntries({ role: 'member' }), ['knowledge']);

  const admin = { role: 'admin' };
  const member = { role: 'member' };
  eq('canManageEnterprise(admin)', canManageEnterprise(admin), true);
  eq('canManageUsers(admin)', canManageUsers(admin), true);
  eq('canResetEnterprise(admin)', canResetEnterprise(admin), true);
  eq('canManageEnterprise(member)', canManageEnterprise(member), false);
  eq('canManageUsers(member)', canManageUsers(member), false);
  eq('canResetEnterprise(member)', canResetEnterprise(member), false);

  eq("describeRole({role:'member'})", describeRole({ role: 'member' }), '普通成员');

  const failed = checks.filter((c) => !c.ok);
  if (failed.length === 0) {
    console.log('SELFCHECK PASS');
    process.exit(0);
  } else {
    for (const f of failed) {
      console.error(`FAIL: ${f.name} got=${JSON.stringify(f.got)} want=${JSON.stringify(f.want)}`);
    }
    process.exit(1);
  }
}