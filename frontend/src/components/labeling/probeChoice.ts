// Telling probes apart (novice re-walk 2026-10-19, finding 2): two humor probes read "humorous vs
// not_humorous · Llama-3.1-8B-Instruct, layer 12" and differed only by opaque run ids. Probes that
// detect the same thing on the same model and layer form a group; within a group the one with the
// highest AUROC is "recommended" (no AUROC stated → none is), and the latest import is "newest".
// Each option also states its AUROC and when it was trained (US Eastern), so the choice is made on
// facts, not ids.
import type { ProbeListItem } from '@/types/labeling';
import { formatEt } from '@/utils/format';
import { labelSeparation, shortModel } from '@/utils/probeLabels';

export type ProbeTag = 'recommended' | 'newest';

export function detectsText(p: ProbeListItem): string {
  return labelSeparation(p.label_mapping) ?? p.concept ?? 'what it detects is not stated';
}

function groupKey(p: ProbeListItem): string {
  return `${detectsText(p)}|${p.hf_id}|${p.layer}`;
}

const when = (p: ProbeListItem): number => {
  const t = Date.parse(p.created_at ?? p.trained_at ?? '');
  return Number.isFinite(t) ? t : -Infinity;
};

/** Tags per probe id, only for probes that share their group with another. */
export function probeTags(items: ProbeListItem[]): Record<string, ProbeTag[]> {
  const groups = new Map<string, ProbeListItem[]>();
  items.forEach((p) => groups.set(groupKey(p), [...(groups.get(groupKey(p)) ?? []), p]));
  const tags: Record<string, ProbeTag[]> = {};
  groups.forEach((members) => {
    if (members.length < 2) return;
    const scored = members.filter((p) => typeof p.auroc === 'number');
    if (scored.length) {
      const best = scored.reduce((a, b) => ((b.auroc as number) > (a.auroc as number) || ((b.auroc as number) === (a.auroc as number) && when(b) > when(a)) ? b : a));
      tags[best.probe_id] = [...(tags[best.probe_id] ?? []), 'recommended'];
    }
    const dated = members.filter((p) => when(p) > -Infinity);
    if (dated.length) {
      const newest = dated.reduce((a, b) => (when(b) > when(a) ? b : a));
      tags[newest.probe_id] = [...(tags[newest.probe_id] ?? []), 'newest'];
    }
  });
  return tags;
}

/** How many probes share this probe's group (1 = it is the only one). */
export function groupSize(items: ProbeListItem[], p: ProbeListItem): number {
  return items.filter((q) => groupKey(q) === groupKey(p)).length;
}

/** A probe as a novice reads it: what it detects, the model and layer, then what sets it apart. */
export function probeOptionText(p: ProbeListItem, tags: ProbeTag[] = []): string {
  const fit = p.fits_resident_model === false ? ' (not the loaded model)' : p.fits_resident_model === null ? ' (no model loaded in miLLM)' : '';
  const facts = [
    typeof p.auroc === 'number' ? `AUROC ${p.auroc.toFixed(3)}` : null,
    formatEt(p.trained_at ?? p.created_at) ? `${p.trained_at ? 'trained' : 'imported'} ${formatEt(p.trained_at ?? p.created_at)}` : null,
    ...tags,
  ].filter(Boolean);
  return `${detectsText(p)} · ${shortModel(p.hf_id)}, layer ${p.layer}${fit}${facts.length ? ` · ${facts.join(' · ')}` : ''} · ${p.name}`;
}

/** The chosen probe's facts, one per line, for the line under the picker. */
export function probeFactLines(p: ProbeListItem): string[] {
  return [
    typeof p.auroc === 'number' ? `AUROC ${p.auroc.toFixed(3)}${p.auroc_dataset ? ` on ${p.auroc_dataset}` : ''} (miStudio's evaluation).` : 'miStudio\'s evaluation (AUROC) is not stated.',
    p.train_dataset || p.trained_at
      ? `Trained${p.train_dataset ? ` on ${p.train_dataset}` : ''}${p.trained_at ? ` ${formatEt(p.trained_at)}` : ''}${p.mistudio_run_id ? `, run ${p.mistudio_run_id}` : ''}.`
      : `Training dataset and date are not stated${p.mistudio_run_id ? `; run ${p.mistudio_run_id}` : ''}.`,
    formatEt(p.created_at) ? `Imported into miLLM ${formatEt(p.created_at)}.` : null,
    p.threshold !== null ? `Fires at a score of ${p.threshold.toFixed(2)} or more${typeof p.target_fpr === 'number' ? `, set for ${(p.target_fpr * 100).toFixed(p.target_fpr < 0.01 ? 1 : 0)}% false positives` : ''}.` : null,
  ].filter((l): l is string => Boolean(l));
}
