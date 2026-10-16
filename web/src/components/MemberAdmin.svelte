<!--
  MemberAdmin.svelte — 企业设置弹窗内的成员管理（纯展示 + 回调）。
  props: users(成员数组) / me(当前账号) / busy(父层请求中) / error(父层错误文案)
         onAdd({username,password}) / onSetRole(username, role)
  本组件不发请求、不存全局状态；数据与错误全部由父层传入。
-->
<script>
  let {
    users = [],
    me = null,
    busy = false,
    error = '',
    onAdd = () => {},
    onSetRole = () => {},
  } = $props();

  let username = $state('');
  let password = $state('');
  let formError = $state('');

  function submit() {
    if (!username.trim() || !password) {
      formError = '用户名和密码都要填';
      return;
    }
    if (me && username.trim() === me.username) {
      formError = '不能用当前账号用户名';
      return;
    }
    formError = '';
    try {
      onAdd({ username: username.trim(), password });
    } catch (e) {
      // expected-exc: 父层回调抛错不应中断表单，交由父层 error 展示
      formError = '添加失败，请重试';
      return;
    }
    username = '';
    password = '';
  }

  function setRole(name, role) {
    try {
      onSetRole(name, role);
    } catch (e) {
      // expected-exc: 父层回调抛错不应中断渲染，错误由父层 error 展示
    }
  }
</script>

<div class="member-admin">
  <div class="head">
    <h3 class="title">成员</h3>
    <p class="hint">管理员可以管理企业、重置数据；普通成员只能使用问答与知识库</p>
  </div>

  {#if error}
    <p class="error">{error}</p>
  {/if}

  {#if users.length === 0}
    <p class="empty">还没有其他成员，下面可以添加</p>
  {:else}
    <table class="table">
      <thead>
        <tr>
          <th>用户名</th>
          <th>角色</th>
          <th>企业</th>
          <th class="col-act"></th>
        </tr>
      </thead>
      <tbody>
        {#each users as u (u.username)}
          <tr>
            <td class="cell-name">{u.username}</td>
            <td>
              <span class="badge" class:badge-admin={u.role === 'admin'}>
                {u.roleLabel ?? (u.role === 'admin' ? '管理员' : '普通成员')}
              </span>
            </td>
            <td class="cell-ent">{u.enterpriseName ?? ''}</td>
            <td class="col-act">
              {#if me && u.username === me.username}
                <span class="self">当前账号</span>
              {:else if u.role === 'admin'}
                <button class="btn" disabled={busy} onclick={() => setRole(u.username, 'member')}>
                  设为普通成员
                </button>
              {:else}
                <button class="btn" disabled={busy} onclick={() => setRole(u.username, 'admin')}>
                  设为管理员
                </button>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  <form class="add" onsubmit={(e) => { e.preventDefault(); submit(); }}>
    <input
      class="input"
      type="text"
      placeholder="用户名"
      bind:value={username}
      autocomplete="off"
    />
    <input
      class="input"
      type="password"
      placeholder="密码"
      bind:value={password}
      autocomplete="new-password"
    />
    <button class="btn btn-primary" type="submit" disabled={busy}>添加成员</button>
  </form>
  {#if formError}
    <p class="error">{formError}</p>
  {/if}
</div>

<style>
  .member-admin {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .head {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .title {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--txt);
  }
  .hint {
    margin: 0;
    font-size: 12px;
    color: var(--txt-sub);
  }
  .error {
    margin: 0;
    font-size: 12px;
    color: var(--danger, #c0392b);
  }
  .empty {
    margin: 0;
    font-size: 13px;
    color: var(--txt-sub);
  }
  .table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .table th {
    text-align: left;
    font-weight: 500;
    color: var(--txt-sub);
    padding: 6px 8px;
    border-bottom: 1px solid var(--border, var(--bg-hover));
  }
  .table td {
    padding: 6px 8px;
    border-bottom: 1px solid var(--border, var(--bg-hover));
    color: var(--txt);
  }
  .col-act {
    text-align: right;
    white-space: nowrap;
  }
  .cell-name {
    font-weight: 500;
  }
  .cell-ent {
    color: var(--txt-sub);
  }
  .badge {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 10px;
    font-size: 12px;
    background: var(--bg-hover);
    color: var(--txt-sub);
  }
  .badge-admin {
    background: var(--brand-soft);
    color: var(--brand);
  }
  .self {
    font-size: 12px;
    color: var(--txt-sub);
  }
  .btn {
    padding: 4px 10px;
    font-size: 12px;
    border: 1px solid var(--border, var(--bg-hover));
    border-radius: 6px;
    background: var(--bg-hover);
    color: var(--txt);
    cursor: pointer;
  }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .btn-primary {
    background: var(--brand);
    border-color: var(--brand);
    color: #fff;
  }
  .add {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .input {
    flex: 1;
    min-width: 0;
    padding: 6px 8px;
    font-size: 13px;
    border: 1px solid var(--border, var(--bg-hover));
    border-radius: 6px;
    background: var(--bg);
    color: var(--txt);
  }
</style>