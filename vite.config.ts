import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
export default defineConfig({
  plugins: [tailwindcss()],
  input: ["websocket_client.html", "instructor.html"],
  server: {
    proxy: {
      '/ws': {
        target: 'http://0.0.0.0:9000',
        ws: true
      }
    }
  }
});