# Workflow Reconstructibility

Assess whether the complete retained research account contains enough
information to understand and reconstruct the material workflow without
executing it or requiring redundant prose.

## Inspect

Use the maintained summary, entry prose, recorded commands, scripts, modules,
configuration, manifests, registered inputs, retained intermediates and
outputs, environment records, and known gaps.

## Review Criteria

- Trace the material sequence from inputs and commands to retained outputs
  through the account as a whole.
- Look for consequential ad hoc commands, transient settings, manual
  interventions, or runtime choices absent from every retained form.
- Check whether required inputs, intermediate products, outputs, and
  dependencies are retained or recoverable.
- Inspect scripts for scientifically material internal dependencies,
  configuration, environment assumptions, or runtime choices missing from the
  retained account.
- Distinguish recoverable workflow detail from intentionally discarded
  exploration.
- Determine whether a knowledgeable reader could reconstruct the workflow
  without guessing a material step or requiring prose that duplicates a
  retained script or artifact.

Trace the workflow across all retained forms and treat scripts and artifacts as
first-class parts of the record. Start from commands or artifacts on which
material results depend and follow only the dependencies needed to reconstruct
those paths.

## Exclusions

- Do not require prose to repeat information plainly and stably recoverable
  from scripts, commands, configuration, manifests, inputs, or artifacts.
- Mechanical validation owns authored relationships it knows about; this lens
  may discover material relationships or steps that were never declared.
- Reproduction executes the recorded workflow and tests regenerated artifacts.
- Research-Log Conformance owns adherence to logging conventions.
- Review reports reconstruction gaps; it does not perform record repair or new
  research.

## Finding Guidance

- **Issue:** A material workflow step, input, dependency, configuration,
  environment assumption, intervention, intermediate, or output is absent from
  every retained form and cannot be reconstructed without guessing.
- **Improvement:** The workflow is reconstructible, but a consequential
  relationship or navigation path should be easier to locate.
- **Unverified:** Access limits or missing retained material prevent the
  workflow from being assessed.
