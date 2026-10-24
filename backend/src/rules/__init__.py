"""Rule signals: the pure validator library, pattern dialect and rule-set evaluator (feature 016).

**Purity guarantee (FR-016.4; FTID 016 section 2.3).** Nothing in this package reads a database,
settings, the clock, the network or a random source when it checks a value or evaluates a rule set.
It imports nothing from ``services``, ``models``, ``core.config`` or ``core.database``;
``tests/unit/rules/test_rules_package_purity.py`` walks every module's syntax tree and fails on such an
import. I/O around rule sets (storage, resolution at build time, use records) belongs to
``services/rules/`` (slice 2).

What it refuses rather than guesses: an unknown validator version (``ValidatorUnknown``; a rule
names an exact ``(id, version)`` and is never given "the latest"), a pattern outside the dialect
(``PatternRefused`` naming the rule and its code-point position), and a document that is not a
``midataworks.rule-set/v1`` document (the schema, generated from ``rule_set.RuleSetDocument``).

Nothing here names a behaviour (FR-016.33): validator ids are formats and algorithms.
"""
