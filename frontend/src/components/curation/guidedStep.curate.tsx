// The guided flow's Curate step (FR-004.44): the recommended operators, each previewed through
// feature 003's operator screen before it is added to the recipe (FR-003.19).
import { Button } from '@/components/common/Button';
import type { GuidedStepPanel, GuidedStepProps } from '@/components/guided/guidedSteps';

export const RECOMMENDED: Array<{ name: string; why: string }> = [
  { name: 'normaliser', why: 'one chat format, nothing stripped' },
  { name: 'chat_json_parser', why: 'a chat stored as JSON text, parsed into messages' },
  { name: 'dedup_exact', why: 'identical rows, kept once' },
  { name: 'dedup_minhash', why: 'near duplicates, with the similarity threshold drawn' },
  { name: 'empty_content', why: 'rows with nothing to read' },
  { name: 'length_band', why: 'rows too short or too long' },
  { name: 'decontaminate', why: 'overlap with a pinned benchmark' },
];

function CurateStep({ onAdvance }: GuidedStepProps) {
  return (
    <div>
      <h2 className="font-semibold mb-1">Curate</h2>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-3">
        Add the cleaning steps you need. Open each on the Operators screen to preview what it keeps and drops, and why.
      </p>
      <ul className="text-sm space-y-1">
        {RECOMMENDED.map((r) => (
          <li key={r.name}>
            <span className="font-mono">{r.name}</span> — {r.why}
          </li>
        ))}
      </ul>
      <div className="flex justify-end mt-4">
        <Button onClick={onAdvance}>Continue to labeling</Button>
      </div>
    </div>
  );
}

const panel: GuidedStepPanel = { step: 'curate', order: 3, Component: CurateStep };
export default panel;
