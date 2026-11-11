<!--
  EvidencePanel — 证据检索（原文出处）
  与「知识问答」分工：问答给结论，这里给"这句话在哪份文件第几行"，
  每条结果带行区间、判定（依据充分/仅间接/没找到）、文档日期，可直接回原文核对。
  数据来自 /api/evidence/search 与 /api/evidence/status；只读，不改任何数据。
-->
<script>
  import { getToken } from '../lib/api.js';

  const authedFetch = (url, opts = {}) => {
    const h = { ...(opts.headers || {}) };
    const t = getToken();
    if (t) h['Authorization'] = 'Bearer ' + t;
    return fetch(url, { ...opts, headers: h });
  };

  let q = $state('');
  let rows = $state([]);
  let verdict = $state(null);
  let status = $state(null);
  let loading = $state(false);
  let err = $state('');
  let searched = $state(false);

  const BADGE = {
    SUPPORTED: { text: '依据充分', cls: 'ok' },
    WEAK: { text: '仅间接依据', cls: 'warn' },
    NOT_FOUND: { text: '没有找到', cls: 'miss' },
    NOT_JUDGED: { text: '未判定', cls: 'miss' },
  };

  async function loadStatus() {
    try {
      const r = await authedFetch('/api/evidence/status');
      const d = await r.json();
      status = d;
    } catch (e) {
      status = { ok: false, built: false, error: String(e) };
    }
  }

  async function doSearch() {
    const text = q.trim();
    if (!text || loading) return;
    loading = true;
    err = '';
    try {
      const r = await authedFetch(
        '/api/evidence/search?q=' + encodeURIComponent(text) + '&k=8&with_verdict=true',
      );
      const d = await r.json();
      if (!d.ok) {
        err = d.error || '检索失败';
        rows = [];
        verdict = null;
      } else {
        rows = d.results || [];
        verdict = d.verdict || null;
      }
      searched = true;
    } catch (e) {
      err = String(e);
      rows = [];
    } finally {
      loading = false;
    }
  }

  // 原文片段里的 markdown 记号在列表里没有意义，显示时抹平（不改库里的原文）
  const clean = (t) => String(t || '')
    .replace(/^\s*#{1,6}\s*/gm, '')
    .replace(/\|/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim();

  function onKeydown(e) {
    if (e.key === 'Enter') doSearch();
  }

  $effect(() => {
    loadStatus();
  });
</script>

<section class="ev-wrap">
  <div class="ev-head">
    <div class="ev-title">输入一句现场问法<span class="ev-sub">给原文所在的行区间；判定只给四态，不做生成</span></div>
    {#if status && status.built}
      <div class="ev-meta">
        文档 {status.docs} · 段 {status.segs} · 日期 {status.date_min} 至 {status.date_max} ·
        库 {status.size_mb} MB
      </div>
    {:else}
      <div class="ev-meta miss">证据库未建立，先跑一次建库命令（见 docs/证据层-落地说明）</div>
    {/if}
  </div>

  <div class="ev-bar">
    <input
      type="text"
      class="ev-input"
      placeholder="例如：本体公理有哪几种"
      bind:value={q}
      onkeydown={onKeydown}
      disabled={loading}
    />
    <button class="ev-btn" onclick={doSearch} disabled={loading || !q.trim()}>
      {loading ? '检索中…' : '检 索'}
    </button>
  </div>

  {#if verdict}
    <div class="ev-verdict {BADGE[verdict.state]?.cls || 'miss'}">
      判定：{BADGE[verdict.state]?.text || verdict.state}　{verdict.why || ''}
    </div>
  {/if}

  {#if err}
    <div class="ev-err">{err}</div>
  {/if}

  {#if rows.length}
    <div class="ev-list">
      {#each rows as r, i}
        <div class="ev-item">
          <div class="ev-line">
            <span class="ev-idx">{i + 1}</span>
            <span class="ev-path">{r.path}:{r.line_from}-{r.line_to}</span>
            {#if r.doc_date}<span class="ev-date">{r.doc_date}</span>{/if}
            <span class="ev-score">相关度 {r.score}</span>
          </div>
          {#if r.heading}<div class="ev-heading">{r.heading}</div>{/if}
          <div class="ev-snippet">{clean(r.snippet)}</div>
        </div>
      {/each}
    </div>
  {:else if searched && !loading && !err}
    <div class="ev-empty">前 8 条里没有对得上的段落。库里确实没有就直接说没有，不猜。</div>
  {/if}
</section>

<style>
  .ev-wrap { padding: 14px 18px 24px; }
  .ev-head { border-bottom: 1px solid var(--line); padding-bottom: 8px; margin-bottom: 12px; }
  .ev-title { font-size: 15px; color: var(--txt); font-weight: 600; }
  .ev-sub { font-size: 12px; color: var(--txt-sub); margin-left: 10px; font-weight: 400; }
  .ev-meta { font-size: 12px; color: var(--txt-sub); margin-top: 4px; }
  .ev-meta.miss { color: #a8722a; }
  .ev-bar { display: flex; gap: 8px; align-items: center; }
  .ev-input {
    flex: 1; height: 34px; padding: 0 10px; font-size: 13px; color: var(--txt);
    background: var(--bg-card); border: 1px solid var(--line); border-radius: 6px;
  }
  .ev-input:focus { outline: none; border-color: var(--brand); }
  .ev-btn {
    height: 34px; padding: 0 16px; font-size: 13px; cursor: pointer; color: #fff;
    background: var(--brand); border: 1px solid var(--brand); border-radius: 6px;
  }
  .ev-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .ev-verdict { margin-top: 10px; font-size: 12.5px; padding: 6px 10px; border-radius: 6px; }
  .ev-verdict.ok { background: #eefaf4; color: #1a7f5a; border: 1px solid #cfe9dc; }
  .ev-verdict.warn { background: #fdf6ee; color: #a8722a; border: 1px solid #ecd9c2; }
  .ev-verdict.miss { background: var(--bg-elevated); color: var(--txt-sub); border: 1px solid var(--line); }
  .ev-err { margin-top: 10px; font-size: 12.5px; color: #b3453c; }
  .ev-list { margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
  .ev-item { background: var(--bg-card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
  .ev-line { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--txt-sub); }
  .ev-idx { width: 18px; height: 18px; line-height: 18px; text-align: center; border-radius: 50%;
            background: var(--bg-elevated); color: var(--txt-sub); font-size: 11px; }
  .ev-path { color: var(--brand); font-weight: 600; }
  .ev-date { padding: 0 6px; border: 1px solid var(--line); border-radius: 4px; }
  .ev-score { margin-left: auto; }
  .ev-heading { margin-top: 5px; font-size: 12.5px; color: var(--txt); }
  .ev-snippet { margin-top: 4px; font-size: 12.5px; color: var(--txt-sub); line-height: 1.7; }
  .ev-empty { margin-top: 12px; font-size: 12.5px; color: var(--txt-sub); }
</style>
