// A "Help" link: remembers its topic and opens the Help screen (novice walk 2026-10-19, finding 14).
// Beside it, "Manual" opens the topic's page in the public manual (config/help.ts `manual`).
import { CircleHelp } from 'lucide-react';

import { helpTopic } from '@/config/help';
import { navigate } from '@/utils/navigate';

import { ManualLink } from './ManualLink';
import { useHelpTopic } from './helpTopicStore';

export function HelpLink({ topic, label = 'Help' }: { topic: string; label?: string }) {
  const open = useHelpTopic((s) => s.open);
  const manual = helpTopic(topic)?.manual;
  return (
    <span className="inline-flex items-center gap-3">
      <button
        type="button"
        data-testid={`help-link-${topic}`}
        onClick={() => {
          open(topic);
          navigate('help', topic);
        }}
        className="inline-flex items-center gap-1 text-xs text-indigo-700 dark:text-indigo-300 hover:underline rounded-sm focus-visible:ring-2 focus-visible:ring-indigo-500"
      >
        <CircleHelp className="w-3.5 h-3.5" aria-hidden="true" />
        {label}
      </button>
      {manual && <ManualLink page={manual} label="Manual" testId={`manual-link-${topic}`} />}
    </span>
  );
}
