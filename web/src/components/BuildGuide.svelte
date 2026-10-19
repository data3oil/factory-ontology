<!-- BuildGuide: 建本体三步向导条
     props: step=1 当前步(1|2|3), onGo(step) 点击回调
     文案: 1 数据建模 / 2 自助建模 / 3 本体演进
     说明: 1 选数据（上传文件 / 连数据库） 2 看 AI 建议并修改，人拍板 3 改数据后抽新词，人确认才并入词典 -->
<script>
  export let step = 1;
  export let onGo = () => {};

  const STEPS = ['数据建模', '自助建模', '本体演进'];
  const DESC = [
    '选数据（上传文件 / 连数据库）',
    '看 AI 建议并修改，人拍板',
    '改数据后抽新词，人确认才并入词典',
  ];

  // fail-open: 步号异常按第一步处理，不抛错
  $: cur = [1, 2, 3].includes(step) ? step : 1;
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
      >{n < cur ? '✓ ' : ''}{n} {name}</button>
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
    background: var(--bg-card);
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