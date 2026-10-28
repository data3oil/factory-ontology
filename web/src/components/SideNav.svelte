<!--
  SideNav — 工厂本体知识问答系统左侧导航。
  props: area(当前一级区) / entry(设置区当前条目) / settingsOpen(设置区是否展开) / onPick(点击回调)。
  onPick payload: {kind:'area', area} 或 {kind:'entry', entry}。
  纯展示组件，数据写死，无网络请求。
-->
<script>
  let {
    area = 'ask',
    entry = 'query',
    settingsOpen = false,
    onPick = () => {},
    entries = null,   // 可见的设置条目 key 列表（后端按角色给）；null/空 = 全部可见
  } = $props();

  // 一级区按落地流程排序：先建本体，再问答，再看板，最后交付。
  // 用文字编号代替 emoji：工厂现场多是投影与投影笔操作，emoji 在小屏与灰度投影下糊成一团。
  const AREAS = [
    { area: 'build', step: '1', label: '建本体' },
    { area: 'ask', step: '2', label: '知识问答' },
    { area: 'dashboard', step: '3', label: '数据看板' },
    { area: 'delivery', step: '4', label: '交付与迁移' },
  ];

  const GROUPS = [
    { title: '建本体与演进', items: [
      { key: 'model', label: '数据建模' },
      { key: 'selfmodel', label: '自助建模' },
      { key: 'evolve', label: '本体演进（人确认）' },
    ] },
    { title: '知识库', items: [
      { key: 'knowledge', label: '知识库管理' },
    ] },
    { title: '模型与质量', items: [
      { key: 'eval', label: '评测基线' },
    ] },
    { title: '系统', items: [
      { key: 'orch', label: '本体编排' },
      { key: 'enterprise', label: '企业与账号' },
    ] },
  ];

  // 角色可见性：entries 是后端按角色给的可见条目；不给或给空 = 全部可见（向后兼容）
  const ALLOWED = $derived(Array.isArray(entries) && entries.length ? new Set(entries) : null);
  const VISIBLE_GROUPS = $derived(
    GROUPS.map((g) => ({ ...g, items: g.items.filter((it) => !ALLOWED || ALLOWED.has(it.key)) })).filter(
      (g) => g.items.length > 0,
    ),
  );

  let open = $state(settingsOpen);
  $effect(() => { open = settingsOpen; });

  function pickArea(a) {
    onPick({ kind: 'area', area: a });
  }
  function pickEntry(k) {
    onPick({ kind: 'entry', entry: k });
  }
</script>

<nav class="sidenav">
  <ul class="list">
    {#each AREAS as it (it.area)}
      <li>
        <button
          type="button"
          class="row"
          class:active={area === it.area}
          onclick={() => pickArea(it.area)}
        >
          <span class="step">{it.step}</span>
          <span class="label">{it.label}</span>
        </button>
      </li>
    {/each}
  </ul>

  <div class="divider"></div>

  <button
    type="button"
    class="row settings-toggle"
    class:active={open}
    onclick={() => (open = !open)}
  >
    <span class="step">·</span>
    <span class="label">设置</span>
  </button>

  {#if open}
    <div class="groups">
      {#each VISIBLE_GROUPS as g (g.title)}
        <div class="group">
          <div class="group-title">{g.title}</div>
          <ul class="list">
            {#each g.items as it (it.key)}
              <li>
                <button
                  type="button"
                  class="row"
                  class:active={entry === it.key}
                  onclick={() => pickEntry(it.key)}
                >
                  <span class="label">{it.label}</span>
                </button>
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </div>
  {/if}
</nav>

<style>
  .sidenav {
    width: 208px;
    box-sizing: border-box;
    padding-top: 8px;
    display: flex;
    flex-direction: column;
    background: var(--bg-card);
    border-right: 1px solid var(--line);
    box-shadow: var(--shadow-card);
    font-size: 13px;
    color: var(--txt);
  }

  .list {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .row {
    width: 100%;
    height: 34px;
    padding: 0 12px;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    gap: 8px;
    border: none;
    border-left: 3px solid transparent;
    background: transparent;
    color: var(--txt-sub);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
    transition: background 120ms ease, color 120ms ease;
  }

  .row:hover {
    background: var(--bg-hover);
  }

  .row.active {
    background: var(--brand-soft);
    border-left-color: var(--brand);
    color: var(--brand);
  }

  .step { display: inline-flex; width: 18px; justify-content: center;
    font-size: 12px; color: inherit; opacity: .65; }
  .icon {
    width: 16px;
    text-align: center;
    flex: none;
  }

  .label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .divider {
    height: 1px;
    margin: 8px 12px;
    background: var(--line);
  }

  .settings-toggle {
    color: var(--txt-sub);
  }

  .groups {
    display: flex;
    flex-direction: column;
    padding-bottom: 8px;
  }

  .group {
    margin-top: 4px;
  }

  .group-title {
    padding: 6px 12px 2px;
    font-size: 11px;
    color: var(--txt-muted);
    cursor: default;
    user-select: none;
  }
</style>