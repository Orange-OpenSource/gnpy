# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# gnpy.tools.library_checks: pure checks of equipment libraries (no logging, no I/O)
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors

"""Checks on equipment libraries. Functions return issues and never log nor modify input data."""

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
import json
import math
from statistics import median
from typing import Dict, List, Iterable, Tuple

import oopt_gnpy_libyang as ly
from gnpy.tools.yang_convert_utils import dump_data
from gnpy.tools.convert_legacy_yang import legacy_to_yang, yang_to_legacy

LEGACY_TYPE = Dict[str, List | int | float | str | Dict | None]
YANG_TYPE = Dict[str, List | int | str | Dict | None]

STANDARD_MODE_ID = [
    # OpenZR+ Specifications, version 3.0, 12 September 2023 https://www.openzrplus.org/documents/
    'OpenZR+ ZR400-OFEC-16QAM',
    'OpenZR+ ZR400-OFEC-16QAM-HA',
    'OpenZR+ ZR400-OFEC-16QAM-HB',
    'OpenZR+ ZR400-OFEC-8QAM-HA',
    'OpenZR+ ZR400-OFEC-8QAM-HB',
    'OpenZR+ ZR300-OFEC-8QAM',
    'OpenZR+ ZR300-OFEC-8QAM-HA',
    'OpenZR+ ZR300-OFEC-8QAM-HB',
    'OpenZR+ ZR200-OFEC-QPSK',
    'OpenZR+ ZR200-OFEC-QPSK-HA',
    'OpenZR+ ZR200-OFEC-QPSK-HB',
    'OpenZR+ ZR100-OFEC-QPSK',
    'OpenZR+ ZR100-OFEC-QPSK-HA',
    'OpenZR+ ZR100-OFEC-QPSK-HB',
    'OIF-100GZR',
    'OIF-400GZR-0x01',
    'OIF-400GZR-0x02',
    'OIF-400GZR-0x03',
    'OIF-800GZR',
    # OpenROADM IPs in catalog https://github.com/OpenROADM/OpenROADM_MSA_Public/blob/master/model/Specifications/apidoc-operational-modes-to-catalog-15_0-optical-spec-6_0.json  # noqa E501
    'OpenROADM-OR-W-100G-SC',
    'OpenROADM-OR-W-100G-oFEC-31.6Gbd',
    'OpenROADM-OR-W-200G-oFEC-31.6Gbd',
    'OpenROADM-OR-W-200G-oFEC-63.1Gbd',
    'OpenROADM-OR-W-300G-oFEC-63.1Gbd',
    'OpenROADM-OR-W-400G-oFEC-63.1Gbd',
    'OpenROADM-OR-W-400G-oFEC-124Gbd',
    'OpenROADM-OR-W-400G-oFEC-118Gbd',
    'OpenROADM-OR-W-400G-oFEC-124Gbd_type2',
    'OpenROADM-OR-W-400G-oFEC-118Gbd_type2',
    'OpenROADM-OR-W-800G-oFEC-124Gbd',
    'OpenROADM-OR-W-800G-oFEC-118Gbd',
    'OpenROADM-OR-W-800G-oFEC-124Gbd_type2',
    'OpenROADM-OR-W-800G-oFEC-118Gbd_type2',
    'OpenROADM-OR-W-600G-oFEC-124Gbd',
    'OpenROADM-OR-W-600G-oFEC-118Gbd',
    'OpenROADM-OR-W-800G-oFEC-131GbdE',
    'OpenROADM-OR-W-800G-oFEC-131GbdM',
    'OpenROADM-OR-W-600G-oFEC-124Gbd_type2',
    'OpenROADM-OR-W-600G-oFEC-118Gbd_type2',
    'OpenROADM-OR-W-800G-oFEC-131GbdE_type2',
    'OpenROADM-OR-W-800G-oFEC-131GbdM_type2',
]

LIBRARY = 'Library'
TRANSCEIVER = 'Transceiver'
AMPLIFIER = 'Edfa'

