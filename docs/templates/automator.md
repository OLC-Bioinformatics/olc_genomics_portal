# Automator Name

<!--
Purpose of this template
------------------------
Use this structure for Redmine Automator documentation pages. Remove all
HTML comments before publishing. Preserve exact command names, parameter
names, filenames, subject values, capitalization rules, and accepted values.

Writing principles
------------------
- Lead with the user's task, not the software's history.
- Make each section understandable when retrieved on its own.
- Use exact identifiers in backticks.
- State limitations and boundaries between similar tools explicitly.
- Do not claim capabilities that are not confirmed by the implementation.
- Prefer short paragraphs, lists, and fenced examples.
-->

## What does it do?

<!--
Open with a direct, user-oriented statement:
"Use <Automator> when you need to..."

Then document:
- the question or task the automator addresses;
- supported input types;
- important limitations;
- when a related automator may be more appropriate.
-->

Use **Automator Name** when you need to describe the supported task clearly.

Automator Name accepts the following input types:

- `FASTA` assemblies
- paired-end `FASTQ` reads

<!-- Remove unsupported input types. -->

Important limitations:

- Document any organism, input, analysis, database, or workflow limitations.
- Explain whether the automator operates on assemblies, raw reads, or both.

## How do I use it?

### Subject

In the **Subject** field, enter:

```text
automator-subject
```

<!-- State whether spelling and case matter. -->

Spelling matters. Matching is/is not case-sensitive.

### Description

<!-- Put required content first. Describe one requirement at a time. -->

The **Description** field must contain:

1. The required analysis declaration.
2. One `SEQID` per line.
3. Any other required fields in their required order.

Minimal request:

```text
analysis=example
2026-SEQ-0001
```

### Attachments

<!-- If there are no attachments, state that explicitly. -->

No attachment is required.

<!-- Or document required/optional attachments with exact formats and names. -->

If you use `analysis=custom`, attach a FASTA-formatted target file and reference
its exact filename in the Description field:

```text
analysis=custom
targetsfile=targets.fasta
2026-SEQ-0001
```

### Optional parameters

<!--
For each parameter, include:
- exact spelling;
- purpose;
- default value;
- accepted values or range;
- an example;
- interactions or incompatibilities.
-->

#### `parameter_name`

Controls what the parameter changes.

- Default: `default_value`
- Accepted values: `value1`, `value2`
- Example:

```text
parameter_name=value1
```

Important interactions:

- Explain whether this parameter requires or conflicts with another parameter.

### Examples

#### Minimal request

```text
analysis=example
2026-SEQ-0001
```

#### Complete request

```text
analysis=example
parameter_name=value1
2026-SEQ-0001
2026-SEQ-0002
```

Explain in one or two sentences what this request will do.

## Interpreting results

<!-- Use exact archive and output filenames. -->

When the analysis finishes, the automator uploads:

```text
result_archive.zip
```

Important files include:

- `result_file.tsv` — explain what the file contains and how to interpret it.
- `summary.csv` — explain important columns, thresholds, or caveats.
- `report.html` — explain what the report is intended to show.

Important interpretation notes:

- State whether absence of a result means a true negative or may reflect a
  technical limitation.
- State important thresholds, units, and quality considerations.
- Explain any result that users commonly misinterpret.

## How long does it take?

A typical request takes approximately **time range**.

Runtime depends on:

- the number of samples;
- input size and type;
- selected parameters;
- queue and compute availability.

<!-- Avoid guarantees unless the system enforces them. -->

## What can go wrong?

### Requested `SEQID` is unavailable

**Symptom:** Describe the issue note, warning, or missing output.

**Likely cause:** The requested sequence data could not be found.

**What to do:** Verify the `SEQID`, confirm that the required input files are
available, and submit a corrected request.

### Required parameter or attachment is missing

**Symptom:** Describe the error shown in the Redmine issue.

**Likely cause:** A required field, parameter, or file was omitted or misspelled.

**What to do:** Correct the request using the required syntax shown above and
submit a new issue.

### Analysis fails after starting

**Symptom:** Describe the failure note and any partial result archive.

**Likely cause:** Document known tool, pipeline, environment, or input-quality
causes without exposing sensitive infrastructure details.

**What to do:** Provide safe, actionable troubleshooting steps and escalation
instructions.

## Related automators

<!--
Document overlap explicitly. Avoid vague statements such as "similar to".
Explain the decision boundary.
-->

- **Related Automator A** — choose this when the input is raw `FASTQ` reads.
- **Related Automator B** — choose this when the input is a draft `FASTA`
  assembly.

## References

<!-- Include maintained upstream documentation and required citations. -->

- [Upstream tool documentation](https://example.org/)
- [Publication](https://doi.org/example)
