import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    env: {
      DEEPSEEK_API_KEY: "test-key",
      DEEPSEEK_BASE_URL: "https://api.deepseek.com",
    },
  },
});
