import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.ts"],
  theme: {
    extend: {
      colors: {
        ink: {
          100: "#eef2ef", 200: "#dce2de", 300: "#bcc5c0", 400: "#95a09a", 500: "#6b7770",
          600: "#45504a", 700: "#2c3430", 800: "#1f2522", 900: "#141816", 950: "#0b0d0c",
        },
        pulse: { 300: "#b9ebb0", 400: "#97de8a", 500: "#74c965", 600: "#56a84a" },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: { sm: "2px", DEFAULT: "3px", md: "4px" },
    },
  },
  plugins: [],
} satisfies Config;
