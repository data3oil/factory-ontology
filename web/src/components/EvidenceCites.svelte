<!--
  EvidenceCites — 问答结果里的「原文出处」块（只读展示）。
  数据来自后端 /api/ask 的融合字段 evidence_cites / evidence_verdict（证据层 evidence_kb 产出）。
  没有这两个字段时整块不渲染，不影响老行为。
-->
<script>
  let { cites = null, verdict = '' } = $props();

  const LABEL = {
    SUPPORTED: '依据充分',
    WEAK: '仅间接依据',
    NOT_FOUND: '没有找到',
    NOT_JUDGED: '未判定',
  };

  const clean = (t) => String(t || '')
    .replace(/^\s*#{1,6}\s*/gm, '')
    .replace(/\|/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();
</script>

{#if cites && cites.length}
  <div class="cites-wrap">
    <div class="cites-head">
      原文出处
      {#if verdict}
        <span class="cites-badge {verdict === 'SUPPORTED' ? 'ok' : verdict === 'WEAK' ? 'warn' : 'miss'}">
          {LABEL[verdict] || verdict}
        </span>
      {/if}
      <span class="cites-hint">可直接回原文逐字核对</span>
    </div>
    {#each cites as c}
      <div class="cites-row">
        <div class="cites-line">
          <span class="cites-path">{c.path}:{c.line_from}-{c.line_to}</span>
          {#if c.doc_date}<span class="cites-date">{c.doc_date}</span>{/if}
        </div>
        {#if c.heading}<div class="cites-title">{c.heading}</div>{/if}
        <div class="cites-snip">{clean(c.snippet)}</div>
      </div>
    {/each}
  </div>
{/if}

<style>
  .cites-wrap { margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg-card); padding: 10px 12px; }
  .cites-head { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--txt); font-weight: 600; }
  .cites-badge { font-size: 11.5px; font-weight: 500; padding: 1px 7px; border-radius: 10px; }
  .cites-badge.ok { background: #eefaf4; color: #1a7f5a; border: 1px solid #cfe9dc; }
  .cites-badge.warn { background: #fdf6ee; color: #a8722a; border: 1px solid #ecd9c2; }
  .cites-badge.miss { background: var(--bg-elevated); color: var(--txt-sub); border: 1px solid var(--line); }
  .cites-hint { margin-left: auto; font-size: 11.5px; font-weight: 400; color: var(--txt-sub); }
  .cites-row { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--line); }
  .cites-line { display: flex; align-items: center; gap: 8px; font-size: 12px; }
  .cites-path { color: var(--brand); font-weight: 600; }
  .cites-date { color: var(--txt-sub); border: 1px solid var(--line); border-radius: 4px; padding: 0 5px; }
  .cites-title { margin-top: 3px; font-size: 12px; color: var(--txt); }
  .cites-snip { margin-top: 3px; font-size: 12px; color: var(--txt-sub); line-height: 1.7; }
</style>
