<!--
  AxiomPanel — 本体公理集与小型推理机（设置区「公理与推理」）。

  这个面板是公理集的唯一编辑入口，也是闭环的自检入口：
    读 / 改 / 存（config/axioms/<kb>.json）→ 跑推理 → 看推导结果与依据 → 直接试问
  试问走的是正式 /api/ask，不另开链路，所以这里看到的就是用户问到的。
-->
<script>
  import { onMount } from 'svelte';
  import { axiomsGet, axiomsSave, axiomsSeed, axiomsReason, askOnce } from '../lib/api.js';

  let { kb = '' } = $props();

  let doc = $state(null);            // 公理文档
  let summary = $state(null);        // 统计/上次推理
  let report = $state(null);         // 本次推理结果
  let roles = $state({});
  let skipped = $state([]);
  let generated = $state(false);
  let busy = $state('');
  let err = $state('');
  let msg = $state('');
  let dirty = $state(false);
  let showJson = $state(false);
  let jsonText = $state('');
  let q = $state('');
  let qa = $state(null);

  const counts = $derived(summary?.counts || {});
  const chains = $derived(doc?.chains || []);
  const rules = $derived(doc?.business_rules || []);
  const funcs = $derived(doc?.functional || []);
  const disjoint = $derived(doc?.disjoint_with || []);

  function touch() { dirty = true; msg = ''; }

  async function load() {
    err = ''; msg = '';
    try {
      const r = await axiomsGet(kb);
      if (!r || r.ok === false) throw new Error(r?.error || r?.message || '读取公理失败');
      doc = r.axioms || {};
      doc.chains = doc.chains || [];
      doc.business_rules = doc.business_rules || [];
      // 兼容两种写法：字符串 / 数组形式的老公理，统一成对象再交给表单
      doc.functional = (doc.functional || []).map((f) =>
        typeof f === 'string' ? { id: 'AX-F-' + f, prop: f } : f);
      doc.disjoint_with = (doc.disjoint_with || []).map((d) =>
        Array.isArray(d) ? { id: 'AX-D-' + d.join('-'), classes: [d[0], d[1]] } : d);
      summary = r.summary || null;
      generated = !!r.generated;
      roles = r.axioms?.seed_report?.roles || {};
      skipped = r.axioms?.seed_report?.skipped || [];
      report = summary?.last_run || null;
      dirty = false;
      jsonText = JSON.stringify(doc, null, 2);
    } catch (e) {
      err = String(e?.message || e);
    }
  }

  onMount(load);

  async function doSave() {
    if (busy) return;
    busy = 'save'; err = ''; msg = '';
    try {
      const payload = showJson ? JSON.parse(jsonText) : doc;
      const r = await axiomsSave(kb, payload);
      if (!r || r.ok === false) throw new Error(r?.error || '保存失败');
      msg = '已保存。公理文件已备份旧版本，推理结果需要重跑一次才更新。';
      dirty = false;
      await load();
    } catch (e) {
      err = String(e?.message || e);
    } finally { busy = ''; }
  }

  async function doSeed() {
    if (busy) return;
    if (dirty && !confirm('当前有未保存的修改，重新生成会覆盖它们。继续？')) return;
    busy = 'seed'; err = ''; msg = '';
    try {
      const r = await axiomsSeed(kb);
      if (!r || r.ok === false) throw new Error(r?.error || '生成失败');
      msg = '已按种子重新生成（旧文件已备份）。路径取自本库数据里真实存在的边。';
      await load();
    } catch (e) { err = String(e?.message || e); } finally { busy = ''; }
  }

  async function doReason() {
    if (busy) return;
    if (dirty) { err = '有未保存的修改，先保存再跑推理。'; return; }
    busy = 'reason'; err = ''; msg = '';
    try {
      const r = await axiomsReason(kb);
      if (!r || r.ok === false) throw new Error(r?.error || '推理失败');
      report = r.report || null;
      summary = r.summary || summary;
      msg = `推理完成：${report?.input ?? 0} 条事实 → 新增 ${report?.inferred ?? 0} 条，${report?.rounds ?? 0} 轮。`;
    } catch (e) { err = String(e?.message || e); } finally { busy = ''; }
  }

  async function doAsk() {
    const text = (q || '').trim();
    if (!text || busy) return;
    busy = 'ask'; err = ''; qa = null;
    try {
      const r = await askOnce(text, kb);
      if (!r || r.ok === false) throw new Error(r?.message || r?.error || '提问失败');
      qa = r;
    } catch (e) { err = String(e?.message || e); } finally { busy = ''; }
  }

  // ── 结构化编辑 ──
  function addChain() {
    chains.push({ id: 'AX-NEW', label: '新属性链', implies: '', path: [{ prop: '', inverse: false }],
      ask: { label: '', terms: [] }, definition: '' });
    doc.chains = chains; touch();
  }
  function delChain(i) { doc.chains.splice(i, 1); touch(); }
  function addStep(c) { c.path.push({ prop: '', inverse: false }); touch(); }
  function delStep(c, i) { c.path.splice(i, 1); touch(); }
  function addRule() {
    rules.push({ id: 'BR-NEW', label: '新业务规则',
      when: { class: '', prop: '', op: 'eq', value: '' },
      then: { prop: '', value: '' }, ask: { label: '', terms: [] } });
    doc.business_rules = rules; touch();
  }
  function delRule(i) { doc.business_rules.splice(i, 1); touch(); }

  function termsText(c) { return (c.ask?.terms || []).join('、'); }
  function setTerms(c, v) {
    c.ask = c.ask || {};
    c.ask.terms = String(v).split(/[、,，\s]+/).filter(Boolean);
    touch();
  }
