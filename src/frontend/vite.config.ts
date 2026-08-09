import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const projectRoot = fileURLToPath(new URL("../..", import.meta.url));

export default defineConfig({
  plugins: [vue()],
  server: {
    fs: { allow: [projectRoot] },
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});

