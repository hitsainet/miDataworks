// Origin: miStudio (Onegaishimas/miStudio) frontend/src/utils/validators.ts @ c829a2cc
// Mode: adapt (docs/REUSE.md). Kept: a validator returning true or a message to show beside the
// field. Changed: the rule is the ONE rule the backend enforces (schemas/sources.py), tested on
// both sides against docs/schemas/hf-repo-id-cases.json; miStudio's version allowed a '.' in the
// owner part only implicitly and had no length cap, so the two sides could disagree.

export const HF_REPO_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/;
export const HF_REPO_ID_MAX = 96;

export function validateHfRepoId(value: string): true | string {
  const repoId = value.trim();
  if (!repoId) return 'Enter a repository ID, for example owner/name.';
  if (repoId.length > HF_REPO_ID_MAX) return `A repository ID is at most ${HF_REPO_ID_MAX} characters.`;
  if (!HF_REPO_ID_PATTERN.test(repoId)) {
    return "A repository ID looks like owner/name: letters, digits, '-', '_' and '.', each part starting with a letter or digit.";
  }
  return true;
}
