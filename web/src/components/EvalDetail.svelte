<!--
  EvalDetail — 评测基线逐题明细（纯展示，不发请求、不存全局状态）
  props: result={kb,questions_n,hits,score}|null, answers=[{q,answer,hit}]|null, elapsed=秒|null
  命中率分档: pct>=80 绿#10b981 良好 / >=60 青#0D9488 中等 / 其余 橙#f59e0b 偏低
-->
<script>
  let { result = null, answers = null, elapsed = null } = $props();

  const MAX_ROWS = 20;
  const MAX_ANSWER = 80;

  // fail-open: 字段缺失/类型异常时按 0 处理，避免整页崩掉
  let pct = $derived.by(() => {
    const n = result?.questions_n ?? 0;
    if (!n) return null;
    return Math.round(((result?.hits ?? 0) / n) * 100);
  });

  let tier = $derived.by(() => {
    if (pct === null) return { color: '#f59e0b', text: '命中偏低，建议补充词典或数据' };
    if (pct >= 80) return { color: '#10b981', text: '命中表现良好' };
    if (pct >= 60) return { color: '#0D9488', text: '命中表现中等' };
    return { color: '#f59e0b', text: '命中偏低，建议补充词典或数据' };
  });

  let rows = $derived(Array.isArray(answers) ? answers : []);
  let shown = $derived(rows.slice(0, MAX_ROWS));
  let missCount = $derived(rows.filter((a) => !a?.hit).length);
  let missPreview = $derived(
    rows
      .filter((a) => !a?.hit)
      .slice(0, 5)
      .map((a) => a?.q ?? '')
      .join('；')
  );

  // fail-open: answer 非字符串时转成空串，摘要不因脏数据抛错
  function brief(text) {
    const s = typeof text === 'string' ? text : '';
    return s.length > MAX_ANSWER ? s.slice(0, MAX_ANSWER) + '…' : s;
  }
</script>

{#if !result && !rows.length}
  <p class="empty">还没有跑过评测。点上面的按钮跑一次，这里会出现逐题明细</p>
{:else}
  {#if result}
    <div class="summary">
      <span class="hit" style:color={tier.color}>
        命中 {result.hits ?? 0} / 共 {result.questions_n ?? 0}
        （命中率 {pct === null ? '—' : pct + '%'}）
      </span>
      <span class="tier" style:color={tier.color}>{tier.text}</span>
      {#if elapsed != null}
        <span class="elapsed">耗时 {elapsed}s</span>
      {/if}
    </div>
  {/if}

  {#if rows.length}
    <table>
      <thead>
        <tr><th>#</th><th>题目</th><th>结果</th><th>答案摘要</th></tr>
      </thead>
      <tbody>
        {#each shown as a, i (i)}
          <tr class:miss={!a?.hit}>
            <td>{i + 1}</td>
            <td>{a?.q ?? ''}</td>
            <td>
              {#if a?.hit}
                <span class="ok"> 命中</span>
              {:else}
                <span class="no"> 未命中</span>
              {/if}
            </td>
            <td>{brief(a?.answer)}</td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if rows.length > MAX_ROWS}
      <p class="more">只显示前 {MAX_ROWS} 条，共 {rows.length} 条</p>
    {/if}

    <p class="agg">命中 {rows.length - missCount} 条 / 共 {rows.length} 条</p>
    {#if missCount}
      <p class="missline">未命中：{missPreview}</p>
    {/if}
  {/if}
{/if}

<style>
  .empty { color: #6b7280; padding: 12px 0; }
  .summary { display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; margin: 8px 0; }
  .hit { font-weight: 600; }
  .tier { font-size: 0.9em; }
  .elapsed { color: #6b7280; font-size: 0.9em; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }
  th { color: #6b7280; font-weight: 500; }
  tr.miss { background: #fff7ed; }
  .ok { color: #10b981; }
  .no { color: #f59e0b; }
  .more, .agg { color: #6b7280; font-size: 0.85em; margin: 6px 0; }
  .missline { color: #b45309; font-size: 0.85em; margin: 4px 0; }
</style>