// Tailwind CSS 4 runs as its own PostCSS plugin and adds vendor prefixes itself (Lightning CSS),
// so autoprefixer is gone (Tailwind v4 upgrade guide).
export default {
  plugins: { '@tailwindcss/postcss': {} },
};
