import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// The miDataworks manual. Modelled on miStudio's manual (miStudio/manual/). Published from the public
// mirror hitsainet/miDataworks by .github/workflows/deploy-manual.yml (actions/deploy-pages), never
// from the private repository. Its guide and reference pages are generated from ../docs/ by
// scripts/sync-docs.mjs, which reads ONLY the pages named in publish-allowlist.json.
//
// The application's Help screen links here; its base URL is frontend/src/config/manual.ts
// (MANUAL_BASE_URL), which must agree with url + baseUrl below (frontend test manualLinks.test.ts).
const config: Config = {
  title: 'miDataworks Manual',
  tagline: 'Dataset curation, labeling and publishing for the mi suite',
  favicon: 'img/favicon.svg',

  url: 'https://hitsainet.github.io',
  baseUrl: '/miDataworks/',

  organizationName: 'hitsainet',
  projectName: 'miDataworks',
  trailingSlash: false,

  // A broken link, a broken anchor or a broken Markdown link fails the build, so CI goes red.
  onBrokenLinks: 'throw',
  onBrokenAnchors: 'throw',
  onDuplicateRoutes: 'throw',

  markdown: {
    // The pages are plain Markdown written for GitHub as well as for this site: `.md` is parsed as
    // CommonMark (so `{`, `<` and the like in prose are text, not JSX); `.mdx` would be MDX.
    format: 'detect',
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/hitsainet/miDataworks/tree/main/manual/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'miDataworks Manual',
      logo: {alt: 'miDataworks', src: 'img/favicon.svg'},
      items: [
        {type: 'docSidebar', sidebarId: 'manualSidebar', position: 'left', label: 'Manual'},
        {to: '/first-dataset', label: 'First dataset', position: 'left'},
        {to: '/reference/mcp-contract', label: 'Reference', position: 'left'},
        {href: 'https://github.com/hitsainet/miDataworks', label: 'GitHub', position: 'right'},
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Manual',
          items: [
            {label: 'Getting started', to: '/getting-started'},
            {label: 'Your first dataset', to: '/first-dataset'},
            {label: 'Label runs', to: '/guide/labeling'},
            {label: 'Publishing and export', to: '/guide/publishing'},
          ],
        },
        {
          title: 'Reference',
          items: [
            {label: 'MCP tools and REST contract', to: '/reference/mcp-contract'},
            {label: 'Writing operators', to: '/reference/operators'},
          ],
        },
        {
          title: 'Suite',
          items: [
            {label: 'GitHub', href: 'https://github.com/hitsainet/miDataworks'},
            {label: 'miStudio manual', href: 'https://hitsainet.github.io/miStudio/'},
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Human in the Stream, LLC. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['bash', 'python', 'json', 'yaml'],
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
