<!-- BuildGuide: 落地流程向导条（四步）
     props: step=1 当前步(1|2|3|4), onGo(step) 点击回调
     文案: 1 数据建模 / 2 校验本体 / 3 知识问答 / 4 数据看板
     说明: 1 选数据（上传/连库）并生成本体 → 2 看 AI 建议、人拍板、并入词典 → 3 用自然语言问数据 → 4 看指标与趋势 -->
<script>
  export let step = 1;
  export let onGo = () => {};

  const STEPS = ['数据建模', '校验本体', '知识问答', '数据看板'];
  const DESC = [
    '选数据（上传文件 / 连数据库），生成企业专属本体',
    '看 AI 的建模建议并修改，人拍板；新词人确认后才并入词典',
    '用自然语言问数据，答案带证据与出处',
    '看指标、趋势与异常，用于汇报与复盘',
  ];

  // fail-open: 步号异常按第一步处理，不抛错
  $: cur = [1, 2, 3, 4].includes(step) ? step : 1;
</script>

<div class="guide">
  <div class="pills">
    {#each STEPS as name, i}
      {@const n = i + 1}
      <button
        type="button"
        class="pill"
        class:done={n < cur}
        class:active={n === cur}
        aria-current={n === cur ? 'step' : undefined}
        on:click={() => onGo(n)}
      >{n < cur ? ' ' : ''}{n} {name}</button>
    {/each}
  </div>
  <p class="desc">{DESC[cur - 1]}</p>
</div>

<style>
  .guide { display: flex; flex-direction: column; gap: 4px; }
  .pills { display: flex; gap: 6px; flex-wrap: wrap; }
  .pill {
    display: inline-flex;
    align-items: center;
    height: 26px;
    padding: 0 10px;
    font-size: 12px;
    border-radius: 999px;
    white-space: nowrap;
    border: 1px solid var(--line);
    background: #fff;
    color: var(--txt-muted);
    cursor: pointer;
  }
  .pill.done { border-color: var(--line); color: var(--txt-muted); }
  .pill.active {
    background: var(--brand-soft);
    border: 1px solid var(--brand);
    color: var(--brand);
    font-weight: 600;
  }
  .desc { margin: 0; font-size: 12px; color: var(--txt-muted); }
</style>