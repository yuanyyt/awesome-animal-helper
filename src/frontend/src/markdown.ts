import DOMPurify from "dompurify";
import { marked } from "marked";

const markdownOptions = { async: false, breaks: true, gfm: true } as const;

export function renderMarkdown(markdown: string): string {
  const html = marked.parse(markdown, markdownOptions) as string;
  return DOMPurify.sanitize(html, {
    FORBID_ATTR: ["style"],
    FORBID_TAGS: ["form", "iframe", "input", "script", "style"],
  });
}

export function markdownToText(markdown: string): string {
  const container = document.createElement("div");
  container.innerHTML = renderMarkdown(markdown);
  return container.textContent?.replace(/\s+/g, " ").trim() ?? "";
}
