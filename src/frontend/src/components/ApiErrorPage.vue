<script setup lang="ts">
import { onMounted, ref } from "vue";

const page = ref<HTMLElement | null>(null);
const contactUrl =
  "mailto:1375417741@qq.com?subject=" +
  encodeURIComponent("红山动物园导览 API 余额不足");

onMounted(() => page.value?.focus());
</script>

<template>
  <main
    ref="page"
    class="api-error-page"
    role="alert"
    aria-labelledby="api-error-title"
    tabindex="-1"
  >
    <header class="api-error-page__header">
      <img
        src="/logo/logo-forest.png"
        alt="南京市红山森林动物园"
        width="1579"
        height="382"
      />
    </header>

    <section class="api-error-page__content">
      <svg class="api-error-page__signal" viewBox="0 0 180 180" aria-hidden="true">
        <path class="api-error-page__trail" d="M25 142c24-46 48-70 78-70 19 0 35 8 52 25" />
        <path class="api-error-page__leaf" d="M102 58c12-26 36-32 54-28-2 23-17 39-44 39" />
        <circle cx="48" cy="128" r="12" />
        <path class="api-error-page__break" d="m82 87 13 13-14 13 14 14" />
      </svg>

      <div class="api-error-page__copy">
        <p>导览服务暂停</p>
        <h1 id="api-error-title">API 余额不足</h1>
        <span>导览服务暂时无法使用，请联系开发者补充 API 余额。</span>
        <a :href="contactUrl">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <path d="m4 7 8 6 8-6" />
          </svg>
          联系开发者
        </a>
        <small>1375417741@qq.com</small>
      </div>
    </section>
  </main>
</template>

<style scoped>
.api-error-page {
  width: 100%;
  min-height: 100dvh;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  padding: max(var(--space-md), env(safe-area-inset-top))
    max(var(--space-md), env(safe-area-inset-right))
    max(var(--space-md), env(safe-area-inset-bottom))
    max(var(--space-md), env(safe-area-inset-left));
  overflow: hidden;
  background: var(--color-paper);
  color: var(--color-ink);
  outline: none;
}

.api-error-page__header img {
  width: min(17rem, 58vw);
  height: auto;
  display: block;
}

.api-error-page__content {
  width: min(100%, 62rem);
  display: grid;
  grid-template-columns: minmax(12rem, 0.75fr) minmax(0, 1.25fr);
  align-items: center;
  gap: clamp(var(--space-lg), 7vw, var(--space-3xl));
  margin: auto;
}

.api-error-page__signal {
  width: 100%;
  max-width: 22rem;
  justify-self: end;
  fill: none;
  stroke: var(--color-accent);
  stroke-linecap: round;
  stroke-linejoin: round;
}

.api-error-page__trail {
  stroke: var(--color-rule-strong);
  stroke-width: 5;
  stroke-dasharray: 10 12;
}

.api-error-page__leaf {
  fill: var(--color-leaf-soft);
  stroke-width: 4;
}

.api-error-page__signal circle {
  fill: var(--color-sun);
  stroke-width: 4;
}

.api-error-page__break {
  stroke: var(--color-map-walk);
  stroke-width: 7;
}

.api-error-page__copy {
  min-width: 0;
  display: grid;
  justify-items: start;
}

.api-error-page__copy p {
  margin: 0 0 var(--space-xs);
  color: var(--color-accent);
  font-size: var(--text-sm);
  font-weight: 800;
}

.api-error-page__copy h1 {
  margin: 0 0 var(--space-md);
  font-family: var(--font-display);
  font-size: clamp(2.75rem, 7vw, 5.75rem);
  font-style: normal;
  font-weight: 700;
  letter-spacing: -0.045em;
  line-height: 0.95;
  overflow-wrap: anywhere;
  text-wrap: balance;
}

.api-error-page__copy > span {
  max-width: 30rem;
  color: var(--color-muted);
  font-size: var(--text-md);
  line-height: 1.65;
  text-wrap: pretty;
}

.api-error-page__copy a {
  min-height: 3rem;
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  margin-top: var(--space-xl);
  padding-inline: var(--space-md);
  border: var(--rule-hair) solid var(--color-accent);
  border-radius: var(--radius-sm);
  background: var(--color-accent);
  color: var(--color-accent-ink);
  font-weight: 800;
  text-decoration: none;
  white-space: nowrap;
}

.api-error-page__copy a svg {
  width: 1.2rem;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.8;
}

.api-error-page__copy a:hover {
  opacity: 0.86;
}

.api-error-page__copy a:active {
  transform: translateY(1px);
}

.api-error-page__copy a:focus-visible {
  outline: 3px solid var(--color-focus);
  outline-offset: 3px;
}

.api-error-page__copy small {
  margin-top: var(--space-xs);
  color: var(--color-muted);
  font-size: var(--text-xs);
}

@media (max-width: 48rem) {
  .api-error-page__header img {
    width: min(11rem, 56vw);
  }

  .api-error-page__content {
    grid-template-columns: minmax(0, 1fr);
    align-content: center;
    gap: var(--space-lg);
  }

  .api-error-page__signal {
    width: min(52vw, 12rem);
    justify-self: center;
  }

  .api-error-page__copy {
    justify-items: center;
    text-align: center;
  }

  .api-error-page__copy h1 {
    font-size: clamp(2.6rem, 13vw, 4rem);
  }

  .api-error-page__copy > span {
    font-size: var(--text-sm);
  }

  .api-error-page__copy a {
    width: min(100%, 20rem);
    justify-content: center;
    margin-top: var(--space-lg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .api-error-page__copy a:active {
    transform: none;
  }
}
</style>
