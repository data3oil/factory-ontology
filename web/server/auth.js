// auth.js — 企业用户登录会话体系（多租户 = 多用户）
// 用户存储: web/users.json。密码 scrypt 哈希加盐。登录签发不透明随机 token（内存会话，含过期）。
// 每个企业用户绑定唯一 kb（单企业唯一性），数据隔离复用后端多租户内核。
import { scryptSync, randomBytes, timingSafeEqual } from 'crypto';
// 角色与权限矩阵的单一真相源（纯函数模块，2026-10-16 落地"角色调整到真"）
import { normalizeRole, roleForNewUser, describeRole, visibleSettingsEntries, isAdmin, canManageEnterprise, canManageUsers, canResetEnterprise } from './roles.js';
// 老账号补角色（角色引入前建的账号没有 role 字段，不补会把企业管理员降级锁死）
import { assignMissingRoles, migrationSummary } from './role_migration.js';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
// 用户存储文件（web/users.json，相对 web/server -> ../users.json）
const USERS_FILE = join(__dirname, '..', 'users.json');

// 会话有效期（毫秒），默认 12 小时
const SESSION_TTL = 12 * 3600 * 1000;

// 内存会话表：token -> {username, expires}
// 同时持久化到 SESSIONS_FILE，node server 重启后仍能恢复登录（根治"重启即掉线"）。
const SESSIONS_FILE = join(__dirname, '..', 'sessions.json');
let sessions = new Map();

/** 加载持久化会话（启动时恢复；清掉已过期项）。 */
function loadSessions() {
  try {
    if (existsSync(SESSIONS_FILE)) {
      const d = JSON.parse(readFileSync(SESSIONS_FILE, 'utf-8'));
      const now = Date.now();
      const fresh = {};
      for (const [k, v] of Object.entries(d || {})) {
        if (v && typeof v.expires === 'number' && v.expires > now) fresh[k] = v;
      }
      sessions = new Map(Object.entries(fresh));
    }
  } catch (e) { /* 损坏视为空 */ }
}
/** 保存会话到磁盘（整表覆盖）。 */
function saveSessions() {
  try {
    writeFileSync(SESSIONS_FILE, JSON.stringify(Object.fromEntries(sessions)), 'utf-8');
  } catch (e) { /* 写失败忽略 */ }
}
/** 供 index.js 启动时恢复会话。 */
export function restoreSessions() { loadSessions(); }

// ── 用户文件读写 ─────────────────────────────────────────────
function loadUsers() {
  try {
    if (existsSync(USERS_FILE)) {
      const d = JSON.parse(readFileSync(USERS_FILE, 'utf-8'));
      const users = (d && d.users) || {};
      // 读到老账号就先补角色（幂等：没有缺角色的账号时什么都不做、不写盘）
      const mig = assignMissingRoles(users);
      if (mig.changed) {
        saveUsers(mig.users);
        console.log('[roles] ' + migrationSummary(mig));
      }
      return mig.users;
    }
  } catch (e) { /* 损坏视为空 */ }
  return {};
}

function saveUsers(users) {
  writeFileSync(USERS_FILE, JSON.stringify({ users }, null, 2), 'utf-8');
}

// ── 密码哈希（scrypt 加盐）────────────────────────────────────
function hashPassword(password, salt) {
  return scryptSync(String(password), salt, 64).toString('hex');
}

function verifyPassword(password, salt, expectedHash) {
  if (!salt || !expectedHash) return false;
  const actual = scryptSync(String(password), salt, 64);
  const expected = Buffer.from(expectedHash, 'hex');
  if (actual.length !== expected.length) return false;
  return timingSafeEqual(actual, expected);
}

// ── 公开用户对象（剔除敏感字段）────────────────────────────────
export function publicUser(u) {
  if (!u) return null;
  return {
    username: u.username,
    enterpriseName: u.enterpriseName || '',
    logo: u.logo || '',
    industry: u.industry || '',
    kb: u.kb || '',
    onboarded: !!u.onboarded,
    // 角色面：前端只渲染后端给的东西，不在浏览器里各写一套权限矩阵
    role: normalizeRole(u.role),
    roleLabel: describeRole(u),
    settingsEntries: visibleSettingsEntries(u),
    can: {
      enterprise: canManageEnterprise(u),
      users: canManageUsers(u),
      reset: canResetEnterprise(u),
    },
  };
}

// ── 企业用户 CRUD ─────────────────────────────────────────────
/** 创建企业用户。kb 缺省自动生成 ent_<username>（单企业唯一命名空间）。 */
export function createUser({ username, password, enterpriseName = '', logo = '', industry = '', kb = '', onboarded = false, role = '' }) {
  const name = String(username || '').trim();
  if (!name) return { ok: false, error: '用户名必填' };
  if (!password) return { ok: false, error: '密码必填' };
  const users = loadUsers();
  if (users[name]) return { ok: false, error: '用户名已存在' };
  const salt = randomBytes(16).toString('hex');
  const user = {
    username: name,
    salt,
    hash: hashPassword(password, salt),
    enterpriseName: String(enterpriseName || '').trim().slice(0, 50),
    logo: String(logo || '').trim().slice(0, 500),
    industry: String(industry || '').trim().slice(0, 30),
    kb: kb || 'ent_' + name.replace(/[^A-Za-z0-9_-]/g, '_'),
    onboarded: !!onboarded,
    // 第一个创建的人 = 企业管理员，其后加入的 = 普通成员（roleForNewUser 里的口径）
    role: role ? normalizeRole(role) : roleForNewUser(Object.keys(users).length),
    created: Date.now(),
  };
  users[name] = user;
  saveUsers(users);
  return { ok: true, user: publicUser(user) };
}