ZERO_SCORE_ISSUE_CODES: set[str] = {
    'BIT_RATE_INVALID',
}


class Axis(Enum):
    """Validation axes used to assess an equipment library."""
    FORMAT = 'format'
    CONSISTENCY = 'consistency'
    COMPLETENESS = 'completeness'


@dataclass(frozen=True)
class Issue:
    """Describe a validation issue found in a library.

    :param axis: Validation axis associated with the issue.
    :param code: Machine-readable issue code.
    :param message: Human-readable description of the issue.
    :param section: Library section where the issue was found.
    :param item: Item concerned by the issue, such as a type variety.
    :param sub_item: Sub-item concerned by the issue, such as a mode.
    :param weight: Penalty applied to the corresponding score.
    """
    axis: Axis
    code: str
    message: str
    section: str                   # 'Transceiver', 'Edfa', 'Roadm', ...
    item: str | None = None        # type_variety
    sub_item: str | None = None    # mode, ...
    weight: float = 1.0


@dataclass
class Report:
    """Collect validation issues and the number of performed checks.

    :ivar issues: Issues found during validation.
    :ivar checked: Number of checks grouped by section and validation axis.
    :ivar legacy_data: Library converted to the legacy representation, if valid.
    """
    issues: list = field(default_factory=list)
    checked: dict = field(default_factory=lambda: defaultdict(int))  # (section, axis) -> n
    legacy_data: dict | None = None

    def extend(self, section: str, axis: Axis, n_checks: int, issues: Iterable[Issue]):
        """Add validation results to the report.

        :param section: Library section associated with the checks.
        :param axis: Validation axis associated with the checks.
        :param n_checks: Number of checks performed.
        :param issues: Issues produced by the checks.
        :return: None.
        """
        self.checked[(section, axis)] += n_checks
        self.issues.extend(issues)

    @property
    def sections(self) -> List[str]:
        """Return the sections containing at least one check.

        :return: Sorted list of section names.
        """
        return sorted({s[0] for s in self.checked})


@dataclass(frozen=True)
class ModeEntry:
    """Store the values required for equalization-offset checks.

    :param transceiver: Transceiver type variety.
    :param mode: Mode identifier.
    :param baud_rate: Mode baud rate in symbols per second.
    :param min_spacing: Minimum channel spacing in hertz.
    :param offset: Equalization offset in decibels.
    """
    transceiver: str
    mode: str
    baud_rate: float
    min_spacing: float
    offset: float | None


WEIGHTS = {Axis.FORMAT: 0.4, Axis.CONSISTENCY: 0.4, Axis.COMPLETENESS: 0.2}


# ----------------------------------------------------------------------
# Format (YANG)
# ----------------------------------------------------------------------
def check_yang(data: YANG_TYPE | LEGACY_TYPE) -> tuple[LEGACY_TYPE | None, list[Issue]]:
    """Validate library data against the YANG model.

    The function first checks RFC7951/YANG-formatted data. If that fails,
    it attempts to convert legacy-formatted data to the YANG representation.

    :param data: Library data to validate.
    :return: A tuple containing the converted legacy data, if available,
        and the list of format issues.
    """
    try:
        return yang_to_legacy(json.loads(dump_data(data))), []
    except ly.Error:
        pass
    try:
        legacy = yang_to_legacy(legacy_to_yang(data))
        return legacy, [Issue(Axis.FORMAT, 'NOT_RFC7951',
                              'Data does not conform to RFC7951 but is compliant with the YANG model. '
                              'Please use YANG formatted data compliant with RFC7951.', section=LIBRARY,
                              weight=0.3)]
    except ly.Error as err:
        return None, [Issue(Axis.FORMAT, 'YANG_INVALID',
                            f'Library does not comply to YANG model: {err}', section=LIBRARY)]


# ----------------------------------------------------------------------
# Mode checks
# ----------------------------------------------------------------------
def _is_valid_bit_rate(value: float) -> bool:
    """Return whether a bit rate is a valid multiple of 50 Gbit/s.

    :param value: Bit rate in bit/s.
    :return: ``True`` if the value is a valid bit rate, otherwise ``False``.
    """
    ratio = value / 50e9
    return ratio >= 1 and math.isclose(ratio, round(ratio), abs_tol=1e-9)


