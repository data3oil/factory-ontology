<!--
  LandingPref — 设置区「登录后先看」一行。
  props:
    value    'ask' | 'dashboard'，默认 'ask'（受控：UI 由 value 驱动）
    onChange (v) => void，选中另一个值时回调，传新值
  纯展示组件：不存状态、不读写 localStorage、不发请求。
-->
<script>
  const OPTIONS = [
    { v: 'ask', label: '知识问答' },
    { v: 'dashboard', label: '数据看板' },
  ];

  let { value = 'ask', onChange = () => {} } = $props();

  function pick(v) {
    if (v !== value) onChange(v);
  }
</script>

<div class="row">
  <span class="label">登录后先看</span>

  <div class="seg" role="radiogroup" aria-label="登录后先看">
    {#each OPTIONS as opt (opt.v)}
      <button
        type="button"
        role="radio"
        aria-checked={value === opt.v}
        class="seg-btn"
        class:active={value === opt.v}
        onclick={() => pick(opt.v)}
      >
        {opt.label}
      </button>
    {/each}
  </div>

  <span class="hint">下次登录生效</span>
</div>

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
  }

  .label {
    color: var(--txt-sub);
  }

  .seg {
    display: flex;
    gap: 6px;
  }

  .seg-btn {
    height: 28px;
    padding: 0 12px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--line);
    background: #fff;
    color: var(--txt-sub);
    font-size: 13px;
    font-weight: 400;
    cursor: pointer;
    transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
  }

  .seg-btn:hover {
    background: var(--bg-hover);
  }

  .seg-btn.active {
    background: var(--brand-soft);
    border-color: var(--brand);
    color: var(--brand);
    font-weight: 600;
  }

  .hint {
    color: var(--txt-muted);
    font-size: 12px;
  }
</style>