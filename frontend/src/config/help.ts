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
      'The goal says what will train on the dataset: a detector (text and a label), a chat model trained by supervised fine-tuning (SFT), preference pairs for direct preference optimization (DPO) or Kahneman-Tversky optimization (KTO), and so on. It decides which columns identify a row. You can change it until the first version is built.',
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
      'Choose the version to label (the one you just built is chosen for you; older and test versions are folded away), then a labeler: the classifier, the judge, or a probe in miLLM. The probe list names what each probe detects, for example "humorous vs not_humorous", the model it was fitted on, its area under the receiver operating characteristic curve (AUROC: how well it separates the two labels, where 1.0 is perfect and 0.5 is chance) and when it was trained. When two probes detect the same thing, one is marked recommended (the highest AUROC) and one newest.',
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
    id: 'version-detail',
    manual: 'guide/versions#version-detail',
    title: 'Version detail',
    body: [
      'Version detail shows one version: how many rows it has, the rows each step removed and why, where it came from (its inputs, recipe and seed), and what other screens add to it, such as the profile, the shortcut audit, the diversity report and whether it was published.',
      'A version never changes. To change the data, edit the recipe and build a new version; the old one stays as it was, so anything made from it can be made again.',
    ],
  },
  {
    id: 'export',
    manual: 'guide/publishing',
    title: 'Publish or export',
    body: ['Publish pushes a version to the Hugging Face Hub, or exports files for training. Checks run before anything leaves this machine, and the published files are compared with the built ones afterwards.'],
  },
  {
    id: 'calibration',
    manual: 'guide/calibration#calibration-sets',
    title: 'Calibration',
    body: [
      'Calibration checks a model labeler against labels people gave the same rows, before its labels are trusted. A calibration set holds those human labels; computing a record compares the labeler with them and shows how far they agree, with the sample size, and a verdict.',
      'A gate uses the verdict: a labeler whose record does not meet its target is marked so before its labels are used. A change to a target is recorded, and a change asked for by an agent waits for your approval.',
    ],
  },
  {
    id: 'review',
    manual: 'guide/calibration#the-review-screen',
    title: 'Review',
    body: [
      'Review shows rows one at a time so a person can accept, override, flag or reject a label. Each decision is recorded beside the row; the row itself is never edited.',
      'A queue comes from a label run, a calibration set, an audit drawn before a public push, or candidates sent by miForge. Keys: a accept, o override, f flag, r reject, j next row, k previous row.',
    ],
  },
  {
    id: 'generation',
    manual: 'guide/generation',
    title: 'Generation',
    body: [
      'Generation writes synthetic rows where real data is thin, with the generation endpoint set in Settings. Every response is checked, and a judge that grades generated rows must be a different model from the one that wrote them.',
      'Steered pairs ask miLLM to answer the same prompt with and without a steering, to make pairs that differ in one way. The diversity report on Version detail shows whether generated rows narrowed the data.',
    ],
  },
  {
    id: 'detector-sets',
    manual: 'guide/detector-sets',
    title: 'Detector sets',
    body: [
      "A detector set turns versions into the four kinds of data miStudio needs to train and test a probe (a small classifier that reads a model's internal activations), sends them to miStudio, and reads miStudio's results back.",
      'Checks run before anything is sent. Every number names its set and its sample, and nothing is matched by guessing: each result is traced to the identifiers recorded when it was sent.',
    ],
  },
  {
    id: 'rule-signals',
    manual: 'guide/rule-signals',
    title: 'Rule signals',
    body: [
      'A rule set decides facts about a text the same way in every app: whether a pattern matches, whether a value is a well-formed identifier such as a card number, and whether a cue word sits near it. Each rule set carries the cases it must pass, and every validator has cases in a shared contract file.',
      'This screen only reads: agents write rule sets through their tools. Spans are counted in characters. A rule set used by a build is kept for good; archiving only hides it from the list.',
    ],
    steps: [
      'Rule sets: filter by behaviour key and open one to see its signals, outputs, cases and the builds that used it.',
      'Validators: open one to see each reason it can give and the cases behind it.',
      'Preview: type a text and click Preview. The text is not saved or logged.',
    ],
  },
  {
    id: 'code_rules',
    manual: 'guide/code-rules',
    title: 'Code rules',
    body: [
      'A code rule is a short Python function that counts something in a text, for when a rule set cannot say it. It can only report a count: it cannot read files, reach the network or keep anything between calls. It runs in a separate, isolated runner, never inside miDataworks itself.',
      'Agents write code rules. Nothing runs in a rule set preview or in a build until you approve its exact code (an agent may try unapproved code on its own test texts; nothing is stored): the request on the Approvals screen shows the full source, what changed since the previous version, the functions, the parameter schema, every case result and the hash (SHA-256, a fingerprint of the exact text). Any change to the code is a new hash and needs a new approval.',
      'Revoke stops an approved code from running in rule set previews and builds from now on; versions already built with it keep their record. A revoked code comes back only through Approve again on this screen (approving an agent request to save it is refused), which runs its stored cases first: if a case fails, or the runner cannot be reached, nothing changes and the screen says which. Both ask for a reason, and for the Settings personal identification number (PIN) when one is set.',
      "A state that is not known is never shown as fine: \"not recorded\", \"revoked\", \"failing under contract\" and \"runner unavailable\" are shown in amber, red or grey.",
    ],
    steps: [
      'Filter by behaviour key, and tick Show archived code rules to include hidden ones.',
      'Open a code rule to see its source, cases, approval history, the builds that used it and whether its cases pass under the current runner contract.',
      'Revoke or Approve again, with a reason. Archive only hides a code rule from the list.',
    ],
  },
  {
    id: 'settings',
    manual: 'getting-started#settings',
    title: 'Settings',
    body: [
      'Settings holds your name (recorded as who did each action), the Hugging Face token, the endpoint for each role (classifier, judge, generation and embeddings), the agent access rules and the shortcut warning level. Requests from agents are on the Approvals screen.',
      "Environment lists the deployment's configuration. Settings marked as changeable can be changed while miDataworks runs, without a restart that would stop running jobs. Each says when a change takes effect, and \"Use the environment value\" removes a change. Every change is recorded with who made it and why, and a change asked for by an agent waits for your approval.",
      'Each endpoint keeps one API key per server. Pointing an endpoint at another server keeps its key for the old server and never sends it to the new one; pointing it back uses that key again. Stored keys lists them, and Delete removes one.',
      'A personal identification number (PIN) can lock every change on this screen. It is not a login: once it is set, a change asks for the PIN, and this browser tab then stays unlocked for 30 minutes. Changing or removing the PIN needs the current one. A forgotten PIN is reset by whoever runs the deployment (SETTINGS_PIN_BYPASS).',
    ],
  },
  {
    id: 'approvals',
    manual: 'getting-started#agent-requests-and-the-approvals-screen',
    title: 'Approvals',
    body: [
      'An automated agent can ask miDataworks to do something that needs your say first, such as pushing to the Hugging Face Hub, labeling many rows or changing a setting. The request waits on the Approvals screen; the number beside Approvals in the menu says how many are waiting, and a banner at the top of every screen says so too.',
      'Each request names the agent, what it would do and when the request expires. Approve runs it; Reject refuses it. A request nobody decides expires, and the agent has to ask again.',
      'Approving a request to change a setting asks for the Settings personal identification number (PIN) when one is set, as changing the setting yourself would.',
      'Below the waiting requests are the ones already decided or expired. Archive hides a rejected or expired one; nothing is deleted.',
    ],
  },
  {
    id: 'words',
    manual: 'intro#words-used-throughout',
    title: 'Words used here',
    body: [
      "Source: rows imported once from the Hugging Face Hub or from files. Version: an unchangeable copy of a dataset after a recipe's steps. Recipe: a saved list of steps. Operator: one step a recipe can run. Label run: one labeler applied to a version's rows.",
      "Classifier: a model that gives each row a probability for each label. Judge: a model that writes a verdict and its reasons against a rubric. Probe: a small classifier that reads a model's internal activations; miLLM runs probes and miStudio trains them.",
      'Area under the receiver operating characteristic curve (AUROC): how well a score separates two classes, where 1.0 is perfect and 0.5 is chance. Model Context Protocol (MCP): how an automated agent calls the tools of miDataworks. Training formats: supervised fine-tuning (SFT), direct preference optimization (DPO) and Kahneman-Tversky optimization (KTO).',
    ],
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
