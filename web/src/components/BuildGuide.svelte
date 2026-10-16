<!-- BuildGuide.svelte — 三步向导条（纯展示）
     props: step=1 当前步(1|2|3), onGo=(n)=>{} 点击某步回调
     三步口径: 1 数据建模(选数据) → 2 自助建模(AI建议人拍板) → 3 本体演进(抽新词人确认可回退)
     fail-open: 步号异常按第一步处理，不抛错 -->
<script>
  let { step = 1, onGo = () => {} } = $props();

  // fail-open: 步号异常按第一步处理，不抛错
  const cur = [1, 2, 3].includes(step) ? step : 1;

  const steps = [
    { n: 1, name: '数据建模', desc: '选数据（上传文件 / 连数据库）' },
    { n: 2, name: '自助建模', desc: '看 AI 建议并修改，人拍板' },
    { n: 3, name: '本体演进', desc: '改数据后抽新词，人确认才并入词典（可回退）' },
  ];

  const stateOf = (n) => (n < cur ? 'done' : n === cur ? 'current' : 'todo');
</script>

<div class="guide">
  <div class="bar">
    {#each steps as s (s.n)}
      <button
        type="button"
        class="step {stateOf(s.n)}"
        aria-current={s.n === cur ? 'step' : undefined}
        onclick={() => onGo(s.n)}
      >
        <span class="num">{stateOf(s.n) === 'done' ? '✓' : s.n}</span>
        <span class="name">{s.name}</span>
        <span class="desc">{s.desc}</span>
      </button>
    {/each}
  </div>
  <p class="foot">三步都走完，本体才算落地；每一步都要人拍板，系统不会自己改词典</p>
</div>

<style>
  .guide {
    font-size: 14px;
  }
  .bar {
    display: flex;
    gap: 8px;
  }
  .step {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 12px;
    text-align: left;
    border: 1px solid var(--txt-muted);
    border-left: 3px solid transparent;
    border-radius: 6px;
    background: transparent;
    color: var(--txt-muted);
    cursor: pointer;
    font: inherit;
  }
  .step:hover {
    border-color: var(--brand);
  }
  .num {
    font-size: 20px;
    font-weight: 700;
    line-height: 1;
  }
  .name {
    font-weight: 600;
  }
  .desc {
    font-size: 12px;
    opacity: 0.85;
  }
  .done {
    color: var(--brand);
    border-color: var(--brand);
  }
  .current {
    color: var(--brand);
    font-weight: 700;
    background: var(--brand-soft);
    border-color: var(--brand);
    border-left: 3px solid var(--brand);
  }
  .foot {
    margin: 8px 0 0;
    font-size: 12px;
    color: var(--txt-muted);
  }
</style>