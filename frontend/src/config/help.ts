// In-app help (novice walk 2026-10-19, finding 14: no help or manual link anywhere). One topic per
// guided step plus the screens a first journey touches, in plain language. This is the summary a
// first-time user needs on screen; the public manual (config/manual.ts) is the full reference, and
// each topic names the manual page (and section) that explains it in depth.
// Every topic id here is linked from the screen it explains (HelpLink); noviceWalk.test.tsx fails
// when a linked id has no topic, and manualLinks.test.tsx when a topic's manual page or section
// does not exist in the manual's sources.

export interface HelpTopic {
  id: string;
  title: string;
  /** Paragraphs; each is plain text. */
  body: string[];
  /** Short "how to" steps, in order. */
  steps?: string[];
  /** The manual page that explains this in depth, relative to the manual's base URL, with an
   *  optional "#section" (a heading slug on that page). */
  manual: string;
}

export const HELP_TOPICS: HelpTopic[] = [
  {
    id: 'journey',
    manual: 'first-dataset',
    title: 'From a Hugging Face dataset to labeled rows',
    body: [
      'New dataset walks you through seven steps. Your progress is saved as you go, so you can leave and come back. A step shows a green tick only when what it makes exists: a source, a dataset, a built version, a started label run.',
      'A version is an exact, unchangeable copy of your data after the steps you chose. Every row that a step removed is logged with the reason, so you can always ask why a row is missing.',
    ],
    steps: [
      'Import: type the repository ID (for example CreativeLang/ColBERT_Humor_Detection), Preview, then Import.',
      'Goal: name the dataset and say what will train on it.',
      'Curate: add the steps that clean and size the data, for example "Sample rows" to keep 25,000 rows.',
      'Build the version (Save and build, below the step).',
      'Label: choose the version, a labeler and how many rows to label, then start the run.',
    ],
  },
  {
    id: 'import',
    manual: 'guide/import#import-from-hugging-face',
    title: 'Import',
    body: [
      'Type a Hugging Face repository ID and press Preview to see its licence, splits, size and sample rows before anything is downloaded. Import downloads it once; importing the same commit again reuses the copy you already have.',
      'Leave Split and Config empty to take every split of the default configuration. The import is pinned to the commit it read, so a version built from it never changes underneath you.',
    ],
  },
  {
    id: 'goal',
    manual: 'first-dataset#2-goal',
    title: 'Goal',
    body: [
      'The goal says what will train on the dataset: a detector (text and a label), a chat model (sft), preference pairs (dpo, kto) and so on. It decides which columns identify a row. You can change it until the first version is built.',
    ],
  },
  {
    id: 'profile',
    manual: 'guide/curation#leakage-contamination-profile-and-clusters',
    title: 'Profile',
    body: [
      'The profile reads a built version: lengths, duplicates and clusters, each with its scale and sample size. Until the first version is built there is nothing to profile; build one on the Curate step and come back.',
    ],
  },
  {
    id: 'curate',
    manual: 'guide/curation#the-sample-operator',
    title: 'Curate and build',
    body: [
      'Curate is where you choose the steps your recipe runs, in order. Press Add beside a suggested step, or choose any operator with "Add another step". Each step shows its settings as a form; problems are marked beside the step.',
      '"Sample rows" (the sample operator) keeps a random sample of a fixed size or share. The draw is seeded: the same seed always keeps the same rows, and every row it leaves out is logged with its place in the draw.',
      'Build version saves your steps as a recipe and builds the version in the background. When the build finishes, the version is chosen for the Label step automatically.',
    ],
    steps: ['Add "Sample rows" and set Rows to keep to 25000.', 'Name the recipe (a name is suggested).', 'Press Build version and wait for "Built".'],
  },
  {
    id: 'label',
    manual: 'guide/labeling#the-label-step-and-starting-a-run',
    title: 'Label',
    body: [
      'Choose the version to label (the one you just built is chosen for you; older and test versions are folded away), then a labeler: the classifier, the judge, or a probe in miLLM. The probe list names what each probe detects, for example "humorous vs not_humorous", the model it was fitted on, its AUROC and when it was trained. When two probes detect the same thing, one is marked recommended (the highest AUROC) and one newest.',
      '"Rows to label" caps the run. The run labels that many inputs and no more, and a cancelled run resumes inside the same rows. Leave it empty to label every row.',
      'The start button names the number of rows it will label; until the count is checked, it names the most the run can label. When it cannot start, the reason and what to do appear above it, for example "load the model in miLLM", with a link to miLLM\'s Models page. The step checks miLLM again by itself every few seconds and when you return to the tab, and your choices are kept on the draft.',
      'After you start, the run\'s progress shows on this step. When it finishes, the result is compared with any two-valued label column the data already has, for example a humor column, so you can see how far they agree before you rely on the labels.',
    ],
  },
  {
    id: 'assemble',
    manual: 'guide/curation#the-shortcut-audit',
    title: 'Assemble',
    body: ['Assemble shows the splits of the labeled version, the shortcut audit (does any column other than the text predict the label?) and the leakage check between splits.'],
  },
  {
    id: 'export',
    manual: 'guide/publishing',
    title: 'Publish or export',
    body: ['Publish pushes a version to the Hugging Face Hub, or exports files for training. Checks run before anything leaves this machine, and the published files are compared with the built ones afterwards.'],
  },
  {
    id: 'recipes',
    manual: 'guide/versions#recipes',
    title: 'Recipes',
    body: [
      'A recipe is a saved, hashed list of steps. New recipe lets you choose each operator and its version from a list; Check recipe tells you whether every step is valid before you save. Build version on a recipe card builds it from a source or a version you choose by name.',
    ],
  },
  {
    id: 'operators',
    manual: 'reference/operators',
    title: 'Operators',
    body: [
      'Operators are the steps a recipe can run: filters, deduplicators, samplers, labelers and reports. Open one to read what it does, preview it on a sample, and see what it keeps and drops and why. Only allowed operators can run; an operator from a third-party package must be allowed here first.',
    ],
  },
  {
    id: 'operations',
    manual: 'getting-started#jobs-and-the-operations-drawer',
    title: 'Operations',
    body: [
      'Show operations lists running jobs with their progress, and failed jobs with the recorded reason. Failures from the last day are shown first; older ones are folded away and can be dismissed together. Dismissing hides a failure; its record is kept.',
    ],
  },
];

export const HELP_TOPIC_IDS = HELP_TOPICS.map((t) => t.id);

/** The topic with this id, if there is one. */
export function helpTopic(id: string): HelpTopic | undefined {
  return HELP_TOPICS.find((t) => t.id === id);
}
