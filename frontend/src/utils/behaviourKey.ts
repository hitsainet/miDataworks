// The suite behaviour-key slug (feature 015; ENH-005 D25, D37). Equal to the backend's
// BEHAVIOUR_KEY_PATTERN (backend/src/schemas/behaviour.py), pinned by behaviourKey.test.ts.
export const BEHAVIOUR_KEY_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/;

/** '' means "no key" and is allowed; anything else must be a slug. */
export const behaviourKeyOk = (value: string): boolean => value === '' || BEHAVIOUR_KEY_PATTERN.test(value);
