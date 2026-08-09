<template>
  <aside class="guide-chat" aria-labelledby="guide-chat-title">
    <div class="guide-chat__intro">
      <span aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M5 18c1-7 5-11 14-12-1 9-5 13-12 13" />
          <path d="M7 19c3-4 6-7 11-10" />
        </svg>
      </span>
      <div>
        <h3 id="guide-chat-title">问问森林导览员</h3>
        <p>以后可以在这里询问“熊猫馆怎么走”或“哪种动物适合先看”。</p>
      </div>
      <strong>即将开放</strong>
    </div>
    <div class="guide-chat__composer" aria-disabled="true">
      <label class="visually-hidden" for="guide-question">导览问题</label>
      <textarea
        id="guide-question"
        rows="2"
        readonly
        placeholder="输入你的导览问题…"
        aria-describedby="guide-chat-helper"
      ></textarea>
      <button type="button" disabled aria-label="发送问题，功能即将开放">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m5 12 14-7-5 14-2.5-5.5L5 12Z" />
        </svg>
      </button>
    </div>
    <p id="guide-chat-helper" class="guide-chat__helper">导览问答接口预留区 · 当前不会发送任何内容</p>
  </aside>
</template>
