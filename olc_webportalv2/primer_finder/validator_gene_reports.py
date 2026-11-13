import json
import os


GENE_CATEGORIES = (
    'FRAME_SHIFT', 'INTERNAL_STOP', 'PARTIAL_CONTIG_END', 'EXTENDED'
)
GENE_MIN_PERCENT_IDENTITY = 90.0


def _gene_value(record, *names):
    """Read a GeneSeekr/BLAST field while accepting common aliases."""
    raw = record.get('raw', record) if isinstance(record, dict) else {}
    for name in names:
        value = record.get(name) if isinstance(record, dict) else None
        if value in (None, '') and isinstance(raw, dict):
            value = raw.get(name)
        if value not in (None, ''):
            return value
    return None


def _gene_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gene_terminal_aligned_codons(record):
    """Return corresponding terminal reference and query codons.

    The query and subject alignment strings are placed in reference
    orientation. The final three non-gap subject positions are used so that
    the query codon is taken from the corresponding alignment columns.

    Returns:
        tuple: Reference codon and query codon. Either value may be None.
    """
    query_sequence = _gene_sequence(
        record,
        "query_sequence",
        "qseq",
        "query_alignment",
    )
    subject_sequence = _gene_sequence(
        record,
        "subject_sequence",
        "sseq",
        "subject_alignment",
    )

    if not query_sequence or not subject_sequence:
        return None, None

    query_sequence = "".join(query_sequence.split()).upper()
    subject_sequence = "".join(subject_sequence.split()).upper()

    if len(query_sequence) != len(subject_sequence):
        return None, None

    subject_start = _gene_number(
        _gene_value(
            record,
            "subject_start",
            "sstart",
            "target_start",
        )
    )
    subject_end = _gene_number(
        _gene_value(
            record,
            "subject_end",
            "send",
            "target_end",
        )
    )

    if (
        subject_start is not None
        and subject_end is not None
        and subject_end < subject_start
    ):
        query_sequence = _gene_reverse_complement(query_sequence)
        subject_sequence = _gene_reverse_complement(subject_sequence)

    terminal_indexes = []

    for index in range(
        len(subject_sequence) - 1,
        -1,
        -1,
    ):
        if subject_sequence[index] != "-":
            terminal_indexes.append(index)

        if len(terminal_indexes) == 3:
            break

    if len(terminal_indexes) != 3:
        return None, None

    terminal_indexes.reverse()

    reference_codon = "".join(subject_sequence[index] for index in terminal_indexes)
    query_codon = "".join(query_sequence[index] for index in terminal_indexes)

    valid_bases = set("ACGT")

    if not set(reference_codon).issubset(valid_bases):
        reference_codon = None

    if not set(query_codon).issubset(valid_bases):
        query_codon = None

    return reference_codon, query_codon


def _gene_query_key(value):
    """Return a stable query name for grouping reports by strain."""
    value = os.path.basename(str(value or '').strip())
    for suffix in ('.fasta', '.fa', '.fna', '.fas'):
        if value.lower().endswith(suffix):
            value = value[:-len(suffix)]
    return value


def _gene_report_key(value):
    """Return the strain identifier encoded in a GeneSeekr report name."""
    value = os.path.basename(str(value or '').strip())
    for suffix in (
        '_blastn_geneseekr.tsv',
        '_blastn_geneseekr.csv',
        '_tblastx_geneseekr.tsv',
        '_tblastx_geneseekr.csv',
        '_geneseekr.tsv',
        '_geneseekr.csv',
    ):
        if value.lower().endswith(suffix):
            return value[:-len(suffix)]
    return _gene_query_key(value)


def _gene_percent_match(record):
    return _gene_number(_gene_value(
        record, 'percent_match', 'percent_identity', 'identity',
        'percent_id', 'percentidentity'
    ))


def _gene_identity_passes_cutoff(record, cutoff=GENE_MIN_PERCENT_IDENTITY):
    identity = _gene_percent_match(record)
    return identity is None or identity >= cutoff


def _gene_interval(record, start_names, end_names):
    start = _gene_number(_gene_value(record, *start_names))
    end = _gene_number(_gene_value(record, *end_names))
    if start is None or end is None:
        return None
    return min(start, end), max(start, end)


def _gene_subject_interval(record):
    return _gene_interval(
        record, ('subject_start', 'sstart', 'target_start'),
        ('subject_end', 'send', 'target_end')
    )


def _gene_query_interval(record):
    return _gene_interval(
        record, ('query_start', 'qstart', 'contig_start'),
        ('query_end', 'qend', 'contig_end')
    )


