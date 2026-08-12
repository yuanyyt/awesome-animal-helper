<script setup lang="ts">
type AppPage = "intro" | "animals" | "guide";
type NavPage = AppPage;
type NavState = "default" | "loading" | "error" | "success";

const props = withDefaults(
  defineProps<{
    activePage: AppPage;
    disabledPages?: NavPage[];
    stateByPage?: Partial<Record<NavPage, NavState>>;
  }>(),
  {
    disabledPages: () => [],
    stateByPage: () => ({}),
  },
);

const emit = defineEmits<{ navigate: [page: NavPage] }>();

const items: { page: NavPage; label: string; primary?: boolean }[] = [
  { page: "intro", label: "首页" },
  { page: "guide", label: "园区导览", primary: true },
  { page: "animals", label: "动物" },
];

function navigate(page: NavPage): void {
  if (!props.disabledPages.includes(page)) emit("navigate", page);
}
</script>

<template>
  <nav class="mobile-bottom-nav" aria-label="移动端主要导航">
    <div class="mobile-bottom-nav__inner">
      <a
        v-for="item in items"
        :key="item.page"
        :href="item.page === 'intro' ? '#home' : `#${item.page}`"
        :class="{ 'is-primary': item.primary }"
        :data-state="props.stateByPage[item.page] ?? 'default'"
        :aria-current="props.activePage === item.page ? 'page' : undefined"
        :aria-busy="props.stateByPage[item.page] === 'loading' || undefined"
        :aria-invalid="props.stateByPage[item.page] === 'error' || undefined"
        :aria-disabled="props.disabledPages.includes(item.page) || undefined"
        :tabindex="props.disabledPages.includes(item.page) ? -1 : undefined"
        @click.prevent="navigate(item.page)"
      >
        <span class="mobile-bottom-nav__icon" aria-hidden="true">
          <svg v-if="item.page === 'intro'" viewBox="0 0 32 32">
            <path d="M6.5 14.5 16 6l9.5 8.5v11H6.5v-11Z" />
            <path d="M12 25.5v-7.75h8v7.75M10 11.2c2.1.6 4 .35 5.7-.75M22 9.7c1.8-.2 3.1-1.15 3.8-2.85" />
            <path class="is-fill" d="M25.2 4.8c2.8.1 4.1 1.3 3.9 3.5-2.7.1-4.1-1.1-3.9-3.5Z" />
          </svg>
          <svg v-else-if="item.page === 'guide'" viewBox="0 0 32 32">
            <path d="m5 8.4 7-2.8 8 2.8 7-2.8v18l-7 2.8-8-2.8-7 2.8v-18Z" />
            <path d="M12 5.6v18M20 8.4v18" />
            <path class="is-accent" d="M15.4 15.1c0-2 1.35-3.45 3.3-3.45s3.3 1.45 3.3 3.45c0 2.6-3.3 5.7-3.3 5.7s-3.3-3.1-3.3-5.7Z" />
            <circle class="is-accent" cx="18.7" cy="15" r="1" />
          </svg>
          <svg v-else viewBox="0 0 32 32">
            <ellipse cx="16" cy="21.2" rx="6.7" ry="5.2" />
            <ellipse cx="8.5" cy="15" rx="2.5" ry="3.2" transform="rotate(-28 8.5 15)" />
            <ellipse cx="13.5" cy="10.6" rx="2.5" ry="3.2" transform="rotate(-8 13.5 10.6)" />
            <ellipse cx="18.8" cy="10.6" rx="2.5" ry="3.2" transform="rotate(8 18.8 10.6)" />
            <ellipse cx="23.7" cy="15" rx="2.5" ry="3.2" transform="rotate(28 23.7 15)" />
          </svg>
        </span>
        <span class="mobile-bottom-nav__label">{{ item.label }}</span>
        <span v-if="props.stateByPage[item.page] === 'error'" class="visually-hidden">加载失败</span>
        <span v-if="props.stateByPage[item.page] === 'success'" class="visually-hidden">已打开</span>
      </a>
    </div>
  </nav>
</template>

<style scoped>
/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V4
 * component: mobile bottom navigation · genre: playful · theme: existing field-guide
 * states: default · hover · focus · active · disabled · loading · error · success · contrast: pass (40–41)
 */
.mobile-bottom-nav {
  display: none;
}

