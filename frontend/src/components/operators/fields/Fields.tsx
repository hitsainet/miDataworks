// One field component per subset type (FR-003.23; FTID 003 section 6). Each shows its title, unit,
// default and hint, and the server's error beside the field.
import type { ReactNode } from 'react';

import type { JsonSchemaProperty } from '@/types/operators';

export interface FieldProps {
  name: string;
  schema: JsonSchemaProperty;
  value: unknown;
  required: boolean;
  readOnly?: boolean;
  error?: string;
  onChange: (value: unknown) => void;
}

const inputClass =
  'w-full rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-900 dark:text-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 disabled:opacity-60';

/** The constraints a field states in words, so a hint never hides a rule (R-03.62). */
export function constraintText(schema: JsonSchemaProperty): string {
  const parts: string[] = [];
  if (schema.minimum !== undefined) parts.push(`at least ${schema.minimum}`);
  if (schema.exclusiveMinimum !== undefined) parts.push(`more than ${schema.exclusiveMinimum}`);
  if (schema.maximum !== undefined) parts.push(`at most ${schema.maximum}`);
  if (schema.exclusiveMaximum !== undefined) parts.push(`less than ${schema.exclusiveMaximum}`);
  if (schema.multipleOf !== undefined) parts.push(`a multiple of ${schema.multipleOf}`);
  if (schema.minLength !== undefined) parts.push(`${schema.minLength}+ characters`);
  if (schema.maxLength !== undefined) parts.push(`up to ${schema.maxLength} characters`);
  if (schema.pattern !== undefined) parts.push(`matching ${schema.pattern}`);
  if (schema.minItems !== undefined) parts.push(`${schema.minItems}+ items`);
  if (schema.maxItems !== undefined) parts.push(`up to ${schema.maxItems} items`);
  if (schema.uniqueItems) parts.push('no repeats');
  return parts.join(', ');
}

