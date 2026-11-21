# US Social Security number detector: the run, end to end

This is the process the first dry run settled on, in order. `pipeline.json` holds the recipes and arguments for each stage; a stage named here is an `id` there. `backend/tests/unit/test_behaviour_pipeline.py` holds the pack to it.

**The rule for changing anything:** a change made to a live recipe, template or set during a run goes back into this pack before the run counts. In the first dry run the pack stopped matching what ran, and a rerun from it would have regenerated every early failure.

Who acts: **agent** can call it directly. **Approval** waits for the operator in the app. **Human** only the operator can do it.

## Before you start
- Check nothing is running in miStudio, miLLM, miDataworks or miForge. A deploy kills in-flight GPU work.
- Generation needs miLLM with one model loaded at a time. Plan GPU time from `sizes.json`: about 18 hours for Llama and 7 for Qwen.
- Set the classifier price and spending ceiling in miDataworks Settings (operator only).
- TypeSafe, the paid classifier, reads every row of the merged carriers. At $0.000015 per call, the dry run's 29,593 rows cost about $0.44 per question.

## 1. Set up (agent)
1. `rule_set`: save `rule_set.json`. The sha256 it returns must equal the pin in `recipe.json`.
2. `templates`: save each template in `templates.json`. Point the run's `template_map` at the saved ids.
3. `index_sources`: one index source per plan in `sizes.json`, from `index_start` for `rows`. Keep ranges apart: row keys follow the index. A test holds this.
4. `plans`: build `carrier_plan@1.1.0` from `plan.json` with the plan's `rows`, `templated_share`, `genre_shares` and `plan_seed`. Its carrier ids name the plan, so two plans cannot share one, whatever their ranges.
5. `role_column`: build the role column on each plan. It is a separate build because the recipe check cannot see `carrier_plan`'s output columns.

## 2. Generate (approval: load the model)
6. `generation`: one run per plan, with the run's `generation_seed`. **Llama writes the training carriers. Qwen is held out, so it never reaches train.**
   - Sampling makes generated text differ between runs, even with the same seed. Everything downstream is deterministic given the rows.
   - Expect losses: `sizes.json` `kept` records what the dry run kept (Llama 88%, Qwen 84%). Plans are oversized for this.
   - Measure by cell, not overall. Qwen's 66% once hid 2% on voice transcripts. Read kept rows per genre on a small run before the full one.
   - If the census shows short cells, run `llama_topup` from `sizes.json`, with shares set from the shortfall.

## 3. Pass 1 and the merge (agent)
7. `pass1`: build per run, filling `{{generation_run_id}}`. It inserts the element and runs the leak check.
8. `merged_pass1`: concatenate the pass 1 versions, seed 1714. Its `check_carrier_ids` step refuses a carrier id shared across runs, before TypeSafe is paid for. To check a version already built without that step, `dataworks_run_version_check` runs it over every row; a preview only reads a sample, so its pass proves nothing.

## 4. Second opinion and calibration (approval, then human)
9. `second_opinion`: TypeSafe answers the `presented` question on every merged row.
10. `calibration_labels`: open the queue on the **merged** version with the second opinion's question, exactly. Sampling the filtered version would be circular. The model's scores stay hidden.
    - **Human:** in miDataworks, open **Review**, then the queue. For each row, click `yes` or `no`, or press `1` or `2`: it saves and moves on. Flag a row only if you cannot tell, with a reason.
    - **The question, taken literally:** does the text present a number as a specific person's SSN? Answer `yes` even if it looks fake or is masked. Answer `no` when SSNs are only discussed, the number belongs to something else, or no value is given.
    - Then, as agent: `dataworks_build_calibration_set`, `dataworks_compute_calibration`. The lower bound of the 95% interval on the agreement score (AUROC, area under the ROC curve) must reach 0.70.
    - **If it fails, stop.** The filter in pass 2 rests on this verdict, and D-7 refuses the send.

## 5. Pass 2 (agent)
11. `pass2`: build on the merged version with the label run bound, seed 1714. Fill `{{label_run_id}}` and `{{slot_hex_alternatives}}`, which is every run's slot marker hex joined with `|`: read the distinct `[[SLOT-XXXXXXXX]]` values of the `slot` column of each pass 1 version.

    What it does, in order:
    - Drops slot markers the writer reshaped or kept as bare hex, and other leaks.
    - Drops carriers where TypeSafe contradicts the planned framing. That includes a `naming_no_value` framing that holds a number: the writer named the field and left a value out, the slot put a number there, and the plan would label it negative. In the first dry run the operator answered `yes` on 20 of the 23 such rows TypeSafe called yes, and `no` on 9 of the 10 it called no.
    - Adds the minimal-pair siblings, in training only.
    - Takes the veto from the plan's `kind`: masked, wrong-length and impossible values (D124, D125). The rule cannot see a rendered value, and none of these is a disclosure in any form.
    - Drops negatives holding an invented number, valid or invalid. `bad_check` rows stay: the invalid number is their own planned element.
    - Splits by held-out genre and generator, with 5% of the rest as the in-distribution test.

    **Read the result before moving on.** The proofs passing does not mean the labels are right. Cross-tab with `dataworks_get_version_crosstab`, `columns` `deployed_truth`, `kind`, `form`, `sibling_variant`: it counts every row per split. Read a sample per cell with `dataworks_get_version_rows`. Masked and positive must be zero. The first dry run passed every proof with 23% of train's positives mislabelled.

