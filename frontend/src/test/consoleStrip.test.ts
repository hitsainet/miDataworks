// @vitest-environment node
// The production build drops console.log/debug/info and keeps warn/error (vite.config.ts,
// `build.rolldownOptions.treeshake.manualPureFunctions`; the Vite 7 `esbuild.pure` option it
// replaced is ignored by Vite 8's Rolldown/Oxc minifier). The build reads the real
// vite.config.ts, so removing the option from it turns this red.
import path from 'node:path';
import { build, type Rolldown } from 'vite';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(import.meta.dirname, '../..');

async function bundle(mode: 'production' | 'development'): Promise<string> {
  const result = await build({
    configFile: path.join(ROOT, 'vite.config.ts'),
    root: ROOT,
    mode,
    logLevel: 'silent',
    build: {
      write: false,
      minify: true,
      sourcemap: false,
      rolldownOptions: { input: path.join(ROOT, 'src/test/fixtures/consoleStrip.ts') },
    },
  });
  const outputs = (Array.isArray(result) ? result : [result]) as Rolldown.RolldownOutput[];
  return outputs
    .flatMap((o) => o.output)
    .map((chunk) => (chunk.type === 'chunk' ? chunk.code : ''))
    .join('\n');
}

describe('production console stripping', () => {
  it('drops console.log, console.debug and console.info and keeps warn and error', async () => {
    const code = await bundle('production');
    expect(code).toContain('MARK_WARN');
    expect(code).toContain('MARK_ERROR');
    expect(code).not.toContain('MARK_LOG');
    expect(code).not.toContain('MARK_DEBUG');
    expect(code).not.toContain('MARK_INFO');
  }, 60_000);

  it('keeps every console call outside production', async () => {
    const code = await bundle('development');
    for (const mark of ['MARK_LOG', 'MARK_DEBUG', 'MARK_INFO', 'MARK_WARN', 'MARK_ERROR']) {
      expect(code).toContain(mark);
    }
  }, 60_000);
});