def check_mode(mode: LEGACY_TYPE, trx_name: str) -> Tuple[int, List[Issue], int, List[Issue]]:
    """Check consistency and completeness of one transceiver mode.

    :param mode: Transceiver mode data.
    :param trx_name: Transceiver type variety.
    :return: A tuple containing, in order, the number of consistency checks,
        consistency issues, completeness checks, and completeness issues.
    """
    name = mode['format']
    cons, comp = [], []
    n_cons = n_comp = 0

    # bit_rate
    n_cons += 1
    try:
        if not _is_valid_bit_rate(float(mode['bit_rate'])):
            cons.append(Issue(Axis.CONSISTENCY, 'BIT_RATE_INVALID',
                              f'bit_rate {mode["bit_rate"]} is not a multiple of 50e9 bit/s',
                              TRANSCEIVER,
                              trx_name, name, weight=0.5))
    except (KeyError, TypeError, ValueError):
        cons.append(Issue(Axis.CONSISTENCY, 'BIT_RATE_INVALID', 'bit_rate missing or not numeric',
                          TRANSCEIVER, trx_name, name, weight=0.5))

    # penalties
    n_comp += 1
    penalties = mode.get('penalties')
    if not penalties:
        comp.append(Issue(Axis.COMPLETENESS, 'PENALTIES_MISSING', 'Missing penalties', TRANSCEIVER, trx_name, name))
    else:
        for key in ('chromatic_dispersion', 'pmd', 'pdl'):
            n_comp += 1
            if not any(key in elem for elem in penalties):
                comp.append(Issue(Axis.COMPLETENESS, 'PENALTY_KIND_MISSING',
                                  f'Missing {key} impairment in penalties', TRANSCEIVER, trx_name, name, weight=0.5))

    # equalization offset presence
    n_comp += 1
    if mode.get('equalization_offset_db') is None:
        comp.append(Issue(Axis.COMPLETENESS, 'OFFSET_MISSING', 'Missing equalization_offset_db',
                          TRANSCEIVER, trx_name, name, weight=0.5))

    # standard / proprietary: informative only, no penalty
    names = [name] + list(mode.get('other_name', []))
    if not any(n in STANDARD_MODE_ID for n in names):
        comp.append(Issue(Axis.COMPLETENESS, 'PROPRIETARY_MODE', 'Proprietary mode only',
                          TRANSCEIVER, trx_name, name, weight=0.0))
    return n_cons, cons, n_comp, comp


# ----------------------------------------------------------------------
# Offset consistency
# ----------------------------------------------------------------------
def _build_clusters(entries: List[ModeEntry], baud_tol: float) -> list[list[ModeEntry]]:
    """Group mode entries by spacing and close baud rate.

    :param entries: Mode entries to group.
    :param baud_tol: Maximum baud-rate difference within one cluster.
    :return: List of mode-entry clusters.
    """
    by_spacing = defaultdict(list)
    for entry in entries:
        by_spacing[entry.min_spacing].append(entry)
    clusters = []
    for group in by_spacing.values():
        group.sort(key=lambda e: e.baud_rate)
        current = []
        for entry in group:
            if current and entry.baud_rate - current[0].baud_rate <= baud_tol:
                current.append(entry)
            else:
                if current:
                    clusters.append(current)
                current = [entry]
        if current:
            clusters.append(current)
    return clusters


