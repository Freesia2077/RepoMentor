import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    env: {
      ANTHROPIC_AUTH_TOKEN: "test-token",
      ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic",
      ANTHROPIC_MODEL: "deepseek-v4-flash",
      NODE_ENV: "test",
    },
  },
});