def _gene_bitscore(record):
    return _gene_number(_gene_value(
        record, 'bit_score', 'bitscore', 'score'
    )) or 0


def _gene_hit_quality(record):
    subject_interval = _gene_subject_interval(record)
    subject_length = _gene_number(_gene_value(
        record, 'subject_length', 'slen', 'target_length'
    ))
    coverage = 0
    if subject_interval is not None and subject_length:
        coverage = (subject_interval[1] - subject_interval[0] + 1) / subject_length
    return coverage, _gene_percent_match(record) or 0, _gene_bitscore(record)


def _gene_sequence(record, *names):
    value = _gene_value(record, *names)
    return ''.join(str(value or '').upper().split())


def _gene_reverse_complement(sequence):
    complement = str.maketrans(
        'ACGTRYKMSWBDHVN-', 'TGCAYRMKSWVHDBN-'
    )
    return sequence.translate(complement)[::-1]


def _gene_translate(sequence, offset=0):
    table = {
        'TTT': 'F', 'TTC': 'F', 'TTA': 'L', 'TTG': 'L',
        'TCT': 'S', 'TCC': 'S', 'TCA': 'S', 'TCG': 'S',
        'TAT': 'Y', 'TAC': 'Y', 'TAA': '*', 'TAG': '*',
        'TGT': 'C', 'TGC': 'C', 'TGA': '*', 'TGG': 'W',
        'CTT': 'L', 'CTC': 'L', 'CTA': 'L', 'CTG': 'L',
        'CCT': 'P', 'CCC': 'P', 'CCA': 'P', 'CCG': 'P',
        'CAT': 'H', 'CAC': 'H', 'CAA': 'Q', 'CAG': 'Q',
        'CGT': 'R', 'CGC': 'R', 'CGA': 'R', 'CGG': 'R',
        'ATT': 'I', 'ATC': 'I', 'ATA': 'I', 'ATG': 'M',
        'ACT': 'T', 'ACC': 'T', 'ACA': 'T', 'ACG': 'T',
        'AAT': 'N', 'AAC': 'N', 'AAA': 'K', 'AAG': 'K',
        'AGT': 'S', 'AGC': 'S', 'AGA': 'R', 'AGG': 'R',
        'GTT': 'V', 'GTC': 'V', 'GTA': 'V', 'GTG': 'V',
        'GCT': 'A', 'GCC': 'A', 'GCA': 'A', 'GCG': 'A',
        'GAT': 'D', 'GAC': 'D', 'GAA': 'E', 'GAG': 'E',
        'GGT': 'G', 'GGC': 'G', 'GGA': 'G', 'GGG': 'G',
    }
    sequence = ('N' * offset) + sequence.replace('-', '')
    return ''.join(
        table.get(sequence[index:index + 3], 'X')
        for index in range(0, len(sequence) - 2, 3)
    )


def _gene_translation(record):
    """Translate a nucleotide BLAST query in the reference orientation."""
    sequence = _gene_sequence(
        record, 'query_sequence', 'qseq', 'query_alignment'
    )
    subject_interval = _gene_subject_interval(record)
    if not sequence or subject_interval is None:
        return '', None
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    if subject_start is not None and subject_end is not None and subject_end < subject_start:
        sequence = _gene_reverse_complement(sequence)
    offset = int(subject_interval[0] - 1) % 3
    return _gene_translate(sequence, offset), subject_interval[1]


def _gene_internal_stop_coordinates(record, protein):
    """Return nucleotide ranges for premature translated stop codons."""
    sequence = _gene_sequence(
        record, 'query_sequence', 'qseq', 'query_alignment'
    ).replace('-', '')
    if not sequence or not protein:
        return []
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    offset = 0
    subject_interval = _gene_subject_interval(record)
    if subject_interval is not None:
        offset = int(subject_interval[0] - 1) % 3
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    if query_start is None or query_end is None:
        return []
    reverse = query_end < query_start
    coordinates = []
    def coordinate_text(value):
        return str(int(value)) if int(value) == value else str(value)

    for amino_acid_index, amino_acid in enumerate(protein[:-1]):
        if amino_acid != '*':
            continue
        sequence_index = offset + (amino_acid_index * 3)
        if reverse:
            codon_end = max(query_start, query_end) - sequence_index
            coordinates.append(f'{coordinate_text(codon_end - 2)}-{coordinate_text(codon_end)}')
        else:
            codon_start = min(query_start, query_end) + sequence_index
            coordinates.append(f'{coordinate_text(codon_start)}-{coordinate_text(codon_start + 2)}')
    return coordinates


