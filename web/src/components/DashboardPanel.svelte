<script>
  // DashboardPanel — 数据可视化面板（设备类型分布 / 状态分布 / 产线统计）
  // 纯 CSS 条形图，零图表依赖
  // 单企业收敛：kb 由父级（App.svelte）注入 currentKb（当前登录企业唯一 kb），
  // kb prop 变化时 $effect 自动重新加载（看板跟随当前企业）。
  import { fetchStats, getToken } from '../lib/api.js';

  let { kb = '' } = $props();    // 当前企业唯一 kb（只读 prop，跟随登录企业）

  let stats = $state(null);
  let loading = $state(true);
  let error = $state('');
  let empty = $state(false);   // 未建模空态（stats 为 null 且 empty=true，非报错）

  // 企业上下文：这个设备/实体在别的系统里（获客/atp/HR）是什么状况。
  // 本系统没有"单个设备详情页"，所以做成按 IRI 查——知道 IRI 就能查，不硬造一个详情页。
  let ctxIri = $state('');
  let ctxData = $state(null);
  let ctxErr = $state('');
  let ctxLoading = $state(false);

  async function loadCtx() {
    const iri = ctxIri.trim();
    if (!iri) { ctxData = null; ctxErr = ''; return; }
    ctxLoading = true; ctxErr = ''; ctxData = null;
    try {
      const r = await fetch('/api/context?iri=' + encodeURIComponent(iri),
                            { headers: { 'x-auth-token': getToken() } });
      ctxData = await r.json();
    } catch (e) {
      ctxErr = (e && e.message) || String(e);
    } finally {
      ctxLoading = false;
    }
  }

  // 图表配色
  const TYPE_COLORS = ['#0D9488', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16'];
  const STATUS_COLOR = { running: '#10b981', idle: '#f59e0b', alarm: '#ef4444', maintenance: '#8b5cf6', offline: '#64748b' };

  // 计算最大数用于比例
  const maxType = $derived(stats && stats.device_type_dist?.length ? Math.max(...stats.device_type_dist.map(d => d.count)) : 1);
  const maxStatus = $derived(stats && stats.status_dist?.length ? Math.max(...stats.status_dist.map(d => d.count)) : 1);
  const maxLine = $derived(stats && stats.line_stats?.length ? Math.max(...stats.line_stats.map(d => d.device_count)) : 1);

  // 诊断式：异常汇总 + 关键指标（黄金三角置顶）
  const total = $derived(stats?.total_devices ?? 0);
  // 运行中设备数：与后端 status_dist 真实状态取值对齐，同时匹配中英文运行状态
  // （ontology_stats 的状态是数据原始存储值，可能为 运行中/运行/正常 或 running/run/normal/active）
  const RUNNING_STATUS = ['running','run','normal','working','active','online','运行中','运行','正常','工作中','在线','生产中'];
  // 用"包含"匹配: 数据是"运行中/维护中"这类带后缀词, 精确相等会漏("维护中"≠"维护")
  const hasAny = (val, words) => { const t = String(val || '').toLowerCase(); return t ? words.some(w => t.includes(w)) : false; };
  const running = $derived((stats?.status_dist || []).filter(s => hasAny(s.status, RUNNING_STATUS)).reduce((sum, s) => sum + (s.count || 0), 0));
  // 异常/故障设备数：与后端 ontology_stats FAULT_STATUS 同一口径(含中文状态词)
  const FAULT_STATUS = ['alarm','maintenance','offline','fault','fail','failed','error','报警','维护','离线','故障','停机','异常','检修'];
  const anomalyCount = $derived((stats?.status_dist || []).filter(s => hasAny(s.status, FAULT_STATUS)).reduce((sum, s) => sum + (s.count || 0), 0));
  const faultPct = $derived(Math.round((stats?.fault_rate ?? 0) * 100));
  const anomalyPct = $derived(total ? Math.round(anomalyCount / total * 100) : 0);
  const faultAlert = $derived(faultPct > 5 || anomalyCount > 0);
  const kpis = $derived([
    { label: '故障/异常设备', value: anomalyCount, color: anomalyCount > 0 ? '#ef4444' : '#10b981', alert: anomalyCount > 0 },
    { label: '故障率', value: faultPct + '%', color: faultAlert ? '#ef4444' : '#10b981', alert: faultAlert },
    { label: '设备总数', value: total, color: '#0D9488', alert: false },
    { label: '运行中', value: running, color: '#10b981', alert: false },
  ]);

  async function load() {
    loading = true; error = ''; empty = false;
    try {
      const res = await fetchStats(kb);
      if (res.ok && res.stats) {
        stats = res.stats;
      } else if (res.ok && res.empty) {
        empty = true;   // 尚未建模：友好空态而非报错
      } else {
        error = res.error || '统计加载失败';
      }
    } catch (e) {
      error = '网络错误';   // 仅真网络异常
    } finally {
      loading = false;
    }
  }

  // 初始 + kb prop 变化（切换/重置后）→ 重新加载当前企业统计
  $effect(() => { load(); });
</script>

<div class="dash">
  {#if loading}
    <div class="skel-block">
      <div class="skel-row">
        <div class="skel skel-kpi"></div>
        <div class="skel skel-kpi"></div>
        <div class="skel skel-kpi"></div>
      </div>
      <div class="skel skel-line-lg"></div>
      <div class="skel skel-line-lg"></div>
      <div class="skel skel-line-lg"></div>
    </div>
  {:else if empty}
    <div class="dash-empty dash-nodata">
      <span class="empty-icon"></span>
      <span class="empty-text">尚未建模，请先在「数据建模」页上传数据</span>
    </div>
  {:else if error}
    <div class="dash-empty dash-err">
      {error}
      <button class="dash-retry" onclick={load}>重试</button>
    </div>
  {:else if stats}
    <!-- 异常告警横幅：突出异常而非埋在统计里 -->
    {#if faultAlert}
      <div class="alert-banner">
        <span class="alert-ico"></span>
        <span>检测到 <b>{anomalyCount}</b> 台设备异常/故障，占设备总数 <b>{anomalyPct}%</b>（故障率 {faultPct}%）。</span>
      </div>
    {/if}

    <!-- 诊断关键指标（黄金三角：异常置顶左上） -->
    <div class="kpi-row">
      {#each kpis as k, i}
        <div class="mini-card" class:alert={k.alert} class:lead={i === 0}>
          <div class="mc-accent" style="background:{k.color}"></div>
          <div class="mc-num" style="color:{k.color}">{k.value}</div>
          <div class="mc-label">{k.label}</div>
        </div>
      {/each}
    </div>

    <div class="chart-grid">
      <!-- 设备类型分布 -->
      <div class="chart-card">
        <div class="chart-title">设备类型分布</div>
        <div class="bars">
          {#each stats.device_type_dist as d, i}
            <div class="bar-row">
              <span class="bar-label">{d.type}</span>
              <div class="bar-track">
                <div class="bar-fill" style="width: {Math.round(d.count / maxType * 100)}%; background: {TYPE_COLORS[i % TYPE_COLORS.length]};"></div>
              </div>
              <span class="bar-val">{d.count}</span>
            </div>
          {/each}
        </div>
      </div>

      <!-- 状态分布 -->
      <div class="chart-card">
        <div class="chart-title">设备状态分布</div>
        <div class="bars">
          {#each stats.status_dist as s}
            <div class="bar-row">
              <span class="bar-label">{s.status}</span>
              <div class="bar-track">
                <div class="bar-fill" style="width: {Math.round(s.count / maxStatus * 100)}%; background: {STATUS_COLOR[s.status] || '#94a3b8'};"></div>
              </div>
              <span class="bar-val">{s.count}</span>
            </div>
          {/each}
        </div>
      </div>
    </div>

    <!-- 产线统计（多表 join 结果） -->
    <div class="chart-card line-card">
      <div class="chart-title">产线设备统计（含负责人/区域）</div>
      <table class="line-table">
        <thead>
          <tr><th>产线</th><th>名称</th><th>区域</th><th>负责人</th><th class="col-num">设备数</th><th class="col-num">运行</th><th class="col-num">异常</th><th class="col-num">总功率(kW)</th></tr>
        </thead>
        <tbody>
          {#each stats.line_stats as l}
            <tr>
              <td class="id-col">{l.line}</td>
              <td>{l.name}</td>
              <td>{l.area}</td>
              <td>{l.supervisor}</td>
              <td class="num-col">{l.device_count}</td>
              <td class="num-col" style="color:{l.running > 0 ? 'var(--success)' : 'var(--text-muted)'}">{l.running}</td>
              <td class="num-col" style="color:{l.alarm > 0 ? 'var(--danger)' : 'var(--text-muted)'}">{l.alarm}</td>
              <td class="num-col">{l.total_power_kw}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <div class="ctx-query">
    <div class="ctx-q-head">
      <b>企业上下文</b>
      <span class="ctx-q-sub">输入实体 IRI，查它在别的系统里的记录（获客/atp/HR）</span>
    </div>
    <div class="ctx-q-row">
      <input class="ctx-q-in" bind:value={ctxIri} placeholder="ent:Asset/onto-eq-m001"
             onkeydown={(e) => e.key === 'Enter' && loadCtx()} />
      <button class="ctx-q-btn" onclick={loadCtx} disabled={ctxLoading}>
        {ctxLoading ? '查询中…' : '查询'}
      </button>
    </div>
    {#if ctxErr}
      <div class="ctx-q-err">读取失败：{ctxErr}</div>
    {:else if ctxData}
      {#if ctxData.reason === 'disabled'}
        <div class="ctx-q-sub">企业上下文层未启用</div>
      {:else if ctxData.reason === 'not_registered'}
        <div class="ctx-q-sub">{ctxData.why || '这个实体还没登记到企业上下文层'}</div>
      {:else if ctxData.reason === 'unreachable'}
        <div class="ctx-q-err">基座不可达：{ctxData.why || ''}</div>
      {:else if ctxData.reason === 'bad_request'}
        <div class="ctx-q-sub">请输入实体 IRI</div>
      {:else}
        <div class="ctx-q-meta">
          <span class="ctx-badge" class:ok={ctxData.certified}>{ctxData.certified ? '已认证' : '未认证'}</span>
          <span class="ctx-q-sub">{ctxData.tokens || 0} tokens · {(ctxData.items || []).length} 条</span>
          {#if ctxData.merged_note}<span class="ctx-q-sub">{ctxData.merged_note}</span>{/if}
        </div>
        {#each (ctxData.items || []) as it}
          <div class="ctx-q-item">
            <span class="ctx-q-kind">{it.kind}</span>
            <span class="ctx-q-text">{it.text}</span>
          </div>
        {/each}
        {#if !(ctxData.items || []).length}
          <div class="ctx-q-sub">暂无上下文条目</div>
        {/if}
      {/if}
    {/if}
  </div>
</div>

<style>
  .dash { display: flex; flex-direction: column; gap: 12px; }
  .dash-empty {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 10px; padding: 48px 20px; text-align: center; color: var(--text-muted); font-size: 13px;
  }
  .dash-empty .empty-icon { font-size: 28px; line-height: 1; }
  .dash-empty .empty-text { color: var(--text-muted); }

  /* 骨架屏 */
  @keyframes shimmer { 0% { background-position: -360px 0; } 100% { background-position: 360px 0; } }
  .skel {
    border-radius: 6px;
    background: linear-gradient(90deg, #eef1f5 25%, #f7f9fc 40%, #eef1f5 55%);
    background-size: 720px 100%; animation: shimmer 1.4s infinite linear;
  }
  .skel-block { display: flex; flex-direction: column; gap: 12px; }
  .skel-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .skel-kpi { height: 74px; }
  .skel-line-lg { height: 14px; width: 100%; }
  .dash-nodata { color: var(--text-muted); }   /* 未建模空态：中性灰，非红色 */
  .dash-err { color: var(--danger); }
  .dash-retry {
    margin-left: 10px; background: var(--bg-card); color: var(--brand);
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 4px 12px; font-size: 12px; cursor: pointer;
    transition: background 150ms ease-out;
  }
  .dash-retry:hover { border-color: var(--brand-line); background: var(--bg-hover); }

  /* KPI 迷你卡片 + 告警 */
  .alert-banner {
    display: flex; align-items: center; gap: 8px;
    background: var(--danger-bg); border: 1px solid var(--border); color: var(--danger-fg);
    border-radius: var(--radius-md); padding: 10px 14px; font-size: 13px;
  }
  .alert-ico { font-size: 15px; }
  .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .mini-card {
    position: relative; overflow: hidden;
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md);
    padding: 16px 12px 12px; text-align: center;
    box-shadow: var(--shadow-card);
  }
  .mini-card.alert { border-color: var(--danger); box-shadow: 0 0 0 1px var(--danger); }
  .mini-card.lead { padding: 20px 12px 16px; }
  .mini-card.lead .mc-accent { height: 4px; }
  .mini-card.lead .mc-num { font-size: 30px; }
  .mc-accent { position: absolute; top: 0; left: 0; right: 0; height: 3px; }
  .mc-num { font-size: 24px; font-weight: 700; line-height: 1.1; font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-variant-numeric: tabular-nums; }
  .mc-label { font-size: 11px; color: var(--text-secondary); margin-top: 4px; }

  /* Charts */
  .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .chart-card {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md);
    padding: 14px; box-shadow: var(--shadow-card);
  }
  .chart-title { font-size: 12px; font-weight: 700; color: var(--text-primary); margin-bottom: 12px; letter-spacing: 0.3px; }

  .bars { display: flex; flex-direction: column; gap: 8px; }
  .bar-row { display: flex; align-items: center; gap: 8px; }
  .bar-label { width: 70px; font-size: 12px; color: var(--text-primary); text-align: right; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .bar-track { flex: 1; height: 16px; background: var(--border); border-radius: var(--radius-sm); overflow: hidden; }
  .bar-fill { height: 100%; border-radius: var(--radius-sm); transition: width 150ms ease-out; min-width: 2px; }
  .bar-val { width: 30px; font-size: 12px; font-weight: 600; color: var(--text-secondary); font-family: ui-monospace, 'SF Mono', Consolas, monospace; font-variant-numeric: tabular-nums; }

  /* Line table */
  .line-card { padding: 14px; }
  .line-table { width: 100%; border-collapse: collapse; }
  .line-table thead th {
    position: sticky; top: 0; z-index: 1;
    padding: 8px 10px; font-size: 12px; font-weight: 600; text-align: left;
    color: var(--text-secondary); background: var(--bg-elevated);
    border-bottom: 1px solid var(--border);
  }
  .line-table tbody td {
    padding: 0 10px; font-size: 13px; text-align: left; color: var(--text-primary);
    border-bottom: 1px solid var(--border);
    height: 40px; transition: background 150ms ease-out;
  }
  .line-table tbody tr:last-child td { border-bottom: none; }
  .line-table tbody tr:hover td { background: var(--bg-hover); }
  .id-col { font-family: ui-monospace, 'SF Mono', SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; color: var(--text-secondary); }
  .num-col { text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, 'SF Mono', Consolas, monospace; }
  .col-num { text-align: right; }

  @media (max-width: 720px) {
    .chart-grid { grid-template-columns: 1fr; }
    .kpi-row { grid-template-columns: repeat(2, 1fr); }
    .skel-row { grid-template-columns: 1fr; }
  }
  /* 企业上下文查询：用站点既有变量，不引新色 */
  .ctx-query {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: var(--radius-md); padding: 12px 14px;
  }
  .ctx-q-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
  .ctx-q-sub { color: var(--text-muted); font-size: 12px; }
  .ctx-q-row { display: flex; gap: 8px; }
  .ctx-q-in {
    flex: 1; padding: 6px 10px; border: 1px solid var(--border);
    border-radius: var(--radius-sm); font-size: 12px; background: var(--bg-input, #fff);
  }
  .ctx-q-btn {
    padding: 6px 14px; border: 1px solid var(--border); border-radius: var(--radius-sm);
    background: var(--accent-bg, #e6f6f3); color: var(--accent-fg, #0d9488);
    font-size: 12px; cursor: pointer;
  }
  .ctx-q-btn:disabled { opacity: .6; cursor: default; }
  .ctx-q-err { color: var(--danger-fg); font-size: 12px; margin-top: 6px; }
  .ctx-q-meta { display: flex; align-items: center; gap: 6px; margin-top: 8px; }
  .ctx-badge {
    display: inline-block; padding: 1px 8px; border-radius: 9px; font-size: 11px;
    background: #fdf3e6; color: #b45309;
  }
  .ctx-badge.ok { background: #e6f6f3; color: #0d9488; }
  .ctx-q-item {
    display: flex; gap: 8px; padding: 5px 0; margin-top: 4px;
    border-top: 1px dashed var(--border); font-size: 12px;
  }
  .ctx-q-kind { flex: 0 0 64px; color: #0d9488; font-size: 11px; }
  .ctx-q-text { flex: 1; color: var(--text-primary); word-break: break-all; }
</style>
