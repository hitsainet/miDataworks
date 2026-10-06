import type { Config } from 'tailwindcss';

import { tailwindColors } from './src/config/brand';

// Tokens per the mockup handoff section 3 (ADR-020). Light mode uses the sibling apps' `dark:`
// variant pattern: unprefixed classes are the light values, `dark:` classes the dark ones.
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: tailwindColors,
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      width: { sidebar: '14rem', 'sidebar-collapsed': '4rem' },
      height: { topbar: '3.5rem' },
    },
  },
  plugins: [],
} satisfies Config;
