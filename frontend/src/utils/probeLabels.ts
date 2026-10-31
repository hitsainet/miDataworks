// Origin: miLLM (Onegaishimas/miLLM) admin-ui/src/utils/probeLabels.ts @ 30cc40d
// Mode: adapt (docs/REUSE.md). Kept: the separation a probe was fitted to make, from its training
// view's label mapping, as "<positive> vs <negative>" in the corpus's own strings, and null when
// either side is empty (a half-stated boundary misleads). Changed: one function, no excluded side.
// miLLM's Probe Monitors page and miStudio show the same words, so the picker here does too
// (novice walk 2026-10-19, finding 6: probes were listed by opaque pmr_ ids).

function labelsMappedTo(mapping: Record<string, string>, target: string): string[] {
  return Object.keys(mapping)
    .filter((label) => String(mapping[label]).toLowerCase() === target)
    .sort();
}

/** `"humorous vs not_humorous"`, or null when the mapping does not name both sides. */
export function labelSeparation(mapping: Record<string, string> | null | undefined): string | null {
  const m = mapping ?? {};
  const positive = labelsMappedTo(m, 'positive');
  const negative = labelsMappedTo(m, 'negative');
  if (positive.length === 0 || negative.length === 0) return null;
  return `${positive.join(' or ')} vs ${negative.join(' or ')}`;
}

/** The model's own name without its organisation: "Llama-3.1-8B-Instruct". */
export function shortModel(hfId: string): string {
  return hfId.split('/').pop() || hfId;
}