def _gene_internal_stop_evidence(record, protein):
    """Return query/reference coordinates and codons for internal stops."""
    sequence = _gene_sequence(
        record, 'query_sequence', 'qseq', 'query_alignment'
    ).replace('-', '')
    subject_sequence = _gene_sequence(
        record, 'subject_sequence', 'sseq', 'subject_alignment'
    ).replace('-', '')
    if not sequence or not protein:
        return []

    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    if None in (query_start, query_end, subject_start, subject_end):
        return []

    reverse = query_end < query_start
    if reverse:
        sequence = _gene_reverse_complement(sequence)
    if subject_end < subject_start and subject_sequence:
        subject_sequence = _gene_reverse_complement(subject_sequence)

    subject_interval = _gene_subject_interval(record)
    offset = int(subject_interval[0] - 1) % 3 if subject_interval else 0

    def coordinate_text(value):
        return str(int(value)) if int(value) == value else str(value)

    evidence = []
    for amino_acid_index, amino_acid in enumerate(protein[:-1]):
        if amino_acid != '*':
            continue
        sequence_index = offset + (amino_acid_index * 3)
        query_codon = sequence[sequence_index:sequence_index + 3]
        subject_codon = subject_sequence[
            sequence_index:sequence_index + 3
        ] if subject_sequence else ''
        if reverse:
            query_codon_end = max(query_start, query_end) - sequence_index
            query_location = f'{coordinate_text(query_codon_end - 2)}-{coordinate_text(query_codon_end)}'
        else:
            query_codon_start = min(query_start, query_end) + sequence_index
            query_location = f'{coordinate_text(query_codon_start)}-{coordinate_text(query_codon_start + 2)}'
        subject_codon_start = (
            min(subject_start, subject_end) + sequence_index
        )
        subject_location = f'{coordinate_text(subject_codon_start)}-{coordinate_text(subject_codon_start + 2)}'
        item = {
            'query_location': query_location,
            'subject_location': subject_location,
        }
        if query_codon and subject_codon:
            item['codon_change'] = f'{subject_codon} -> {query_codon}'
        evidence.append(item)
    return evidence


def _gene_at_contig_end(record):
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    query_length = _gene_number(_gene_value(
        record, 'query_length', 'qlen', 'contig_length'
    ))
    return query_length is not None and (
        (query_start is not None and query_start <= 3) or
        (query_end is not None and query_length - query_end < 3)
    )


def _gene_query_is_reverse(record):
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    if query_start is None or query_end is None:
        return None
    return query_end < query_start


def _gene_alignment_frame(record):
    """Return the query/reference phase for a nucleotide alignment."""
    query_start = _gene_number(_gene_value(
        record, 'query_start', 'qstart', 'contig_start'
    ))
    query_end = _gene_number(_gene_value(
        record, 'query_end', 'qend', 'contig_end'
    ))
    subject_start = _gene_number(_gene_value(
        record, 'subject_start', 'sstart', 'target_start'
    ))
    subject_end = _gene_number(_gene_value(
        record, 'subject_end', 'send', 'target_end'
    ))
    if None in (query_start, query_end, subject_start, subject_end):
        return None
    query_anchor = query_start if query_end >= query_start else query_end
    subject_anchor = subject_start if subject_end >= subject_start else subject_end
    return int((query_anchor - subject_anchor) % 3)


def _gene_subject_gap(first, second):
    """Return the unaligned gap between disjoint reference intervals."""
    first_interval = _gene_subject_interval(first)
    second_interval = _gene_subject_interval(second)
    if first_interval is None or second_interval is None:
        return None
    if first_interval[1] < second_interval[0]:
        return second_interval[0] - first_interval[1] - 1
    if second_interval[1] < first_interval[0]:
        return first_interval[0] - second_interval[1] - 1
    # Overlapping alignments are not consecutive reference fragments. They
    # commonly occur when BLAST reports a shorter and a full-length hit.
    return None


def _gene_detail_hit(record):
    subject_interval = _gene_subject_interval(record)
    query_interval = _gene_query_interval(record)
    subject_length = _gene_number(_gene_value(
        record, 'subject_length', 'slen', 'target_length'
    ))
    return {
        'query': _gene_query_key(_gene_value(
            record, 'query', 'Query', 'query_id', 'qseqid'
        )),
        'target': str(_gene_value(
            record, 'target', 'Target', 'subject_id', 'sseqid'
        ) or ''),
        'identity': _gene_percent_match(record),
        'coverage': round(_gene_hit_quality(record)[0] * 100, 2),
        'subject_start': subject_interval[0] if subject_interval else None,
        'subject_end': subject_interval[1] if subject_interval else None,
        'subject_length': subject_length,
        'query_start': query_interval[0] if query_interval else None,
        'query_end': query_interval[1] if query_interval else None,
        'orientation': 'reverse' if _gene_query_is_reverse(record) else 'forward',
        'bit_score': _gene_bitscore(record),
    }


