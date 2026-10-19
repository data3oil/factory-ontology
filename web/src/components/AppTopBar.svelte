<!-- AppTopBar：页面顶部常驻条（企业/知识库标签 + 常驻提问框 + 设置/退出）。
     props（均可选）：enterprise 企业名；kb 当前知识库名；busy 提问进行中；
     onAsk(text) 提交提问；onSwitchKb() 切换知识库；onOpenSettings() 打开设置；
     settingsActive 是否在设置区；onLogout() 退出。 -->
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
  } = $props();

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
      <span class="ent">🏢 {enterprise}</span>
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
    >⚙️ 设置</button>
    <button class="txt-btn" type="button" onclick={onLogout}>退出</button>
  </div>
</div>

<style>
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
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    font-size: 12px;
    padding: 3px 8px;
    cursor: pointer;
    transition: background 120ms, border-color 120ms;
  }
  .kb:hover {
    background: var(--brand-soft);
    border-color: var(--brand);
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
    box-shadow: 0 0 0 3px var(--ring);
  }
  .mid input:disabled {
    background: var(--bg-hover);
    color: var(--txt-sub);
  }

  .ask {
    height: 32px;
    padding: 0 14px;
    font-size: 13px;
    font-weight: 600;
    color: #fff;
    background: var(--brand);
    border: none;
    border-radius: var(--radius-md);
    cursor: pointer;
    white-space: nowrap;
    transition: background 120ms;
  }
  .ask:hover:not(:disabled) {
    background: var(--brand-dark);
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