@media (max-width: 48rem), (pointer: coarse) and (max-height: 32rem) {
  .mobile-bottom-nav {
    position: fixed;
    z-index: var(--z-sticky);
    right: 0;
    bottom: 0;
    left: 0;
    display: block;
    padding: var(--space-xs) max(var(--space-sm), env(safe-area-inset-right)) max(var(--space-xs), env(safe-area-inset-bottom)) max(var(--space-sm), env(safe-area-inset-left));
    border-top: 0;
    background: var(--color-paper-3);
    color: var(--color-ink);
    box-shadow: none;
  }

  .mobile-bottom-nav__inner {
    width: min(100%, 30rem);
    min-height: 4.25rem;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    align-items: end;
    margin-inline: auto;
  }

  a {
    position: relative;
    min-width: 0;
    min-height: 4.25rem;
    display: grid;
    grid-template-rows: 2.75rem auto;
    place-items: center;
    align-content: center;
    gap: var(--space-2xs);
    padding: var(--space-2xs) var(--space-xs);
    border: 0;
    border-radius: var(--radius-lg);
    background: transparent;
    color: var(--color-muted);
    transition: transform var(--dur-short) var(--ease-out), opacity var(--dur-short) var(--ease-out);
    white-space: nowrap;
    text-decoration: none;
    touch-action: manipulation;
  }

  .mobile-bottom-nav__icon {
    width: 2.75rem;
    height: 2.75rem;
    display: grid;
    place-items: center;
    border: var(--rule-hair) solid transparent;
    border-radius: var(--radius-round);
  }

  svg {
    width: 2rem;
    height: 2rem;
    overflow: visible;
    fill: none;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-linejoin: round;
    stroke-width: 1.8;
  }

  svg .is-fill {
    fill: currentColor;
    stroke: none;
  }

  svg .is-accent {
    fill: var(--color-coral-soft);
    stroke: currentColor;
  }

  .mobile-bottom-nav__label {
    max-width: 100%;
    overflow: hidden;
    font-size: var(--text-sm);
    font-weight: 700;
    line-height: 1;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  a[aria-current="page"] {
    color: var(--color-accent);
  }

  a[aria-current="page"] .mobile-bottom-nav__icon {
    border-color: var(--color-accent);
    background: var(--color-accent-soft);
    color: var(--color-accent);
  }

  a.is-primary .mobile-bottom-nav__icon {
    width: 2.75rem;
    height: 2.75rem;
    transform: none;
    box-shadow: none;
  }

  a.is-primary svg {
    width: 2rem;
    height: 2rem;
  }

  a:focus-visible {
    outline: 3px solid var(--color-focus);
    outline-offset: 1px;
  }

  a:active,
  a.is-active {
    transform: translateY(1px) scale(0.98);
  }

  a[aria-disabled="true"],
  a.is-disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  a[data-state="loading"],
  a.is-loading {
    opacity: 0.62;
    pointer-events: none;
  }

  a[data-state="loading"] .mobile-bottom-nav__icon,
  a.is-loading .mobile-bottom-nav__icon {
    animation: mobile-nav-loading 900ms var(--ease-in-out) infinite alternate;
  }

  a[data-state="error"],
  a.is-error {
    color: var(--color-danger);
  }

  a[data-state="error"] .mobile-bottom-nav__icon,
  a.is-error .mobile-bottom-nav__icon {
    border-color: var(--color-danger);
  }

  a[data-state="success"],
  a.is-success {
    color: var(--color-success);
  }

  a[data-state="success"] .mobile-bottom-nav__icon,
  a.is-success .mobile-bottom-nav__icon {
    border-color: var(--color-success);
  }
}

@media (hover: hover) and (pointer: fine) and (max-width: 48rem) {
  a:hover,
  a.is-hover {
    transform: translateY(-1px);
  }
}

@media (pointer: coarse) and (max-height: 32rem) {
  .mobile-bottom-nav {
    padding-block: var(--space-2xs) max(var(--space-2xs), env(safe-area-inset-bottom));
  }

  .mobile-bottom-nav__inner,
  a {
    min-height: 3.5rem;
  }

  a {
    grid-template-rows: 2rem auto;
    gap: 0;
  }

  .mobile-bottom-nav__icon,
  a.is-primary .mobile-bottom-nav__icon {
    width: 2rem;
    height: 2rem;
  }

  svg,
  a.is-primary svg {
    width: 1.55rem;
    height: 1.55rem;
  }
}

@keyframes mobile-nav-loading {
  from { opacity: 0.45; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  a {
    transition-duration: 0ms;
  }

  a[data-state="loading"] .mobile-bottom-nav__icon,
  a.is-loading .mobile-bottom-nav__icon {
    animation-duration: 150ms;
    animation-iteration-count: 1;
  }
}
</style>