/** 取用户（私有，含哈希）。 */
function getUserByUsername(username) {
  const users = loadUsers();
  return users[username] || null;
}

/** 读取企业用户（公开）。 */
export function getUser(username) {
  return publicUser(getUserByUsername(username));
}

/** 更新企业用户字段（enterpriseName/logo/industry/onboarded/kb）。 */
export function updateUser(username, patch) {
  const users = loadUsers();
  const u = users[username];
  if (!u) return { ok: false, error: '用户不存在' };
  if (patch.enterpriseName !== undefined) u.enterpriseName = String(patch.enterpriseName).trim().slice(0, 50);
  if (patch.logo !== undefined) u.logo = String(patch.logo).trim().slice(0, 500);
  if (patch.industry !== undefined) u.industry = String(patch.industry).trim().slice(0, 30);
  // kb 支持显式清空(null/'' 都视为清空)与更新——reset 场景需要把旧 kb 清掉重建
  if (patch.kb !== undefined) u.kb = patch.kb === null || patch.kb === '' ? '' : String(patch.kb).trim();
  if (patch.onboarded !== undefined) u.onboarded = !!patch.onboarded;
  if (patch.role !== undefined) u.role = normalizeRole(patch.role);
  users[username] = u;
  saveUsers(users);
  return { ok: true, user: publicUser(u) };
}

/** 删除企业用户。 */
export function deleteUser(username) {
  const users = loadUsers();
  if (!users[username]) return { ok: false, error: '用户不存在' };
  delete users[username];
  saveUsers(users);
  return { ok: true };
}

/** 列出全部企业用户（公开面，供管理员在设置里看成员与角色）。 */
export function listUsers() {
  const users = loadUsers();
  return {
    ok: true,
    users: Object.values(users)
      .map(publicUser)
      .sort((a, b) => (isAdmin(b) ? 1 : 0) - (isAdmin(a) ? 1 : 0) || String(a.username).localeCompare(String(b.username))),
  };
}

// ── 登录/会话 ─────────────────────────────────────────────────
/** 校验用户名密码，成功签发会话 token。 */
export function login(username, password) {
  const u = getUserByUsername(String(username || '').trim());
  if (!u) return { ok: false, error: '用户名或密码错误' };
  if (!verifyPassword(password, u.salt, u.hash)) {
    return { ok: false, error: '用户名或密码错误' };
  }
  const token = randomBytes(32).toString('hex');
  sessions.set(token, { username: u.username, expires: Date.now() + SESSION_TTL });
  saveSessions();
  return { ok: true, token, user: publicUser(u) };
}

/** 注销（使 token 失效）。 */
export function logout(token) {
  if (token) { sessions.delete(token); saveSessions(); }
  return { ok: true };
}

/** 解析 token → 公开用户。会话失效/不存在返回 null。 */
export function resolveUser(token) {
  if (!token) return null;
  const s = sessions.get(token);
  if (!s) return null;
  if (s.expires < Date.now()) { sessions.delete(token); saveSessions(); return null; }
  return publicUser(getUserByUsername(s.username));
}

/** 会话健康检查（供 /api/auth/me 用，返回当前用户公开对象 + 会话剩余）。 */
export function me(token) {
  const u = resolveUser(token);
  if (!u) return { ok: false, error: '未登录或会话已失效', unauthenticated: true };
  return { ok: true, user: u };
}

// ── 首次启动种子用户（仅当 users.json 不存在或为空）────────────
export function seedUsersIfEmpty() {
  const users = loadUsers();
  if (Object.keys(users).length > 0) return;
// 首次启动：只建一个管理员账号，口令取环境变量 FO_ADMIN_PASSWORD；
  // 未设置则随机生成，并在控制台打印一次（默认口令 admin123 这类是明确的交付红线）。
  const seedPwd = String(process.env.FO_ADMIN_PASSWORD || '').trim() || randomBytes(9).toString('base64url');
  const r = createUser({ username: process.env.FO_ADMIN_USER || 'admin', password: seedPwd,
    enterpriseName: '示例企业', industry: '', kb: 'ent_admin', onboarded: false, role: 'admin' });
  if (r && r.ok) {
    if (!process.env.FO_ADMIN_PASSWORD) {
      console.log('[首次启动] 已生成管理员账号 %s，初始口令（仅本次打印，请登录后立即修改）：%s',
        process.env.FO_ADMIN_USER || 'admin', seedPwd);
    }
  }
}
