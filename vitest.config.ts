import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ['src/**/*.test.ts', 'tests/**/*.test.ts'],
    globals: true,
    env: {
      ANTHROPIC_AUTH_TOKEN: "test-token",
      ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic",
      ANTHROPIC_MODEL: "deepseek-v4-pro",
    },
  },
});