export function FieldShell({ name, schema, required, error, children }: Omit<FieldProps, 'value' | 'onChange'> & { children: ReactNode }) {
  const constraints = constraintText(schema);
  const hint = schema['x-hint'] ?? schema.description;
  return (
    <div className="space-y-1" data-testid={`field-${name}`}>
      <label htmlFor={`param-${name}`} className="block text-sm font-medium text-slate-700 dark:text-slate-300">
        {schema.title ?? name}
        {schema['x-unit'] ? <span className="ml-1 text-slate-500 dark:text-slate-400">({schema['x-unit']})</span> : null}
        {required ? <span className="ml-1 text-red-500" aria-label="required">*</span> : null}
      </label>
      {children}
      {hint || constraints || schema.default !== undefined ? (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {hint}
          {constraints ? `${hint ? ' ' : ''}Must be ${constraints}.` : ''}
          {schema.default !== undefined ? ` Default: ${String(schema.default)}.` : ''}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-xs text-red-500" data-testid={`field-error-${name}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

function numberOrRaw(raw: string, integer: boolean): unknown {
  if (raw.trim() === '') return undefined;
  const parsed = integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
  // Keep what the person typed when it is not a number: the server says why (FR-003.7).
  return Number.isNaN(parsed) || String(parsed) !== raw.trim() ? raw : parsed;
}

export function NumberField(props: FieldProps) {
  const { name, schema, value, readOnly, onChange } = props;
  const integer = schema.type === 'integer';
  const slider = schema['x-widget'] === 'slider' && schema.minimum !== undefined && schema.maximum !== undefined;
  return (
    <FieldShell {...props}>
      <div className="flex items-center gap-3">
        <input
          id={`param-${name}`}
          className={inputClass}
          inputMode={integer ? 'numeric' : 'decimal'}
          type="text"
          value={value === undefined || value === null ? '' : String(value)}
          placeholder={schema.default !== undefined ? String(schema.default) : undefined}
          disabled={readOnly}
          onChange={(e) => onChange(numberOrRaw(e.target.value, integer))}
        />
        {slider ? (
          <input
            aria-label={`${schema.title ?? name} slider`}
            type="range"
            min={schema.minimum}
            max={schema.maximum}
            step={schema.multipleOf ?? (integer ? 1 : 'any')}
            value={typeof value === 'number' ? value : Number(schema.default ?? schema.minimum)}
            disabled={readOnly}
            onChange={(e) => onChange(integer ? Number.parseInt(e.target.value, 10) : Number(e.target.value))}
            className="accent-indigo-500"
          />
        ) : null}
      </div>
    </FieldShell>
  );
}

export function TextField(props: FieldProps) {
  const { name, schema, value, readOnly, onChange } = props;
  const common = {
    id: `param-${name}`,
    className: inputClass,
    value: typeof value === 'string' ? value : '',
    placeholder: typeof schema.default === 'string' ? schema.default : undefined,
    disabled: readOnly,
    minLength: schema.minLength,
    maxLength: schema.maxLength,
  };
  return (
    <FieldShell {...props}>
      {schema['x-widget'] === 'textarea' ? (
        <textarea {...common} rows={4} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input {...common} type="text" pattern={schema.pattern} onChange={(e) => onChange(e.target.value)} />
      )}
    </FieldShell>
  );
}

export function BooleanField(props: FieldProps) {
  const { name, value, readOnly, onChange } = props;
  return (
    <FieldShell {...props}>
      <input
        id={`param-${name}`}
        type="checkbox"
        checked={Boolean(value ?? props.schema.default)}
        disabled={readOnly}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-400"
      />
    </FieldShell>
  );
}

export function EnumSelect(props: FieldProps) {
  const { name, schema, value, readOnly, onChange } = props;
  const options = schema.enum ?? [];
  return (
    <FieldShell {...props}>
      <select
        id={`param-${name}`}
        className={inputClass}
        value={value === undefined ? '' : String(value)}
        disabled={readOnly}
        onChange={(e) => {
          const found = options.find((o) => String(o) === e.target.value);
          onChange(found ?? undefined);
        }}
      >
        <option value="">{schema.default !== undefined ? `Default (${String(schema.default)})` : 'Choose…'}</option>
        {options.map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}

export function ConstField(props: FieldProps) {
  return (
    <FieldShell {...props}>
      <div id={`param-${props.name}`} className="font-mono text-sm text-slate-600 dark:text-slate-300">
        {JSON.stringify(props.schema.const)}
      </div>
    </FieldShell>
  );
}

export function ArrayEnumField(props: FieldProps) {
  const { name, schema, value, readOnly, onChange } = props;
  const chosen = Array.isArray(value) ? value : [];
  return (
    <FieldShell {...props}>
      <div id={`param-${name}`} className="flex flex-wrap gap-3" role="group" aria-label={schema.title ?? name}>
        {(schema.items?.enum ?? []).map((option) => (
          <label key={String(option)} className="inline-flex items-center gap-1 text-sm text-slate-700 dark:text-slate-300">
            <input
              type="checkbox"
              className="accent-indigo-500"
              checked={chosen.includes(option)}
              disabled={readOnly}
              onChange={(e) => onChange(e.target.checked ? [...chosen, option] : chosen.filter((c) => c !== option))}
            />
            {String(option)}
          </label>
        ))}
      </div>
    </FieldShell>
  );
}

export function ArrayScalarField(props: FieldProps) {
  const { name, schema, value, readOnly, onChange } = props;
  const numeric = schema.items?.type === 'integer' || schema.items?.type === 'number';
  return (
    <FieldShell {...props}>
      <input
        id={`param-${name}`}
        className={inputClass}
        type="text"
        value={Array.isArray(value) ? value.join(', ') : ''}
        placeholder="Comma-separated values"
        disabled={readOnly}
        onChange={(e) => {
          const parts = e.target.value.split(',').map((p) => p.trim()).filter(Boolean);
          onChange(numeric ? parts.map((p) => (Number.isNaN(Number(p)) ? p : Number(p))) : parts);
        }}
      />
    </FieldShell>
  );
}