def _is_salmonella_gene_target(target):
    """Return whether a gene reference is invA or stn."""
    normalized = str(target or '').strip().lower()
    return (
        normalized == 'inva' or normalized.endswith('_inva') or
        normalized == 'stn' or normalized.endswith('_stn') or
        'm90846' in normalized or 'l16014' in normalized
    )


def _gene_target_allowed(target, allowed_targets):
    normalized = str(target or '').strip().lower()
    if (
        set(str(allowed).strip().lower() for allowed in allowed_targets) ==
        set(('inva', 'stn'))
    ):
        return _is_salmonella_gene_target(target)
    return any(
        normalized == str(allowed).strip().lower() or
        normalized.endswith('_' + str(allowed).strip().lower())
        for allowed in allowed_targets
    )


def categorize_gene_hit_details(records, identity_cutoff=90,
                                allowed_targets=None):
    """Return gene-specific evidence for each assigned category."""
    grouped = {}
    for record in records or []:
        query = _gene_query_key(_gene_value(
            record, 'query', 'Query', 'query_id', 'qseqid'
        ))
        if query and (
            identity_cutoff is None or
            _gene_identity_passes_cutoff(record, identity_cutoff)):
            report = _gene_report_key(_gene_value(record, 'report'))
            grouped.setdefault(report or query, {}).setdefault(
                query, []
            ).append(record)

    details = {}
    for report, query_groups in grouped.items():
        targets = {}
        for query, hits in query_groups.items():
            for hit in hits:
                target = str(_gene_value(
                    hit, 'target', 'Target', 'subject_id', 'sseqid'
                ) or '').strip()
                if not target:
                    continue
                if (
                    allowed_targets is not None and
                    not _gene_target_allowed(target, allowed_targets)
                ):
                    continue
                targets.setdefault(target, {}).setdefault(
                    query, []
                ).append(hit)

        report_details = {}
        for target, contig_hits in targets.items():
            best_by_contig = {
                query: max(hits, key=_gene_hit_quality)
                for query, hits in contig_hits.items()
            }
            best_hit = max(
                best_by_contig.values(), key=_gene_hit_quality
            )
            protein, subject_end = _gene_translation(best_hit)
            subject_interval = _gene_subject_interval(best_hit)
            subject_length = _gene_number(_gene_value(
                best_hit,
                'subject_length',
                'slen',
                'target_length'
            ))

            terminal_reference = (
                subject_interval is not None and
                subject_length is not None and
                subject_interval[0] <= 1 and
                subject_end is not None and
                subject_end >= subject_length
            )

            evidence = []

            has_internal_stop = bool(
                protein and (
                    '*' in protein[:-1] or
                    (
                        protein.endswith('*') and
                        not terminal_reference
                    )
                )
            )

            if has_internal_stop:
                stop_coordinates = _gene_internal_stop_coordinates(
                    best_hit,
                    protein
                )
                stop_evidence = _gene_internal_stop_evidence(
                    best_hit,
                    protein
                )
                evidence.append({
                    'category': 'INTERNAL_STOP',
                    'reason': (
                        'Internal stop codon(s) encountered at nucleotide '
                        '{0}.'.format(', '.join(stop_coordinates))
                        if stop_coordinates else
                        'Translated query contains a premature stop codon.'
                    ),
                    'hit': _gene_detail_hit(best_hit),
                    'terminal_reference': terminal_reference,
                    'stop_coordinates': stop_coordinates,
                    'stop_evidence': stop_evidence
                })

            reference_terminal_codon, query_terminal_codon = (
                _gene_terminal_aligned_codons(best_hit)
            )
            reference_has_terminal_stop = (
                reference_terminal_codon in ('TAA', 'TAG', 'TGA')
            )
            query_has_terminal_stop = (
                query_terminal_codon in ('TAA', 'TAG', 'TGA')
            )

            if (
                terminal_reference and
                reference_has_terminal_stop and
                query_terminal_codon is not None and
                not query_has_terminal_stop
            ):
                evidence.append({
                    'category': 'EXTENDED',
                    'reason': (
                        f'The complete reference ends in stop codon {reference_terminal_codon}, but '
                        f'the corresponding query codon is {query_terminal_codon}.'
                    ),
                    'hit': _gene_detail_hit(best_hit),
                    'terminal_reference': True,
                    'reference_terminal_codon': (
                        reference_terminal_codon
                    ),
                    'query_terminal_codon': query_terminal_codon
                })

            for hit in best_by_contig.values():
                hit_interval = _gene_subject_interval(hit)
                hit_subject_length = _gene_number(_gene_value(
                    hit,
                    'subject_length',
                    'slen',
                    'target_length'
                ))
                if (
                    hit_interval is not None and
                    hit_subject_length is not None and
                    hit_interval[1] < hit_subject_length and
                    _gene_at_contig_end(hit)
                ):
                    evidence.append({
                        'category': 'PARTIAL_CONTIG_END',
                        'reason': (
                            'Reference hit ends before the target terminus '
                            'at a contig boundary.'
                        ),
                        'hit': _gene_detail_hit(hit)
                    })

            contig_hits_list = list(best_by_contig.values())
            for index, first in enumerate(contig_hits_list):
                for second in contig_hits_list[index + 1:]:
                    gap = _gene_subject_gap(first, second)
                    if (
                        gap is not None and gap < 10 and
                        _gene_query_is_reverse(first) is not None and
                        _gene_query_is_reverse(first) ==
                        _gene_query_is_reverse(second) and
                        _gene_alignment_frame(first) is not None and
                        _gene_alignment_frame(second) is not None and
                        _gene_alignment_frame(first) !=
                        _gene_alignment_frame(second)
                    ):
                        evidence.append({
                            'category': 'FRAME_SHIFT',
                            'reason': (
                                'Adjacent reference fragments have different '
                                'inferred reading frames.'
                            ),
                            'gap': gap,
                            'first_hit': _gene_detail_hit(first),
                            'second_hit': _gene_detail_hit(second)
                        })

            if evidence:
                report_details[target] = evidence

        if report_details:
            details[report] = report_details

    return details


