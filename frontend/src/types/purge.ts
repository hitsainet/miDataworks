// Purge (operator decision 2026-10-20): the permanent delete of archived items. Mirrors
// backend/src/schemas/purge.py.
export type PurgeKind = 'dataset' | 'label_run' | 'generation_run' | 'approval' | 'rule_set';

export interface PurgeItem {
  kind: PurgeKind;
  id: string;
}

export interface PurgeBlocker {
  code: string;
  message: string;
  table: string | null;
  ids: string[];
}

export interface PurgeOutcome {
  kind: PurgeKind;
  id: string;
  name: string | null;
  /** purgeable: a plan that would go through; purged: done; blocked: `code` says why. */
  status: 'purgeable' | 'purged' | 'blocked';
  code: string | null;
  message: string | null;
  blockers: PurgeBlocker[];
  counts: Record<string, number>;
  bytes: number;
  paths: number;
  paths_left: string[];
  tombstone_id: string | null;
  /**
   * A batch item an earlier item of the same batch already removed with it (a run its dataset
   * took): `status` is `purged`, `code` is `removed_with`, and `tombstone_id` is that item's
   * record, which counts this one.
   */
  removed_with?: PurgeParent | null;
}

export interface PurgeParent {
  kind: PurgeKind;
  id: string;
  name: string | null;
}

export interface PurgeBatch {
  items: PurgeOutcome[];
  purged: number;
  blocked: number;
  bytes: number;
}