</script>

<div class="panel">
  <div class="head">
    <span class="title">公理与推理</span>
    <span class="kb">库：{kb || '当前'}</span>
    <span class="chip">链 {counts.chains ?? 0}</span>
    <span class="chip">规则 {counts.business_rules ?? 0}</span>
    <span class="chip">单值 {counts.functional ?? 0}</span>
    <span class="chip">不相交 {counts.disjoint ?? 0}</span>
    <span class="spacer"></span>
    {#if dirty}<span class="warn">未保存</span>{/if}
    <button class="btn" onclick={doSeed} disabled={!!busy}>按种子重新生成</button>
    <button class="btn" onclick={doReason} disabled={!!busy}>跑推理</button>
    <button class="btn primary" onclick={doSave} disabled={!!busy}>保存</button>
  </div>

  <p class="hint">
    公理集存在 config/axioms/{kb}.json。改完保存，问答链路会自动使用新的推导结果（下次提问前重建闭包）。
    属性链的 <code>反向</code> 表示沿箭头逆着走，例如「产品 → 批次」写 producedIn 并勾反向。
  </p>

  {#if generated}
    <p class="note">这份公理集是刚按跨行业种子生成的，路径取自本库数据里真实存在的边。请过一眼再保存。</p>
  {/if}
  {#if err}<p class="err">{err}</p>{/if}
  {#if msg}<p class="ok">{msg}</p>{/if}

  {#if Object.keys(roles).length}
    <div class="roles">
      <span class="lbl">种子认出的角色：</span>
      {#each Object.entries(roles) as [role, list] (role)}
        <span class="tag">{role}：{list.join(' / ')}</span>
      {/each}
    </div>
  {/if}
  {#if skipped.length}
    <div class="skip">
      <span class="lbl">种子跳过：</span>
      {#each skipped as s}<span class="tag dim">{s}</span>{/each}
    </div>
  {/if}

  <div class="switcher">
    <button class="tab" class:on={!showJson} onclick={() => (showJson = false)}>表单</button>
    <button class="tab" class:on={showJson} onclick={() => { jsonText = JSON.stringify(doc, null, 2); showJson = true; }}>JSON 原文</button>
  </div>

  {#if showJson}
    <textarea class="json" bind:value={jsonText} oninput={touch} spellcheck="false"></textarea>
  {:else}
    <!-- 属性链 -->
    <h4>属性链 <button class="mini" onclick={addChain}>+ 新增</button></h4>
    {#if !chains.length}<p class="empty">还没有属性链。点「按种子重新生成」看它能不能从数据里认出来。</p>{/if}
    {#each chains as c, i (c.id + i)}
      <div class="item">
        <div class="row">
          <input class="w80" bind:value={c.id} oninput={touch} placeholder="编号" />
          <input class="grow" bind:value={c.label} oninput={touch} placeholder="名称" />
          <input class="w120" bind:value={c.implies} oninput={touch} placeholder="推出属性(英文)" />
          <button class="mini danger" onclick={() => delChain(i)}>删除</button>
        </div>
        <div class="row">
          <span class="lbl">路径</span>
          {#each c.path as st, si}
            <span class="step">
              <input class="w120" bind:value={st.prop} oninput={touch} placeholder="属性名" />
              <label class="ck"><input type="checkbox" bind:checked={st.inverse} onchange={touch} /> 反向</label>
              {#if si < c.path.length - 1}<span class="arrow">→</span>{/if}
            </span>
          {/each}
          <button class="mini" onclick={() => addStep(c)}>+ 步</button>
          {#if c.path.length > 1}<button class="mini" onclick={() => delStep(c, c.path.length - 1)}>- 步</button>{/if}
        </div>
        <div class="row">
          <span class="lbl">问法</span>
          <input class="grow" value={termsText(c)} oninput={(e) => setTerms(c, e.currentTarget.value)}
                 placeholder="原料、用料、材料（顿号或逗号分隔）" />
        </div>
        {#if c.from_class || c.to_class}
          <div class="meta">{c.from_class} → {c.to_class}</div>
        {/if}
      </div>
    {/each}

    <!-- 业务规则 -->
    <h4>业务规则 <button class="mini" onclick={addRule}>+ 新增</button></h4>
    {#if !rules.length}<p class="empty">还没有业务规则。规则是「条件 + 沿一条关系 + 赋值」，改完记得跑推理看它真的推了什么。</p>{/if}
    {#each rules as r, i (r.id + i)}
      <div class="item">
        <div class="row">
          <input class="w80" bind:value={r.id} oninput={touch} placeholder="编号" />
          <input class="grow" bind:value={r.label} oninput={touch} placeholder="名称" />
          <button class="mini danger" onclick={() => delRule(i)}>删除</button>
        </div>
        <div class="row">
          <span class="lbl">当</span>
          <input class="w140" bind:value={r.when.class} oninput={touch} placeholder="类名" />
          <input class="w120" bind:value={r.when.prop} oninput={touch} placeholder="属性" />
          <select bind:value={r.when.op} onchange={touch}>
            <option value="eq">等于</option>
            <option value="in">属于</option>
            <option value="gt">大于</option>
            <option value="lt">小于</option>
          </select>
          <input class="w120" bind:value={r.when.value} oninput={touch} placeholder="值" />
        </div>
        <div class="row">
          <span class="lbl">则</span>
          <input class="w120" bind:value={r.then.prop} oninput={touch} placeholder="属性" />
          <span class="eq">=</span>
          <input class="w120" bind:value={r.then.value} oninput={touch} placeholder="值" />
          <input class="w120" bind:value={r.then.link} oninput={touch} placeholder="沿关系(可空)" />
          <input class="w140" bind:value={r.then.target_class} oninput={touch} placeholder="目标类(可空)" />
        </div>
        <div class="row">
          <span class="lbl">问法</span>
          <input class="grow" value={termsText(r)} oninput={(e) => setTerms(r, e.currentTarget.value)}
                 placeholder="不合格、待处理" />
        </div>
      </div>
    {/each}

    <!-- 单值 / 不相交 -->
    <div class="two">
      <div>
        <h4>单值属性</h4>
        {#each funcs as f, i (f.prop + i)}
          <div class="row tight">
            <input class="grow" bind:value={f.prop} oninput={touch} />
            <button class="mini danger" onclick={() => { doc.functional.splice(i, 1); touch(); }}>×</button>
          </div>
        {/each}
        <button class="mini" onclick={() => { doc.functional.push({ id: 'AX-F-NEW', prop: '' }); touch(); }}>+ 新增</button>
      </div>
      <div>
        <h4>不相交类</h4>
        {#each disjoint as d, i (d.id + i)}
          <div class="row tight">
            <input class="grow" bind:value={d.classes[0]} oninput={touch} />
            <span class="eq">⊥</span>
            <input class="grow" bind:value={d.classes[1]} oninput={touch} />
            <button class="mini danger" onclick={() => { doc.disjoint_with.splice(i, 1); touch(); }}>×</button>
          </div>
        {/each}
        <button class="mini" onclick={() => { doc.disjoint_with.push({ id: 'AX-D-NEW', classes: ['', ''] }); touch(); }}>+ 新增</button>
      </div>
    </div>
  {/if}

  <!-- 推理结果 -->
  {#if report}
    <h4 class="sep">上次推理</h4>
    <div class="stats">
      <span>原始 {report.input} 条</span>
      <span>新增 {report.inferred} 条</span>
      <span>闭合 {report.closed} 条</span>
      <span>{report.rounds} 轮</span>
      {#if report.repaired}<span class="warn">引用断裂修正 {report.repaired}</span>{/if}
      {#if report.functional_conflicts?.length}<span class="warn">单值冲突 {report.functional_conflicts.length}</span>{/if}
      {#if report.disjoint_conflicts?.length}<span class="warn">不相交冲突 {report.disjoint_conflicts.length}</span>{/if}
    </div>
    {#if report.endpoint_mismatch?.length}
      <p class="note">
        有 {report.endpoint_mismatch.length} 处公理端点与实例数据对不上，已跳过不推（宁可不推也不推错）：
        {#each report.endpoint_mismatch as m}
          <span class="tag dim">{m.prop}.{m.kind} 要求 {m.expect}，实际 {m.actual.join('/')}</span>
        {/each}
      </p>
    {/if}
    {#if report.by_axiom && Object.keys(report.by_axiom).length}
      <div class="contrib">
        {#each Object.entries(report.by_axiom) as [k, v] (k)}
          <span class="tag">{k} +{v}</span>
        {/each}
      </div>
    {/if}
    {#if report.rule_conflicts?.length}
      <p class="note">
        规则改写了既有状态（{report.rule_conflicts.length} 条），要不要改由你判断：
        {#each report.rule_conflicts.slice(0, 5) as c}
          <span class="tag dim">{c.individual}：{c.before.join('、')} → {c.after}</span>
        {/each}
      </p>
    {/if}
    {#if report.samples?.length}
      <details class="samples">
        <summary>看推导样例（{report.samples.length} 条，各带依据）</summary>
        {#each report.samples.slice(0, 12) as s}
          <div class="sample">
            <code>{s.triple}</code>
            <div class="why">依据 {s.axiom}：{(s.from || []).join('；')}</div>
          </div>
        {/each}
      </details>
    {/if}
  {/if}

  <!-- 试问：走正式问答接口 -->
  <h4 class="sep">试问一句，看闭环通不通</h4>
  <div class="row">
    <input class="grow" bind:value={q} placeholder="例：P004 用了哪些原料 / 哪些产品暂停发货"
           onkeydown={(e) => { if (e.key === 'Enter') doAsk(); }} />
    <button class="btn primary" onclick={doAsk} disabled={!!busy}>提问</button>
  </div>
  {#if qa}
    <div class="qa">
      <div class="row">
        <span class="chip">mode {qa.mode}</span>
        <span class="chip">引擎 {(qa.engines || []).join('/') || '-'}</span>
        <span class="chip">置信 {qa.confidence || '-'}</span>
      </div>
      <p class="ans">{qa.answer}</p>
      {#if qa.evidence?.length}
        <div class="ev">
          {#each qa.evidence.slice(0, 8) as e}
            <span class="tag">{e.entity} · {e.attr} = {e.value}{e.axiom ? ' 〔' + e.axiom + '〕' : ''}</span>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .panel { background: #f7faf9; color: #1f2d2a; border: 1px solid #d9e6e2;
    border-radius: 8px; padding: 12px 14px; font-size: 14px; line-height: 1.5; }
  .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding-bottom: 8px; border-bottom: 1px solid #e2ece9; }
  .title { font-weight: 600; color: #2f9e8f; font-size: 15px; }
  .kb { color: #6b7c78; font-size: 12px; }
  .spacer { flex: 1; }
  .chip { background: #eef5f3; border: 1px solid #d6e6e1; border-radius: 10px;
    padding: 1px 8px; font-size: 12px; color: #46615c; }
  .btn { border: 1px solid #cfe0dc; background: #fff; color: #1f2d2a; border-radius: 6px;
    padding: 4px 10px; cursor: pointer; font-size: 13px; }
  .btn:hover { border-color: #2f9e8f; }
  .btn.primary { background: #2f9e8f; border-color: #2f9e8f; color: #fff; }
  .btn:disabled { opacity: .55; cursor: default; }
  .mini { border: 1px dashed #cfe0dc; background: #fff; border-radius: 5px;
    padding: 1px 7px; font-size: 12px; cursor: pointer; color: #46615c; }
  .mini.danger { color: #b4432f; border-color: #efd3cc; }
  .hint { color: #6b7c78; font-size: 12px; margin: 8px 0; }
  code { background: #eef5f3; padding: 0 4px; border-radius: 3px; }
  .note { background: #fff8e6; border: 1px solid #f0e0b6; border-radius: 6px;
    padding: 6px 9px; font-size: 12px; margin: 6px 0; }
  .err { background: #fdeceb; border: 1px solid #f3c9c4; color: #9c2b1c;
    border-radius: 6px; padding: 6px 9px; font-size: 13px; }
  .ok { background: #eaf6f2; border: 1px solid #c8e6dd; color: #1c6b5c;
    border-radius: 6px; padding: 6px 9px; font-size: 13px; }
  .warn { color: #b4651b; font-size: 12px; font-weight: 600; }
  .roles, .skip, .contrib, .ev { display: flex; gap: 6px; flex-wrap: wrap;
    align-items: center; margin: 4px 0; }
  .lbl { color: #6b7c78; font-size: 12px; }
  .tag { background: #eef5f3; border: 1px solid #d6e6e1; border-radius: 6px;
    padding: 1px 7px; font-size: 12px; color: #35544e; }
  .tag.dim { background: #f4f6f5; color: #6b7c78; }
  .switcher { display: flex; gap: 6px; margin: 10px 0 6px; }
  .tab { border: 1px solid #cfe0dc; background: #fff; border-radius: 6px 6px 0 0;
    padding: 3px 12px; cursor: pointer; font-size: 13px; color: #46615c; }
  .tab.on { background: #2f9e8f; color: #fff; border-color: #2f9e8f; }
  h4 { margin: 14px 0 6px; font-size: 13px; color: #2f6f64; display: flex;
    gap: 8px; align-items: center; }
  h4.sep { border-top: 1px solid #e2ece9; padding-top: 10px; }
  .item { border: 1px solid #e2ece9; background: #fff; border-radius: 6px;
    padding: 7px 8px; margin-bottom: 6px; }
  .row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 3px 0; }
  .row.tight { margin: 2px 0; }
  .step { display: inline-flex; gap: 6px; align-items: center; }
  .arrow { color: #8aa39d; }
  .ck { font-size: 12px; color: #6b7c78; display: inline-flex; gap: 3px; align-items: center; }
  .meta { color: #8aa39d; font-size: 12px; margin-top: 2px; }
  .empty { color: #8aa39d; font-size: 12px; margin: 2px 0 6px; }
  .eq { color: #8aa39d; }
  input, select { border: 1px solid #d9e6e2; border-radius: 5px; padding: 3px 6px;
    font-size: 13px; color: #1f2d2a; background: #fff; }
  input:focus, select:focus, textarea:focus { outline: 1px solid #2f9e8f; }
  .w80 { width: 80px; } .w120 { width: 120px; } .w140 { width: 140px; }
  .grow { flex: 1; min-width: 140px; }
  .json { width: 100%; min-height: 320px; font-family: Consolas, monospace;
    font-size: 12px; border: 1px solid #d9e6e2; border-radius: 6px; padding: 8px;
    background: #fff; color: #1f2d2a; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .stats { display: flex; gap: 10px; flex-wrap: wrap; font-size: 12px; color: #46615c; }
  .samples { margin-top: 8px; font-size: 12px; }
  .samples summary { cursor: pointer; color: #2f6f64; }
  .sample { border-left: 2px solid #cfe0dc; padding: 3px 8px; margin: 5px 0; }
  .sample code { font-size: 11px; color: #35544e; word-break: break-all; }
  .why { color: #8aa39d; font-size: 11px; margin-top: 2px; }
  .qa { border: 1px solid #e2ece9; background: #fff; border-radius: 6px;
    padding: 8px 10px; margin-top: 8px; }
  .ans { margin: 6px 0; font-size: 14px; }
</style>
