<!--
  AnswerProvenance — 「这份答案怎么来的」折叠面板（纯展示，无请求/无全局状态）
  props: meta={deep,mode,engines:[],confidence,n,noBasis} | null, evidenceCount, evidenceHtml
  口径: 1) meta 为 null → 只渲染一行提示; 2) mode==='rule' → 标「规则直答」+ 依据说明, 不打分;
        3) 其余 mode → 三条件各 1 分(deep / evidenceCount>=2 / confidence>=0.6) → 高/中/低;
        4) noBasis===true → 顶部橙色提示 + 额外 chip。
  规则直答由本体规则确定性算出、可复算, 本就没有概率置信度, 故不算「低可信」。
-->
<script>
  // 与仓库其它组件一致：Svelte 5 runes 取 props（不用 Svelte 4 的 export let）
  let { meta = null, evidenceCount = 0, evidenceHtml = '' } = $props();

  const isRule = () => meta && meta.mode === 'rule';

  // 三条件打分: 各 1 分。拿不到或非数字的 confidence 不计分。
  function score() {
    const deep = meta && meta.deep === true;
    const ev = Number(evidenceCount) >= 2;
    const conf = Number(meta && meta.confidence);
    const confOk = Number.isFinite(conf) && conf >= 0.6;
    const total = (deep ? 1 : 0) + (ev ? 1 : 0) + (confOk ? 1 : 0);
    const label = total === 3 ? '可信度高' : total === 2 ? '可信度中' : '可信度低';
    return {
      total,
      label,
      items: [
        { ok: deep, text: '深度分析（deep）' },
        { ok: ev, text: '证据 ≥ 2 条' },
        { ok: confOk, text: '置信度 ≥ 0.6' },
      ],
    };
  }

  // chips: 过滤 'none'/null/undefined/空数组
  function chips() {
    const out = [];
    const m = meta || {};
    if (m.mode) out.push({ k: '模式', v: m.mode });
    const engines = Array.isArray(m.engines) ? m.engines.filter((e) => e) : [];
    if (engines.length) out.push({ k: '引擎', v: engines.join(' + ') });
    if (m.confidence != null && m.confidence !== 'none') out.push({ k: '置信', v: m.confidence });
    if (Number(evidenceCount) > 0) out.push({ k: '证据', v: `${evidenceCount} 条` });
    if (m.noBasis === true) out.push({ k: '后端标记', v: '无依据' });
    return out;
  }
</script>

{#if !meta}
  <!-- fail-open: meta 缺失时只给一行提示, 不渲染表格/清单 -->
  <div class="ap-empty">这次问答没有带回溯源信息</div>
{:else}
  <details class="ap">
    <summary class="ap-summary">
      {#if isRule()}
        <span class="ap-rule">规则直答</span>
      {:else}
        <span class="ap-score">{score().label}</span>
      {/if}
      <span class="ap-hint">这份答案怎么来的</span>
    </summary>

    <div class="ap-body">
      {#if meta.noBasis === true}
        <div class="ap-warn">后端标记：这次回答没有依据（no_basis）</div>
      {/if}

      {#if isRule()}
        <p class="ap-note">由本体规则直接算出，可复算；这类答案没有概率置信度是正常的。</p>
      {:else}
        <ul class="ap-cond">
          {#each score().items as it}
            <li class:ok={it.ok}>
              <span class="ap-mark">{it.ok ? '' : ''}</span>
              <span>{it.text}</span>
            </li>
          {/each}
        </ul>
      {/if}

      <div class="ap-chips">
        {#each chips() as c}
          <span class="ap-chip"><b>{c.k}</b>{c.v}</span>
        {/each}
      </div>

      <div class="ap-evidence">
        {#if evidenceHtml}
          {@html evidenceHtml}
        {:else}
          <div class="ap-empty">这次没有带回证据明细</div>
        {/if}
      </div>
    </div>
  </details>
{/if}

<style>
  .ap { border: 1px solid var(--border, #e5e7eb); border-radius: 8px; font-size: 13px; }
  .ap-summary { cursor: pointer; padding: 8px 12px; display: flex; gap: 8px; align-items: center; }
  .ap-rule { color: var(--brand, #2563eb); font-weight: 600; }
  .ap-score { font-weight: 600; }
  .ap-hint { color: var(--muted, #6b7280); }
  .ap-body { padding: 4px 12px 12px; }
  .ap-warn { color: var(--warn, #b45309); font-weight: 600; margin: 6px 0; }
  .ap-note { color: var(--muted, #6b7280); margin: 6px 0; }
  .ap-cond { list-style: none; padding: 0; margin: 6px 0; }
  .ap-cond li { color: var(--muted, #6b7280); }
  .ap-cond li.ok { color: var(--text, #111827); }
  .ap-mark { display: inline-block; width: 1.2em; }
  .ap-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
  .ap-chip { background: var(--chip-bg, #f3f4f6); border-radius: 999px; padding: 2px 10px; }
  .ap-chip b { font-weight: 600; margin-right: 4px; color: var(--muted, #6b7280); }
  .ap-empty { color: var(--muted, #6b7280); }
</style>