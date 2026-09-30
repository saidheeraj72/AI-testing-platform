"""Merges candidates that are the same problem into one bug with several occurrences.

Same problem = same primary signal signature (method + path template +
status, normalized error text, or the same check). No semantic similarity.
"""

from __future__ import annotations

from app.schemas.bug import Analysis, Bug, Candidate, Occurrence


def deduplicate(analyzed: list[tuple[Candidate, Analysis, str, list[str]]]) -> list[Bug]:
    """analyzed: (candidate, analysis, described_by, steps_to_reproduce), in session order."""
    bugs: dict[str, Bug] = {}
    for candidate, analysis, described_by, reproduce in analyzed:
        key = candidate.primary.signature
        occurrence = Occurrence(step=candidate.step, action_seq=candidate.action_seq,
                                url=candidate.url, screenshot=candidate.screenshot)
        if key in bugs:
            bug = bugs[key]
            bug.occurrences.append(occurrence)
            bug.network.extend(n for n in candidate.network if n not in bug.network)
            bug.console.extend(c for c in candidate.console if c not in bug.console)
            continue
        bugs[key] = Bug(
            id=f"BUG-{len(bugs) + 1:03d}",
            title=analysis.title,
            severity=analysis.severity,
            category=analysis.category,
            summary=analysis.summary,
            expected=analysis.expected,
            actual=analysis.actual,
            url=candidate.url,
            signature=key,
            signals=[s.summary for s in candidate.signals],
            described_by=described_by,
            steps_to_reproduce=reproduce,
            occurrences=[occurrence],
            network=list(candidate.network),
            console=list(candidate.console),
        )
    return list(bugs.values())