def check_equalization_offset_consistency(
        entries: List[ModeEntry], reference: ModeEntry | None = None, baud_tol: float = 5e9, offset_tol: float = 0.2,
        proportionality_tol: float = 0.75) -> tuple[int, list[Issue]]:
    """Check equalization offsets across transceiver modes.

    Two rules are checked:

    * offsets must be consistent within a cluster of modes sharing the same
      minimum spacing and having close baud rates;
    * cluster offsets must be proportional to the reference channel, using
      either the spacing ratio or the baud-rate ratio.

    If all modes are without an offset this check is skipped.

    :param entries: Mode entries to validate.
    :param reference: Reference SI channel, if available.
    :param baud_tol: Maximum baud-rate difference within a cluster.
    :param offset_tol: Maximum allowed intra-cluster offset difference.
    :param proportionality_tol: Maximum allowed difference from the expected
        reference-based offset.
    :return: A tuple containing the number of performed checks and the issues.
    """
    # if all offsets are 0, then skip this check
    n_checks, issues = 0, []
    if not [e for e in entries if e.offset != 0 and e.mode != 'reference_mode']:
        return n_checks, issues

    # if any offset is defined, then all offsets should be consistent
    clusters = _build_clusters(entries, baud_tol)
    # ------------------------------------------------------------------
    # 1. Offsets must be consistents across modes
    # ------------------------------------------------------------------
    for cluster in clusters:
        n_checks += len(cluster)
        cluster_offset = median(e.offset for e in cluster)
        for entry in cluster:
            if abs(entry.offset - cluster_offset) > offset_tol:
                others = ', '.join(f'{e.transceiver}/{e.mode}={e.offset: .2f} dB' for e in cluster if e is not entry)
                issues.append(Issue(
                    Axis.CONSISTENCY, 'OFFSET_INCONSISTENT',
                    f'Spacing {entry.min_spacing * 1e-9: .2f} GHz, '
                    f'baud_rate={entry.baud_rate * 1e-9: .2f} Gbaud: offset={entry.offset: .2f} dB '
                    f'differs from cluster median {cluster_offset: .2f} dB. Cluster: {others}',
                    TRANSCEIVER, entry.transceiver, entry.mode))

    # without reference, we can not check the proportionality with respect to it.
    if reference is None or reference.offset is None:
        return n_checks, issues

    for cluster in clusters:
        baud = median(e.baud_rate for e in cluster)
        spacing = median(e.min_spacing for e in cluster)
        exp_spacing = reference.offset + 10 * math.log10(spacing / reference.min_spacing)
        exp_baud = reference.offset + 10 * math.log10(baud / reference.baud_rate)
        for entry in cluster:
            n_checks += 1
            if (abs(entry.offset - exp_spacing) > proportionality_tol
                    and abs(entry.offset - exp_baud) > proportionality_tol):
                issues.append(Issue(
                    Axis.CONSISTENCY, 'OFFSET_NOT_PROPORTIONAL',
                    f'Offset {entry.offset: .2f} dB not consistent with reference '
                    f'(baud_rate={entry.baud_rate * 1e-9: .2f} Gbaud, '
                    f'spacing={entry.min_spacing * 1e-9: .2f} GHz). '
                    f'Expected approximately {exp_spacing: .2f} dB from spacing '
                    f'or {exp_baud: .2f} dB from baud rate.',
                    TRANSCEIVER, entry.transceiver, entry.mode))
    return n_checks, issues


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def validate(data: LEGACY_TYPE | YANG_TYPE) -> Report:
    """Validate an equipment library.

    The input dictionary is not modified.

    :param data: Equipment library data already loaded in memory.
    :return: Validation report containing performed checks and issues.
    """
    report = Report()
    legacy, issues = check_yang(data)
    report.extend(LIBRARY, Axis.FORMAT, 1, issues)
    if legacy is None:
        return report
    report.legacy_data = legacy

    entries, reference = [], None
    si = (legacy.get('SI') or [None])[0]
    if si:
        reference = ModeEntry('reference_channel', 'reference_mode', float(si['baud_rate']),
                              float(si['spacing']), float(si['power_dbm']))
        entries.append(reference)

    for trx in legacy.get('Transceiver', []):
        trx_name = trx['type_variety']
        for mode in trx['mode']:
            n_cons, cons, n_comp, comp = check_mode(mode, trx_name)
            report.extend(TRANSCEIVER, Axis.CONSISTENCY, n_cons, cons)
            report.extend(TRANSCEIVER, Axis.COMPLETENESS, n_comp, comp)
            # no offset key means offset is 0dB in GNPy
            offset = mode.get('equalization_offset_db', 0)
            entries.append(ModeEntry(trx_name, mode['format'], mode['baud_rate'],
                                     mode['min_spacing'], offset))

    n_checks, issues = check_equalization_offset_consistency(entries, reference)
    report.extend(TRANSCEIVER, Axis.CONSISTENCY, n_checks, issues)
    return report