def categorize_gene_hits(records, allowed_targets=None, identity_cutoff=90):
    """Return the assigned GeneSeekr categories for each report.

    Detailed classification is performed by
    ``categorize_gene_hit_details`` so that the category summary and
    supporting evidence always use identical rules.

    Args:
        records (list): Normalized GeneSeekr or BLAST result records.
        allowed_targets (iterable): Optional target-name restriction.
        identity_cutoff (float): Minimum identity percentage.

    Returns:
        dict: Report names mapped to sorted category lists.
    """
    details = categorize_gene_hit_details(
        records=records,
        identity_cutoff=identity_cutoff,
        allowed_targets=allowed_targets,
    )

    result = {}

    for report, targets in details.items():
        categories = set()

        for evidence_items in targets.values():
            for evidence in evidence_items:
                category = evidence.get("category")

                if category:
                    categories.add(category)

        if categories:
            result[report] = sorted(categories)

    return result


def filter_report_by_gene_categories(report_text, categories, selected):
    """Remove categorized strains from the JSON report used for summaries."""
    try:
        report = json.loads(report_text or '{}')
    except (TypeError, ValueError):
        return report_text
    selected = set(selected or [])
    excluded = {
        query for query, values in (categories or {}).items()
        if selected.intersection(values)
    }
    if not excluded:
        return report_text

    def matches(seqid, query):
        seqid = _gene_query_key(seqid).lower()
        query = _gene_query_key(query).lower()
        return seqid == query or seqid in query or query in seqid

    for primer_data in report.values():
        if not isinstance(primer_data, dict):
            continue
        for panel_data in primer_data.values():
            if not isinstance(panel_data, dict):
                continue
            for seqid in list(panel_data):
                if any(matches(seqid, query) for query in excluded):
                    panel_data.pop(seqid, None)
    return json.dumps(report, sort_keys=True, indent=4, separators=(',', ': '))


def gene_exclusion_note(selected, categories):
    """Build the persisted explanation shown with filtered summaries."""
    selected = sorted(
        set(selected or []), key=lambda value: str(value or '')
    )
    excluded = sorted(
        [
            query for query, values in (categories or {}).items()
            if set(values).intersection(selected)
        ],
        key=lambda value: str(value or '')
    )
    if not selected or not excluded:
        return ''
    return (
        'Gene-analysis exclusions: {0} categor{1} selected; {2} strain{3} '
        'removed from summary totals and model results. Categorized strains '
        'remain in the Excel report.'.format(
            ', '.join(selected),
            'y' if len(selected) == 1 else 'ies',
            len(excluded),
            '' if len(excluded) == 1 else 's',
        )
    )


