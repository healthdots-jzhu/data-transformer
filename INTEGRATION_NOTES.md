# Implementation notes

This project contains the configuration-driven transformer, its tests, schema
configurations, and coverage tooling.

## Configuration migration

Solution 15 moves `settings.generic_aliases` to top-level `generic_aliases` and
`settings.generic_line_container_names` to top-level `generic_line_containers`.
The supplied configs already use the updated structure. Configs are external files;
pass their path to `transform()` or the CLI after installing the package.

## Solution 16 findings and proposed fixes

Solution 16 is applied as supplied. The following fixes are proposals, not changes
included in this application.

1. **Require agreement between each label's independent best match.** Currently,
   all variants only need to score above the threshold against a common candidate.
   For example, configured `insured name` -> `insured_name`, `insurer name` ->
   `insurer_name`, and `nom compagnie` -> `insurer_name` can accept the conflicting
   pair `insured name / nom compagnie` as `insurer_name` through a fuzzy match.
   Resolve each variant against the complete field list first, using the existing
   strong/weak ranking and score rules. Require one unambiguous winner per variant
   and the same canonical field across all variants. Refuse unmatched variants,
   ties, or disagreement; report which variants resolved to which fields. Keep the
   weakest score and combined language evidence only after agreement is established.
   Regression tests should cover agreeing labels, conflicting exact winners with
   overlapping fuzzy candidates, ties, unknown labels, and single-language inputs.

2. **Preserve all languages when a candidate becomes a duplicate.** Currently,
   `CompetingMatch` stores single-language attributes only. A bilingual candidate's
   single-language attribute is `None`, so its English/French evidence disappears
   when it loses to a plain `price` key. Extend competing matches to retain the
   complete language tuples of both candidates and include those tuples in
   `FieldMappingOutcome.languages()`. Preserve the existing single-language fields
   where needed for translation handling. Number format must depend on all mapped
   evidence, regardless of which value wins. With both `price` and `price / prix`
   containing `1,234`, mixed mode should report ambiguity by default. Regression
   tests should cover both source-key orders, English and French preference,
   document and line fields, and explicit ambiguity-policy overrides.

## Other multilingual limitations retained from Solution 15

These are review findings, not fixes included in this application:

- Additional translations bypass field validation.
- Repeating an identical French translation through two aliases can cause a false
  conflict with the English value.
- CSV delimiter detection considers the header only; bilingual `|` labels can
  cause a comma-separated file to be misclassified. An explicit delimiter avoids it.

The expanded test suite covers configuration contracts, source formats and encodings,
ownership and alias mapping, validator boundaries, number formats, line processing,
schema selection, extension hooks, cross-field rules, output schemas and the CLI.
Passing these tests does not resolve the known findings listed above.

`scripts/run_coverage.py` measures the transformer package and requires at least
96% statements and 96% branches independently. Coverage excludes no production
lines or branches. Current results are recorded in `TEST_RESULTS_transformer.md`.
