// The version the sidebar foot shows. The source of truth is frontend/package.json "version"
// (kept equal to backend/pyproject.toml by version.test.ts); vite.config.ts reads it at build
// time and defines __APP_VERSION__, so no component writes the number by hand. Kept out of
// brand.ts because tailwind.config.ts imports brand.ts outside Vite, where the define is absent.

declare const __APP_VERSION__: string;

export const APP_VERSION: string = __APP_VERSION__;
