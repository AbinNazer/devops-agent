/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        jarvis: {
          bg: "#05080a",
          panel: "#0b1114",
          border: "#12232a",
          cyan: "#22d3ee",
          teal: "#2dd4bf",
          text: "#c8f5ff",
          muted: "#5a7a82",
          warn: "#f5c451",
          crit: "#f56565",
          ok: "#34d399",
        },
      },
      boxShadow: {
        glow: "0 0 24px rgba(34, 211, 238, 0.35)",
        "glow-sm": "0 0 12px rgba(34, 211, 238, 0.25)",
      },
      animation: {
        "orb-pulse": "orbPulse 2.4s ease-in-out infinite",
        "orb-ring": "orbRing 6s linear infinite",
        "orb-ring-rev": "orbRingRev 9s linear infinite",
        "fade-in": "fadeIn 0.2s ease-out",
      },
      keyframes: {
        orbPulse: {
          "0%, 100%": { transform: "scale(1)", opacity: "0.9" },
          "50%": { transform: "scale(1.08)", opacity: "1" },
        },
        orbRing: {
          from: { transform: "rotate(0deg)" },
          to: { transform: "rotate(360deg)" },
        },
        orbRingRev: {
          from: { transform: "rotate(360deg)" },
          to: { transform: "rotate(0deg)" },
        },
        fadeIn: {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};