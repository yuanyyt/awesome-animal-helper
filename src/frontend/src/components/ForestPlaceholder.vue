<script setup lang="ts">
defineProps<{ variant?: number }>();
</script>

<template>
  <svg
    class="forest-placeholder"
    :class="`forest-placeholder--${(variant ?? 0) % 3}`"
    viewBox="0 0 320 220"
    aria-hidden="true"
    focusable="false"
  >
    <path class="forest-placeholder__sun" d="M238 28a29 29 0 1 1 0 58 29 29 0 0 1 0-58Z" />
    <path class="forest-placeholder__hill" d="M0 168c45-49 82-61 114-34 39-55 87-49 143 17 20-13 41-12 63 4v65H0Z" />
    <path class="forest-placeholder__leaf" d="M142 40c-49 18-68 57-45 93 45-5 72-38 67-91-6-4-14-5-22-2Z" />
    <path class="forest-placeholder__stem" d="M91 160c19-55 42-83 69-104" />
    <g class="forest-placeholder__tracks">
      <ellipse cx="215" cy="145" rx="12" ry="16" />
      <ellipse cx="246" cy="156" rx="12" ry="16" />
      <ellipse cx="211" cy="116" rx="7" ry="9" />
      <ellipse cx="230" cy="111" rx="7" ry="9" />
      <ellipse cx="252" cy="122" rx="7" ry="9" />
    </g>
  </svg>
</template>

