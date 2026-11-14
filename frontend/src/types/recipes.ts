// Feature 002's recipe wire types (FTDD 002 section 5.3). Mirrors backend/src/schemas/recipes.py.

export interface RecipeStep {
  operator: string;
  version: string;
  params: Record<string, unknown>;
}

export interface RecipeBody {
  format: 'dw.recipe/v1';
  steps: RecipeStep[];
}

export interface StepError {
  code: string;
  message: string;
}

export interface Validation {
  valid: boolean;
  body_errors: StepError[];
  steps: Array<{ index: number; operator: string | null; version: string | null; errors: StepError[] }>;
}

export interface RecipeRevision {
  id: string;
  recipe_id: string;
  revision_number: number;
  recipe_hash: string;
  body: RecipeBody;
  step_labels: string[];
  cloned_from_revision_id: string | null;
  imported: boolean;
  created_by: string;
  created_by_origin: string;
  created_at: string;
  versions_built: number;
}

export interface RecipeSummary {
  id: string;
  name: string;
  description: string | null;
  archived: boolean;
  head_revision_id: string | null;
  head_hash: string | null;
  step_count: number;
  providers: string[];
  revision_count: number;
  versions_built: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface Recipe extends RecipeSummary {
  revisions: RecipeRevision[];
}

export interface ImportResult {
  outcome: 'created' | 'already_present' | 'refused';
  hash: string | null;
  recipe: Recipe | null;
  reasons: StepError[];
  notes: string[];
}

export interface RecipeDraft {
  id: string;
  recipe_id: string | null;
  dataset_id: string | null;
  name: string | null;
  body: Partial<RecipeBody>;
  step_labels: string[];
  inputs: Array<Record<string, unknown>>;
  flow_state: { step?: string; choices?: Record<string, unknown> };
  updated_by: string;
  updated_at: string;
}

export type DraftWrite = Omit<RecipeDraft, 'id' | 'updated_by' | 'updated_at'>;
