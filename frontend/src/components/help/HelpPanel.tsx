// The Help screen and the "Help" link each screen carries (novice walk 2026-10-19, finding 14).
// A link remembers its topic, opens this screen, and the screen scrolls to that topic.
// Each topic is a summary; its "Read more in the manual" link opens the full page of the public
// manual (config/manual.ts), and the screen opens with a link to the manual itself.
import { useEffect } from 'react';

import { PageHead } from '@/components/layout/PageHead';
import { HELP_TOPICS } from '@/config/help';
import type { PanelDef } from '@/config/panels';

import { ManualLink } from './ManualLink';
import { useHelpTopic } from './helpTopicStore';

export function HelpPanel({ panel }: { panel: PanelDef }) {
  const topic = useHelpTopic((s) => s.topic);
  useEffect(() => {
    if (topic) document.getElementById(`help-${topic}`)?.scrollIntoView?.({ block: 'start' });
  }, [topic]);
  return (
    <>
      <PageHead title={panel.title} subtitle={panel.subtitle} />
      <p className="mb-4 text-sm text-slate-700 dark:text-slate-300">
        These are short summaries. The <ManualLink page="" label="miDataworks manual" testId="manual-home" /> explains every step in full.
      </p>
      <nav aria-label="Help topics" className="mb-6 flex flex-wrap gap-2 text-xs">
        {HELP_TOPICS.map((t) => (
          <a key={t.id} href={`#/help/${t.id}`} data-testid={`help-chip-${t.id}`}
            className="rounded-full border border-slate-200 dark:border-slate-700 px-3 py-1 hover:bg-slate-100 dark:hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-indigo-500">
            {t.title}
          </a>
        ))}
      </nav>
      <div className="stack-y-6 max-w-3xl">
        {HELP_TOPICS.map((t) => (
          <section key={t.id} id={`help-${t.id}`} aria-labelledby={`help-${t.id}-title`} data-current={t.id === topic ? 'true' : undefined}
            className={`rounded-xl border p-5 bg-white dark:bg-slate-800 scroll-mt-4 ${t.id === topic ? 'border-indigo-400' : 'border-slate-200 dark:border-slate-700'}`}>
            <h2 id={`help-${t.id}-title`} className="font-semibold mb-2">{t.title}</h2>
            {t.body.map((p, i) => <p key={i} className="text-sm text-slate-700 dark:text-slate-300 mb-2">{p}</p>)}
            {t.steps && (
              <ol className="list-decimal pl-5 text-sm text-slate-700 dark:text-slate-300 stack-y-1">
                {t.steps.map((s, i) => <li key={i}>{s}</li>)}
              </ol>
            )}
            <div className="mt-3">
              <ManualLink page={t.manual} label="Read more in the manual" testId={`help-manual-${t.id}`} />
            </div>
          </section>
        ))}
      </div>
    </>
  );
}