# ----------------------------------------------------------------------
# Score (pure computation on a report)
# ----------------------------------------------------------------------

SECTION_WEIGHTS = {            # weight of each section when computing the global score
    'Transceiver': 0.4,
    'Edfa': 0.2,
    'Roadm': 0.2,
    'Fiber': 0.1,
    'SI': 0.1,
}


def axis_score(report: Report, section: str, axis: Axis) -> float | None:
    """Compute the score for one section and one validation axis.

    :param report: Validation report.
    :param section: Library section to score.
    :param axis: Validation axis to score.
    :return: Score between 0 and 100, or ``None`` if no check was performed.
    """
    total = report.checked.get((section, axis), 0)
    if total == 0:
        return None
    penalty = sum(i.weight for i in report.issues
                  if i.section == section and i.axis == axis)
    return max(0.0, 100 * (1 - penalty / total))


def _weighted(scores: Dict, weights: Dict) -> float | None:
    """Compute a weighted average of available scores.

    Scores with a value of ``None`` are excluded from the calculation.

    :param scores: Scores indexed by their corresponding key.
    :param weights: Weight associated with each score key.
    :return: Weighted average, or ``None`` if no score is available.
    """
    valid = {k: s for k, s in scores.items() if s is not None}
    if not valid:
        return None
    total_w = sum(weights[k] for k in valid)
    return sum(weights[k] * s for k, s in valid.items()) / total_w


def section_score(report: Report, section: str) -> tuple[float | None, dict[Axis, float | None]]:
    """Compute the score for one library section.

    :param report: Validation report.
    :param section: Library section to score.
    :return: A tuple containing the section score and the score for each axis.
    """
    scores = {a: axis_score(report, section, a)
              for a in (Axis.CONSISTENCY, Axis.COMPLETENESS)}
    return _weighted(scores, WEIGHTS), scores


@dataclass
class Score:
    """Store global, format, and per-section validation scores.

    :param total: Global library score.
    :param format: Global format score.
    :param sections: Scores grouped by section.
    """
    total: float
    format: float | None
    sections: dict            # section -> (score, {axis: score})


def score(report: Report) -> Score:
    """Compute all scores from a validation report.

    :param report: Validation report.
    :return: Global score, format score, and per-section scores.
    """
    fmt = axis_score(report, LIBRARY, Axis.FORMAT)
    sections = {s: section_score(report, s) for s in report.sections if s != LIBRARY}

    if any(issue.code in ZERO_SCORE_ISSUE_CODES for issue in report.issues):
        return Score(0.0, fmt, sections)

    if fmt == 0:
        return Score(0.0, 0.0, sections)
    content = _weighted({s: v[0] for s, v in sections.items()},
                        {s: SECTION_WEIGHTS.get(s, 1.0) for s in sections})
    if content is None:
        return Score(fmt or 0.0, fmt, sections)
    total = (WEIGHTS[Axis.FORMAT] * (fmt if fmt is not None else 100)
             + (1 - WEIGHTS[Axis.FORMAT]) * content)
    return Score(total, fmt, sections)


def format_score(score_result: Score) -> str:
    """Format validation scores for human-readable output.

    :param score_result: Scores to format.
    :return: Multiline textual representation of the scores.
    """
    lines = [f'Score: {score_result.total: .1f} (format={score_result.format})']
    for section, (value, axes) in score_result.sections.items():
        detail = ', '.join(f'{a.value}={"n/a" if v is None else f"{v: .1f}"}' for a, v in axes.items())
        lines.append(f'  {section}: {"n/a" if value is None else f"{value: .1f}"} ({detail})')
    return '\n'.join(lines)
