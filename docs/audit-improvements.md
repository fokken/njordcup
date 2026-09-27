# Audit improvement priorities

[Back to README](../README.md)

These are proposed improvements, not implemented capabilities. The September 2026
review covered request budgeting/recovery, checkpoint invalidation, review scope,
per-file consolidation, SARIF resumption, reporting and their documentation. Mocked
tests establish controller behavior; they do not measure live-model detection quality.

## Recommended order

1. **Measure narrative audit quality.** Extend the small structured evaluation suite
   with realistic multi-file vulnerable/fixed pairs and independently reviewed
   expected evidence. Evaluate the default prose workflow, including consolidation.
   Track missed issues, unsupported claims, correct locations, retained details,
   tokens and time. Compare context budgets and models against the same cases before
   changing defaults. CWE matches alone are too coarse to establish a correct finding.

2. **Add a bounded cross-file review stage.** Per-file coverage can miss vulnerabilities
   spanning an input handler, service, authorization check and sensitive operation.
   Queue related file groups using existing import/call hints and architecture notes;
   include manifests/configuration that control their behavior. Track this as separate
   relationship coverage rather than inflating reviewed-line counts. Keep a generic
   text fallback for languages without a parser.

3. **Make missing context actionable in prompt mode.** Prose reviews currently receive
   automatically chosen excerpts without the structured mode's retrieval loop. Add an
   optional bounded follow-up for uncertain claims: retrieve callers, callees, helpers
   and relevant configuration, then ask the model to reassess. Preserve the initial
   response and any unresolved context gaps. Avoid making valid JSON a prerequisite;
   controller-selected context can drive the first version.

4. **Validate evidence and challenge potential issues.** Keep raw prose, but optionally
   check recognizable path/line citations against the supplied source and run a second
   pass for counterevidence, guards and exploit preconditions. Label missing or invalid
   evidence instead of discarding the response. Ask for the input-to-operation path,
   attacker control, affected scope and concrete remediation. Separate source provenance
   checks from the model's judgment about exploitability.

5. **Expose gaps and schedule targeted passes.** Report skipped files with reasons,
   oversized chunks, omitted reference context and unfinished analyses in one queue.
   Add optional passes for authorization, tenant isolation, unsafe input handling,
   secrets and deployment configuration across related modules. Distinguish files
   indexed, lines processed, relationships examined and issue claims verified.

6. **Preserve detail through consolidation.** Synthesis currently splits serialized
   text and may divide an issue's explanation. Prefer whole file/analysis boundaries,
   stable references to original responses and separate issue/limitation sections.
   Check whether consolidated text retains source references and distinct claims;
   expose originals alongside the summary. Add explicit cross-area per-file
   consolidation for overlapping reviews, with checkpoints.

7. **Make change-driven reviews more conservative.** Offer invalidation modes that
   include surrounding components when import/call resolution is weak, and trigger
   cross-file checks on changed interfaces or security configuration. Show why each
   checkpoint was reused or invalidated. Parser-derived edges remain hints, so a
   full rerun must remain available.

Start with quality evaluation and the cross-file stage: they make it possible to
measure whether additional context and calls actually improve detection. Keep each
extra pass optional and budgeted so smaller local models remain usable.
