<!-- AppTopBar：页面顶部常驻条（企业/知识库标签 + 常驻提问框 + 设置 + 账号菜单）。
     props（均可选）：enterprise 企业名；kb 当前知识库名；busy 提问进行中；
     onAsk(text) 提交提问；onSwitchKb() 切换知识库；onOpenSettings() 打开设置；
     settingsActive 是否在设置区；onLogout() 退出登录；
     userName/roleLabel 账号菜单里显示的"当前是谁"。 -->
<script>
  let {
    enterprise = '',
    kb = '',
    busy = false,
    onAsk = () => {},
    onSwitchKb = () => {},
    onOpenSettings = () => {},
    settingsActive = false,
    onLogout = () => {},
    userName = '',
    roleLabel = '',
  } = $props();

  // 账号菜单：退出登录收进来，避免顶栏出现一个孤零零的"退出"
  let acctOpen = $state(false);
  const avatarText = $derived(
    (String(userName || enterprise || '企').trim()[0] || '企').toUpperCase(),
  );

  let text = $state('');

  function submit() {
    const t = text.trim();
    if (!t || busy) return;
    onAsk(t);
    text = '';
  }

  function onKeydown(e) {
    if (e.key === 'Enter') submit();
  }
</script>

<div class="bar">
  <div class="left">
    {#if enterprise}
      <span class="ent"> {enterprise}</span>
    {/if}
    {#if kb}
      <button class="kb" type="button" onclick={onSwitchKb}>{kb} ▾</button>
    {/if}
  </div>

  <div class="mid">
    <input
      type="text"
      placeholder="问一句，比如：上月订单成本为什么超了"
      bind:value={text}
      onkeydown={onKeydown}
      disabled={busy}
    />
    <button class="ask" type="button" onclick={submit} disabled={busy}>
      {busy ? '提问中…' : '提问'}
    </button>
  </div>

  <div class="right">
    <button
      class="txt-btn"
      class:active={settingsActive}
      type="button"
      onclick={onOpenSettings}
    > 设置</button>
    <div class="acct">
      <button
        class="acct-btn"
        type="button"
        aria-haspopup="menu"
        aria-expanded={acctOpen}
        title={userName ? `当前账号：${userName}` : '账号'}
        onclick={() => (acctOpen = !acctOpen)}
      >
        <span class="acct-avatar">{avatarText}</span>
        <span class="acct-caret">{acctOpen ? '▾' : '▸'}</span>
      </button>
      {#if acctOpen}
        <div class="acct-menu" role="menu">
          <div class="acct-head">
            <div class="acct-name">{userName || '未命名账号'}</div>
            <div class="acct-org">{[enterprise, roleLabel].filter(Boolean).join(' · ') || '—'}</div>
          </div>
          <button class="acct-item" type="button" onclick={() => { acctOpen = false; onLogout(); }}>
            退出登录
          </button>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .acct { position: relative; }
  .acct-btn {
    display: flex; align-items: center; gap: 4px; height: 30px; padding: 0 4px;
    background: none; border: 0; cursor: pointer; border-radius: 15px;
  }
  .acct-btn:hover { background: var(--bg-hover); }
  .acct-avatar {
    width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    background: var(--brand); color: #fff; font-size: 12px; font-weight: 700;
  }
  .acct-caret { font-size: 10px; color: var(--txt-muted); }
  .acct-menu {
    position: absolute; right: 0; top: 34px; width: 190px; z-index: 60;
    background: var(--bg-card); border: 1px solid var(--line); border-radius: 8px;
    box-shadow: 0 8px 24px rgba(15,23,42,.14); padding: 6px;
  }
  .acct-head { padding: 6px 8px 8px; border-bottom: 1px solid var(--line); margin-bottom: 4px; }
  .acct-name { font-size: 12.5px; font-weight: 700; color: var(--txt); }
  .acct-org { font-size: 11px; color: var(--txt-muted); margin-top: 2px; }
  .acct-item {
    width: 100%; text-align: left; padding: 8px; font-size: 12.5px; color: var(--txt);
    background: none; border: 0; border-radius: 6px; cursor: pointer;
  }
  .acct-item:hover { background: var(--bg-hover); color: var(--danger-fg, #b91c1c); }
  .bar {
    height: 52px;
    background: var(--bg-card);
    border-bottom: 1px solid var(--line);
    padding: 0 14px;
    display: flex;
    align-items: center;
    gap: 12px;
    transition: background 120ms;
  }

  .left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 0 0 auto;
  }

  .ent {
    font-size: 13px;
    color: var(--txt);
    white-space: nowrap;
  }

  .kb {
    background: var(--brand-soft);
    color: var(--brand);
    border: none;
    border-radius: var(--radius-sm);
    font-size: 12px;
    padding: 3px 8px;
    cursor: pointer;
    transition: background 120ms;
  }
  .kb:hover {
    background: var(--bg-hover);
  }

  .mid {
    flex: 1 1 auto;
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .mid input {
    flex: 1 1 auto;
    min-width: 0;
    height: 32px;
    padding: 0 10px;
    font-size: 13px;
    color: var(--txt);
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: var(--radius-md);
    outline: none;
    transition: border-color 120ms, box-shadow 120ms;
  }
  .mid input::placeholder {
    color: var(--txt-muted);
  }
  .mid input:focus {
    border-color: var(--brand);
    box-shadow: var(--ring);
  }
  .mid input:disabled {
    background: var(--bg-hover);
    color: var(--txt-sub);
  }

  .ask {
    height: 32px;
    padding: 0 14px;
    font-size: 13px;
    color: var(--brand);
    background: var(--brand-soft);
    border: none;
    border-radius: var(--radius-md);
    cursor: pointer;
    white-space: nowrap;
    transition: background 120ms;
  }
  .ask:hover:not(:disabled) {
    background: var(--bg-hover);
  }
  .ask:disabled {
    color: var(--txt-muted);
    background: var(--bg-hover);
    cursor: default;
  }

  .right {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 0 0 auto;
  }

  .txt-btn {
    height: 30px;
    padding: 0 10px;
    font-size: 13px;
    color: var(--txt-sub);
    background: transparent;
    border: none;
    border-radius: var(--radius-sm);
    cursor: pointer;
    white-space: nowrap;
    transition: background 120ms, color 120ms;
  }
  .txt-btn:hover {
    background: var(--bg-hover);
  }
  .txt-btn.active {
    background: var(--brand-soft);
    color: var(--brand);
  }
</style>