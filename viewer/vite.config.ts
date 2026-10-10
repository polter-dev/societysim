import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Relative asset paths, so the build works from any static host subpath.
  base: "./",
  test: { include: ["src/**/*.test.ts"] },
});
