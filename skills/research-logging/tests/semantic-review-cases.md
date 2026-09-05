# Semantic Review Cases

Use these cases for focused review of the research-logging semantic Review
surface. They are behavior expectations, not research evidence or mechanical-
validation cases.

## Researcher Direction And Routing

Given completion of Record, Replace, Update Summary, Repair, Reorganize,
Validate, reproduction, or an ordinary agent self-check, the agent does not
start semantic Review unless the researcher separately directs it.

Given “review the analysis,” “review the evidence,” or “review the record,” the
agent reads the catalog, expands only the corresponding named group, briefly
states the selected group and lenses, and begins without requesting redundant
confirmation.

Given “review everything” or “perform a complete semantic review,” the agent
expands all three groups to all nineteen lenses. It reports findings under the
owning lenses and assigns no aggregate group verdict.

Given only “review this research log,” the agent presents the catalog's exact
four-option group-first menu and waits for the researcher to choose. It does not
default to Record Review or all lenses.

Given a focused but ambiguous request that maps to no group or definite lens,
the agent presents the exact numbered nineteen-lens catalog and waits for a
selection. It does not invent lens names or descriptions.

Given a clear natural request such as “check whether these conclusions are
justified and appropriately cautious,” the agent selects Claim Support and
Claim Calibration, names them, and proceeds. It does not require the researcher
to know the canonical lens names.

Given a named lens and an ambiguous target, the agent preserves the selected
lens and asks only for the target needed to proceed. It does not reopen lens
selection.

Given ambiguous wording between Validate, Review, and Reproduction, the agent
asks which operation the researcher wants. The explicit operation names are
strong intent signals, and Validate does not require the researcher to say
“mechanical.”

Given only “check this research log” or “verify this research log,” the agent
asks whether the researcher wants Validate, Review, or Reproduction. Given a
specific semantic question beginning with “check” or “verify,” it may instead
route by meaning to the matching Review lenses.

Given a maintained `<log>.md` target, the complete log includes that summary
and its sibling `<log>/` tree. The agent discovers the tree independently of
summary navigation, then loads only the material required by the selected
lenses. An unlisted entry is not automatically outside the log.

## Loading And Traversal

Given one selected lens, the agent loads the shared Review operation, catalog,
and only that lens prompt. It does not load all lens prompts.

Given a named group, the agent loads exactly the group's lens prompts. Given
several selected lenses, it loads their prompts without loading adjacent lenses.

Given compatible selected lenses, the agent makes one material-first traversal
and applies every relevant selected lens while each entry, claim, evidence item,
source, artifact, or script is in context. It does not make one complete pass
per lens or group.

Given a lens that requires materially different material, the agent may make a
targeted additional traversal for external sources, rendered presentations, or
code and dependencies. It does not preload the complete log merely because the
review is semantic.

Given an independent-review request, the review runs in a fresh task with the
request, resolved target, selected prompts, current material, and neutral
locators. It excludes prior semantic conclusions, proposed corrections, and
expected outcomes unless adjudicating them is the target. Rigor alone does not
require a fresh task.

## Report And Authority

Given a semantic Review, the agent may use read-only or disposable diagnostics
but does not execute a research command, vary inputs or methods, regenerate an
artifact, run Validate or reproduction, create evidence, or edit the maintained
research account.

Given findings, the agent returns one self-contained numbered report using only
Issue, Improvement, and Unverified. Each finding names its lens and durable
location, explains the problem and consequence in natural prose, and ends with
a bounded recommended action. It uses no universal severity or semantic pass
status.

Given no material findings, the agent says that none were found within the
named target and applied lenses, then states unverified or out-of-scope areas
separately. It does not certify or approve the research account.

Given a completed Review, the agent stops for researcher direction. A later
instruction to address named findings authorizes the same agent to switch to
the appropriate owning research operation. It does not broaden the work to
unselected findings.