## 6. Calibration negatives (agent)
12. `negatives_sources`: import the three pinned slices in `benchmarks.json` `real_text`.
13. `negatives`: screen with the rule set, keep the carriers' length range, sample 2,500 per language so each language can be read on its own.
13a. `everyday_sources`: import `benchmarks.json` `everyday_text`: the same corpora at the same revisions, the NEXT data file of each, so no row is a calibration negative (ENH-006 D128).
13b. `everyday_negatives`: the same screen and length range, then keep only rows that use a number (three digits in a row, a delimited number, or two number words in a row), 2,000 per language. Every probe of the stack trains on these as negatives: the first spelled-out probe fired on 10.6% of ordinary text because it had never seen a number word used normally.

## 7. The set (agent, then approval)
14. `main_set`: create the set on the pass 2 and negatives versions. Keep `label_source_columns` as written: the audit does not test a listed column, so only list what the label truly comes from. `label_source_reasons` says why each is there.
15. `check`: run the checks. A D-4 refusal has a repair: `dataworks_plan_leakage_repair`, save and build its recipe on the pass 2 version, bind that version, check again. Read why any check is green, and what it saw.
    - **D-7 does not judge the training label.** It reads TypeSafe's record, and `deployed_truth` comes from the plan. Run `dataworks_get_calibration_label_agreement` with the calibration set, the pass 2 version, `label_column` `deployed_truth`, `positive_value` `true`, `by` `kind` and `framing`, and `exclude` `{"kind": ["masked", "wrong_length", "bad_check"], "framing": ["discussed_in_general"]}`. The question asks about presentation, so it answers `yes` on a masked, wrong-length or impossible value framed as someone's, all false by decision (D124, D125), and `no` on an example, true by decision (D124). The first dry run read 80% on true rows (76 of 95) and, after D115's fix, 89.6% on false rows (86 of 96). No check gates this yet: whether it should refuse is the operator's decision.
15a. **The stack (ENH-006 D128): one probe per form.** The stack's verdict is that any probe fires, so each probe learns its own form and leaves the others' disclosures out of every split. Every probe trains on every non-disclosure row of every form plus the everyday rows. `{{pass2_version}}` here is the D-4 repair from step 15.
    - `stack_negatives`: build it once on the repaired pass 2 version: the non-disclosure rows, splits unchanged.
    - `stack`: for each probe in `probes`, put its `owns` in place of `{{owns}}`.
      - **No `id_test_share`** (digit forms, split into parts, glued letters): build `probe_recipe` on the repaired pass 2 version plus the everyday version.
      - **With an `id_test_share`** (spelled, spaced, mixed with words, split across lines): their in-distribution test would hold under 20 positives, and miStudio refuses a view under 20 of a class.
        1. Build `resplit_recipe`, with its fractions in place of `"{{split_fractions}}"` as a JSON list, on the repaired pass 2 version. That redraws only the probe's own positives; held-out sets keep pass 2's values.
        2. Build `probe_recipe` on that, `stack_negatives` and the everyday version.
        3. Check the set and repair D-4. The moved positives' training siblings are near duplicates of them, and the repair drops those training rows. The first run lost 21 to 23 training positives per probe in all, moved and dropped together.
    - One set per probe, following `set`. The label is `probe_truth`, mapped `True` and `False`, because `rule_label` writes `str(True)`. An unlabelled row reads `unlabelled`, which D-2 refuses.
    - Read the cross-tab of `probe_truth` by split before checking: train holds the 6,000 everyday rows and only the probe's own positives.
    - The digit-form probe declares `r_ssn_cued` (its `label_source_extra_reason`).
16. `send`: approval. Only after D-7 is green from a real calibration record. Send the stack's sets together under one approval with `dataworks_send_detector_sets`. It writes one version's publish files per call, so call it again until it returns the approval. Each view declares its grouping columns from the manifest, so `kind`, `framing`, `form` and `genre` need no `update_probe_dataset_columns`.

## 7a. The human-graded test set (agent, then approval)
16a. `human_eval`: build this stage's recipe on the merged version, filling `{{calibration_set}}` and one `exclude_versions` entry per training version a probe compared on this set was fitted on. Each rebuild moves rows between splits, so a row clear of one training set can sit in the next. `calibration_labels@1.0.0` keeps only the rows the set labelled, writes each one's human label, and drops any row sharing a row key, `carrier_id` or `pair_id` with a named split; its step report counts what it kept and left out. The later `rule_label` keeps masked rows separable.
16b. Bind it to the set as an `extra_eval` role: `label_column` `human_label_ex_masked`, mapped `yes` positive, `no` negative, `excluded` excluded. Run `check`, then `send` again: only the new view registers. It goes to miStudio as `eval`, `in_distribution`, so it never counts toward the rung 2 gates or the choice of probe: it judges the plan's labels, and a probe's score on it is reported beside them.

## 8. Train and evaluate (approval: GPU)
17. `train` and `evaluate` are filled in when the dry run reaches them.
    - Held-out wording baselines (D-9) were 0.78-0.83 on AUROC: a probe has to beat them. Recall at 1% false positives is the headline.
    - Read recall by `kind` and rendering: about half of positives are format-invalid, by decision, and half are rendered.

## Known limits of this data
- TypeSafe chooses rows, in held-out sets too, so held-out sets hold few of the hardest `discussed_in_general` negatives.
- A number invented in spelled or spaced form can't be seen by the rule, so a negative may still hold one. Unmeasured.
- Positives holding a second valid number are uneven across held-out sets (0.4% to 17%), so read `test_unseen_both` with that in mind.
- The real-text positive role and the three sub-question sets are not built in this pipeline yet.
