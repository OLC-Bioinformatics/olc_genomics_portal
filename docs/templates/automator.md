# Automator Name

<!--
Use this template for user-facing Redmine Automator documentation. Remove all
comments and placeholders before publishing. Preserve exact subjects, parameter
names, accepted values, filenames, capitalization, and punctuation.

Each section should make sense when retrieved independently. State whether a
fact is user guidance, an implementation detail, or upstream-tool behavior.
Do not copy secrets, credentials, private hostnames, or sensitive paths.
-->

## Document metadata

<!-- Maintain this metadata as visible prose or repository metadata. -->

- **Automator owner:** Team or role
- **Last verified:** YYYY-MM-DD
- **Automator code revision:** Git commit or release
- **Underlying tool and version:** Tool 1.2.3
- **Database/reference-data version:** Version or last-updated date
- **Documentation authority:** Approved user guidance
- **Access level:** Standard or internal

## What does it do?

Use **Automator Name** when you need to describe the supported user task directly.

Supported inputs:

- `FASTA` assemblies
- paired-end `FASTQ` reads

Important limitations:

- State organism, input, database, scale, and workflow restrictions.
- State whether the workflow operates on assemblies, reads, or both.
- State when a related Automator is more appropriate.

## How do I use it?

### Subject

In the **Subject** field, enter:

```text
automator-subject
```

State whether spelling and case matter and whether aliases are accepted.

### Description

The **Description** field must contain the following items in this order:

1. The required analysis declaration.
2. One `SEQID` per line.
3. Any required fields or parameters in their exact order.

Minimal request:

```text
analysis=example
2026-SEQ-0001
```

### Attachments

State explicitly that no attachment is required, or document every required and optional attachment. Include exact formats, filename rules, and how the description refers to each attachment.

Example:

```text
analysis=custom
targetsfile=targets.fasta
2026-SEQ-0001
```

### Parameters

For every user-configurable parameter, provide exact spelling, purpose, default, accepted values/range, example, and interactions.

#### `parameter_name`

Controls what the parameter changes.

- **Required:** No
- **Default:** `default_value`
- **Accepted values:** `value1`, `value2`
- **Example:**

```text
parameter_name=value1
```

- **Interactions:** Explain requirements, conflicts, ignored combinations, and whether the Automator or user controls the setting.
- **Source:** Identify whether this is approved Automator guidance, observed implementation behavior, or upstream-tool guidance.

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

Explain the intended effect of each example.

## What happens after submission?

Document visible Redmine behavior:

1. How the request is validated.
2. What input data are located.
3. Which broad analysis mode is run.
4. Which artifacts are uploaded.
5. Which issue status/note indicates completion.
6. What the user should do when validation or analysis fails.

Do not expose unnecessary infrastructure details in standard-access documentation.

## Interpreting results

When the analysis finishes, the Automator uploads:

```text
result_archive.zip
```

Important files:

- `result_file.tsv` — purpose, important columns, units, thresholds, caveats.
- `summary.csv` — purpose and common interpretation mistakes.
- `report.html` — intended use.

State whether absence of a result is a true negative or may reflect input quality, unsupported organisms, database limitations, or technical failure. State important thresholds and whether results require expert review or confirmation.

## How long does it take?

A typical request takes approximately **time range**, based on an identified operational baseline.

Runtime depends on sample count, input size/type, selected options, queue/compute availability, database access, and tool behavior. Avoid guarantees unless the system enforces them. Include the date or release for measured estimates.

## What can go wrong?

### Requested `SEQID` is unavailable

**Symptom:** Describe the Redmine note, missing output, or user-visible error.

**Likely cause:** The sequence or required input data could not be found.

**What to do:** Verify the `SEQID`, confirm required files exist, and submit a corrected request.

### Required parameter or attachment is missing

**Symptom:** Quote or accurately describe the user-visible error.

**Likely cause:** A required field, parameter, or file was omitted or misspelled.

**What to do:** Correct the request using the documented syntax.

### Analysis fails after starting

**Symptom:** Describe the issue note and any partial artifacts.

**Likely cause:** List approved, non-sensitive causes supported by evidence.

**What to do:** Provide safe diagnostic, retry, and escalation steps. State what information should accompany escalation.

## Related Automators

- **Related Automator A** — choose this when the input is raw `FASTQ` reads.
- **Related Automator B** — choose this when the input is a draft `FASTA` assembly.

Explain decision boundaries rather than only saying tools are similar.

## Implementation notes

<!-- Put this section in internal documentation when it exposes internal details. -->

- **Automator source:** `path/to/automator.py` at commit/release
- **Underlying command:** Describe exact fixed options and which options users can control.
- **Input retrieval:** Approved operational summary.
- **Uploaded artifacts:** Exact filenames.
- **Redmine transition:** Completion/failure status and notes.
- **Known deployment constraints:** Version-matched, non-secret details.

When code and user documentation disagree, do not silently choose one. Record the discrepancy, identify the deployed revision, and have the owner correct the authoritative documentation or implementation.

## References

- [Version-matched upstream documentation](https://example.org/)
- [Open or authorized publication](https://doi.org/example)
- [Approved validation or database documentation](https://example.org/)

Record tool/document versions and licensing/access restrictions. Prefer upstream documentation for operational parameter behavior and publications for scientific rationale, validation, and limitations.
