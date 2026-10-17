<!-- OrchStatusCard: 本体编排层状态卡片。
     props: state('probing'|'up'|'down') / url / lastProbe / onRefresh / onOpen
     纯展示组件，不发网络请求，状态由父层探测后传入。 -->
<script>
  let {
    state = 'probing',
    url = '',
    lastProbe = '',
    onRefresh = () => {},
    onOpen = () => {},
  } = $props();

  const META = {
    probing: { dot: 'var(--txt-muted)', label: '探测中…', desc: '正在探测本机编排服务…' },
    up: { dot: 'var(--success)', label: '运行中', desc: '编排层已在运行，可打开编排台查看流程。' },
    down: { dot: 'var(--warning)', label: '未部署', desc: '本机 8010 端口没有服务，这一层是可选的能力延展，不影响问答与建模。' },
  };
  const meta = $derived(META[state] ?? META.probing);
</script>

<div class="card">
  <div class="head">
    <span class="title">本体编排</span>
    <span class="status">
      <span class="dot" style="background:{meta.dot}"></span>
      <span class="label">{meta.label}</span>
    </span>
  </div>

  <p class="desc">{meta.desc}</p>

  {#if url}
    <p class="url">{url}</p>
  {/if}

  {#if lastProbe}
    <p class="probe">上次探测：{lastProbe}</p>
  {/if}

  <div class="actions">
    <button class="btn" onclick={onRefresh}>重新探测</button>
    {#if state === 'up'}
      <button class="btn primary" onclick={onOpen}>打开编排台</button>
    {/if}
  </div>
</div>

<style>
  .card {
    background: var(--bg-card);
    border: 1px solid var(--line);
    border-radius: var(--radius-md);
    padding: 16px;
    max-width: 720px;
    box-shadow: var(--shadow-card);
    color: var(--txt);
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .title {
    font-size: 14px;
    font-weight: 600;
  }
  .status {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--txt-sub);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex: none;
  }
  .desc {
    margin: 8px 0 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--txt-sub);
  }
  .url {
    margin: 8px 0 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    color: var(--txt-muted);
    word-break: break-all;
  }
  .probe {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--txt-muted);
  }
  .actions {
    display: flex;
    gap: 8px;
    margin-top: 12px;
  }
  .btn {
    border: 1px solid var(--line);
    background: var(--bg-card);
    color: var(--txt);
    border-radius: var(--radius-sm);
    font-size: 12px;
    padding: 6px 12px;
    cursor: pointer;
    transition: background 120ms ease;
  }
  .btn:hover {
    background: var(--bg-hover);
  }
  .btn.primary {
    background: var(--brand);
    border-color: var(--brand);
    color: #fff;
  }
  .btn.primary:hover {
    background: var(--brand-soft);
    border-color: var(--brand-soft);
  }
</style>