Given authorization to address a semantic Review finding, the agent switches
to the owning research operation and runs only that operation's bounded checks.
Repairing a Review finding does not automatically invoke Validate, another
Review, or reproduction. Repair of a published mechanical-validation finding
may separately rerun validation to confirm that named condition.

Given a review finding about a researcher decision, the agent may report weak
support but does not reverse, replace, or relabel the decision. Given missing
content, it does not invent evidence, uncertainty, references, results,
decisions, or conclusions.

## Lens Boundaries

Given an external dataset used for several roles, Source Authority inspects its
identity, version, authority, and general limitations once, then judges fitness
for each role separately. It does not treat generated model or simulation
outputs as external sources.

Given a cited statement, Citation Support checks what the reference establishes.
Source Authority separately checks whether that reference is trustworthy and
fit for use; Claim Support separately checks the account's complete support.

Given a substantive claim, Claim Support evaluates whether the evidence and
reasoning warrant its proposition. Claim Calibration separately evaluates
whether certainty, scope, causality, and uncertainty wording match that support.

Given a researcher decision, Decision Support evaluates its evidence,
objectives, criteria, constraints, alternatives, and tradeoffs without choosing
a replacement decision.

Given an evidence body, Evidence Coverage checks whether material conditions,
outcomes, counterexamples, and boundaries are represented. Evidence Selection
separately checks whether inclusion and exclusion choices are appropriate and
transparent.

Given a maintained summary, Summary Fidelity traces material statements to
entries and scans current entry conclusions and decisions for omitted or stale
summary content. It does not turn faithful transcription into proof that the
underlying claim is sound.

Given related entries, Cross-Entry Consistency compares terminology, units,
configurations, assumptions, results, and lifecycle state while allowing
clearly explained differences among materially different experiments.

Given a figure or table, Presentation Fidelity inspects the rendered form and
underlying retained material for misleading choices. Mechanical validation
separately checks deterministic source-to-presentation equality.

Given related presentations, Presentation Consistency compares titles, labels,
legends, terminology, ordering, units, and styling while allowing differences
that communicate a clear purpose.

Given research code, Implementation Fidelity compares code with the method,
configuration, selections, and output construction recorded in the log. General
maintainability and architecture remain code-quality review concerns.

Given a reported result, Result Derivation traces backward from the retained
result through substantive calculations to its upstream inputs. It stops before
the retained result-to-log-presentation relationship owned by validation. It
verifies feasible arithmetic from retained inputs before reporting a
quantitative contradiction, without turning that diagnostic into evidence or
reproduction.

Given a research method, Methodological Validity assesses the suitability of
the design, comparisons, controls, models, and assumptions. Correct
implementation does not establish methodological validity.

Given statistical conclusions, Statistical Validity examines sampling,
estimation, uncertainty, aggregation, and inference. Numerical Validity
separately examines approximation, convergence, stability, precision, and units.

Given workflow-reconstruction concerns, Workflow Reconstructibility treats
commands, scripts, configuration, manifests, inputs, intermediates, and
artifacts as first-class record content. It reports material information absent
from every retained form and never requires prose to repeat what those forms
make plain.

Given a log-structure review, Research-Log Conformance diagnoses semantic misuse
of section roles, entry boundaries, navigation, lifecycle state, or retained-
material placement. Deterministic file, metadata, association, and presentation
checks remain validation concerns.

Given a research-log writing review, Research-Log Writing applies
`$science-writing` with the research-logging conventions, uses sections as the
primary writing units, and also evaluates log-level coherence. It keeps findings
within writing rather than silently adding claim, method, or other lenses.

## Neighboring Review Systems

Given a request to review whether research code matches the method recorded in
the log, the agent applies Implementation Fidelity. Given a request for general
software quality, it uses code-quality review. An explicit request for both
keeps their findings distinct.

Given writing inside a research log, the agent applies Research-Log Writing and
its science-writing pairing. Given scientific prose outside a research log, it
uses science-writing directly.

Given a request to judge whether a citation supports an attribution, the agent
applies Citation Support. Finding, acquiring, registering, or updating a
reference remains reference work.
