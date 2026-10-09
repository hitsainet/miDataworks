// The schema-rendered settings form (FR-003.23; FTDD 003 section 5.4). Renders any params_schema in
// the supported subset with no per-operator code. Every keyword the backend's subset lists is
// handled here (RENDERED_KEYWORDS); a keyword outside it is SHOWN as an error, never dropped
// silently. Exported for feature 002's recipe editor and the guided flow (FR-002.41).
import type { FieldError, JsonSchemaProperty, ParamsSchema } from '@/types/operators';

import {
  ArrayEnumField,
  ArrayScalarField,
  BooleanField,
  ConstField,
  EnumSelect,
  type FieldProps,
  NumberField,
  TextField,
} from './fields/Fields';

/** Keywords this renderer understands. src/test/schemaSubset.json (pinned to the backend) must be
 *  a subset of this, or operators.test.tsx fails. */
export const RENDERED_KEYWORDS = new Set([
  // top level
  'type', 'properties', 'required', 'additionalProperties', 'title', 'description', '$schema',
  // common
  'enum', 'const', 'default',
  // numbers (input bounds and the constraint text)
  'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf',
  // strings
  'minLength', 'maxLength', 'pattern',
  // arrays
  'items', 'minItems', 'maxItems', 'uniqueItems',
  // extensions
  'x-unit', 'x-hint', 'x-advanced', 'x-widget',
]);
export const RENDERED_TYPES = new Set(['string', 'integer', 'number', 'boolean', 'array']);

export function unsupportedKeywords(schema: JsonSchemaProperty): string[] {
  const own = Object.keys(schema).filter((k) => !RENDERED_KEYWORDS.has(k));
  const items = schema.items ? Object.keys(schema.items).filter((k) => !RENDERED_KEYWORDS.has(k)).map((k) => `items.${k}`) : [];
  const badType = schema.type !== undefined && !RENDERED_TYPES.has(schema.type) ? [`type ${String(schema.type)}`] : [];
  return [...own, ...items, ...badType];
}

export function fieldFor(schema: JsonSchemaProperty): (props: FieldProps) => JSX.Element {
  if (schema.const !== undefined) return ConstField;
  if (schema.enum) return EnumSelect;
  if (schema.type === 'integer' || schema.type === 'number') return NumberField;
  if (schema.type === 'boolean') return BooleanField;
  if (schema.type === 'array') return schema.items?.enum ? ArrayEnumField : ArrayScalarField;
  return TextField;
}

/** Map a server error's JSON pointer to its field (``/min_len`` → ``min_len``). */
export function errorsByField(errors: FieldError[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of errors) {
    const field = e.pointer.split('/')[1] ?? '';
    out[field] = out[field] ? `${out[field]} ${e.message}` : e.message;
  }
  return out;
}

export interface SchemaFormProps {
  schema: ParamsSchema;
  values: Record<string, unknown>;
  onChange?: (values: Record<string, unknown>) => void;
  errors?: FieldError[];
  readOnly?: boolean;
}

export function SchemaForm({ schema, values, onChange, errors = [], readOnly = false }: SchemaFormProps) {
  const byField = errorsByField(errors);
  const required = new Set(schema.required ?? []);
  const entries = Object.entries(schema.properties ?? {});
  const render = ([name, prop]: [string, JsonSchemaProperty]) => {
    const unsupported = unsupportedKeywords(prop);
    if (unsupported.length) {
      return (
        <p key={name} role="alert" className="text-sm text-red-500" data-testid={`field-unsupported-${name}`}>
          {name}: this form cannot show {unsupported.join(', ')}. The operator's manifest must stay within the supported subset.
        </p>
      );
    }
    const Field = fieldFor(prop);
    return (
      <Field
        key={name}
        name={name}
        schema={prop}
        value={values[name]}
        required={required.has(name)}
        readOnly={readOnly}
        error={byField[name]}
        onChange={(value) => {
          const next = { ...values };
          if (value === undefined) delete next[name];
          else next[name] = value;
          onChange?.(next);
        }}
      />
    );
  };
  const basic = entries.filter(([, p]) => !p['x-advanced']);
  const advanced = entries.filter(([, p]) => p['x-advanced']);
  return (
    <div className="space-y-4" data-testid="schema-form">
      {entries.length === 0 ? <p className="text-sm text-slate-500 dark:text-slate-400">This operator takes no settings.</p> : null}
      {basic.map(render)}
      {advanced.length ? (
        <details className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
          <summary className="cursor-pointer text-sm text-slate-600 dark:text-slate-300 focus-visible:ring-2 focus-visible:ring-indigo-400">Advanced</summary>
          <div className="mt-3 space-y-4">{advanced.map(render)}</div>
        </details>
      ) : null}
      {byField[''] ? (
        <p role="alert" className="text-sm text-red-500">
          {byField['']}
        </p>
      ) : null}
    </div>
  );
}
