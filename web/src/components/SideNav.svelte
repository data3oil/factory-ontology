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
    tab = '',
    settingsOpen = false,
    onPick = () => {},
    entries = null,   // 可见的设置条目 key 列表（后端按角色给）；null/空 = 全部可见
  } = $props();

  const AREAS = [
    { area: 'ask', icon: '💬', label: '问答' },
    { area: 'kb', icon: '📚', label: '知识库' },
    { area: 'dashboard', icon: '📈', label: '看板' },
  ];

  // 管理 + 设置两组（2026-10-19 商业化改版）：业务三入口在上，这里只放管理与运维，
  // 默认折叠；可见性由后端 entries（roles.js）决定，普通成员只会看到「模型与检索」。
  // 「系统」单独成组置底（2026-10-19 老郑要求）：与日常管理/设置分开，避免误点。
  const GROUPS = [
    { title: '管理', items: [
      { key: 'ontology.model', entry: 'ontology', tab: 'model', label: '本体建模' },
      { key: 'ontology.selfmodel', entry: 'ontology', tab: 'selfmodel', label: '自动匹配建模' },
      { key: 'ontology.evolve', entry: 'ontology', tab: 'evolve', label: '本体演进（人确认）' },
      { key: 'ontology.lexasset', entry: 'ontology', tab: 'lexasset', label: '词典' },
      { key: 'eval', label: '评测' },
    ] },
    { title: '设置', items: [
      { key: 'models', label: '模型与检索' },
      { key: 'members', label: '企业与成员' },
    ] },
    { title: '系统', items: [
      { key: 'system', icon: '🖥️', label: '系统' },
    ] },
  ];

  // 角色可见性：entries 是后端按角色给的条目键。
  // 新旧版本键位不一致时（后端还返回 model/selfmodel/orch/enterprise，前端已改成 ontology/models…），
  // 用别名表把旧键映射到新条目，避免整块菜单被滤空；一个都对不上就当版本错配 → 全部可见。
  // 越权仍由服务端端点鉴权兜底，菜单只负责「看得见」。
  const ALIASES = {
    ontology: ['model', 'selfmodel', 'evolve', 'lexasset', 'knowledge'],
    system: ['orch', 'assets', 'enterprise', 'delivery'],
    members: ['enterprise'],
  };
  const ALLOWED = $derived.by(() => {
    if (!Array.isArray(entries) || !entries.length) return null;
    const allowed = new Set();
    for (const g of GROUPS) {
      for (const it of g.items) {
        const key = it.entry || it.key;
        if (entries.includes(key) || (ALIASES[key] || []).some((a) => entries.includes(a))) allowed.add(key);
      }
    }
    return allowed.size ? allowed : null;
  });
  const VISIBLE_GROUPS = $derived(
    GROUPS.map((g) => ({ ...g, items: g.items.filter((it) => !ALLOWED || ALLOWED.has(it.entry || it.key)) })).filter(
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
          <span class="icon">{it.icon}</span>
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
    <span class="icon">⚙️</span>
    <span class="label">管理与设置</span>
  </button>

  {#if open}
    <div class="groups">
      {#each VISIBLE_GROUPS as g, gi (g.title)}
        {#if gi > 0}
          <div class="divider group-divider"></div>
        {/if}
        <div class="group">
          <div class="group-title">{g.title}</div>
          <ul class="list">
            {#each g.items as it (it.key)}
              <li>
                <button
                  type="button"
                  class="row"
                  class:active={it.entry ? (entry === it.entry && (!it.tab || tab === it.tab)) : entry === it.key}
                  onclick={() => onPick({ kind: 'entry', entry: it.entry || it.key, tab: it.tab })}
                >
                  {#if it.icon}<span class="icon">{it.icon}</span>{/if}
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

  .row.child {
    height: 30px;
    padding-left: 30px;
    font-size: 12.5px;
    color: var(--txt-muted);
  }

  .row.child.active {
    color: var(--brand);
    background: transparent;
    border-left-color: transparent;
    font-weight: 600;
  }

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

  .group-divider {
    margin: 6px 12px;
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