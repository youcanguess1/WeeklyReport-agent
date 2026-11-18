from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.evidence import MaterialPack
from app.schemas.memory import SkillBrief
from app.schemas.review import ReviewResult
from app.schemas.report import WeeklyReportDocument, WeeklyReportDocumentDraft
from app.tools.skill_injection_tool import format_skill_briefs


_REPORT_PERIOD_PATTERN = re.compile(
    r'(?P<start>\d{4}-\d{1,2}-\d{1,2})\s*(?:\u81f3|\u5230|[-~\uff5e])\s*(?P<end>\d{4}-\d{1,2}-\d{1,2})'
)


def build_writer_context(
    *,
    material_pack: MaterialPack,
    project_name: str | None,
    template_path: Path,
    task_notes: str | None = None,
    skill_briefs: list[SkillBrief] | None = None,
    previous_report: WeeklyReportDocument | None = None,
    review_result: ReviewResult | None = None,
    revision_instructions: str | None = None,
) -> str:
    requested_name = project_name or "(task name unavailable)"
    template_text = template_path.read_text(encoding="utf-8")
    lines = [
        f"Task ID: {material_pack.task_id}",
        f"Task Name: {requested_name}",
        f"Task Notes: {task_notes.strip() if task_notes and task_notes.strip() else '(none)'}",
        f"Available Evidence Refs: {', '.join(available_evidence_refs(material_pack)) or '(none)'}",
        "",
        "Weekly Report Template:",
        template_text.strip(),
        "",
        "Relevant Skills (title and description only):",
        format_skill_briefs(skill_briefs),
        "Use get_skill_content with the exact title only when you need the full skill details.",
        "",
        "Image Materials:",
    ]
    understanding_by_id = {item.material_id: item for item in material_pack.understandings}
    for index, material in enumerate(material_pack.materials, start=1):
        understanding = understanding_by_id.get(material.material_id)
        lines.extend([
            f"[Image {index}]",
            f"Material ID: {material.material_id}",
            f"User note: {material.note or '(none)'}",
            f"Understanding: {understanding.content_summary if understanding else '(unavailable)'}",
            "",
        ])
    lines.extend(
        [
            "Output rules:",
            "1. Fill only the five weekly-report lists: completed_this_week, in_progress, risks_and_blockers, next_week_plan, and pending_confirmations.",
            "2. Use only the task name, task notes, and the supplied existing workflow/evidence materials; do not invent dates, owners, status, blockers, commitments, or outcomes.",
            "3. Report completion only when the supplied notes or materials explicitly establish that status.",
            "4. When images are supplied, treat their contents only as supplemental evidence; explicit user notes take precedence over image-based inference.",
            "5. Put unclear status or facts requiring confirmation in pending_confirmations; leave other unsupported sections empty.",
        ]
    )
    if previous_report is not None and review_result is not None:
        lines.extend(
            [
                "",
                "Previous Weekly Report JSON:",
                previous_report.model_dump_json(indent=2),
                "",
                "Review Result JSON:",
                review_result.model_dump_json(indent=2),
                '',
                'Latest User Revision Instructions:',
                revision_instructions.strip() if revision_instructions and revision_instructions.strip() else '(none)',
                "",
                "Revision rules:",
                '1. Latest User Revision Instructions are explicit factual corrections and take priority over task notes and the previous weekly report when they conflict.',
                '2. Apply every explicit correction. Do not retain an old percentage, completion state, dependency, risk, blocker, plan, or pending status that conflicts with the latest instructions.',
                '3. Use existing workflow materials only for facts not corrected by the latest instructions; do not infer facts beyond the instructions or materials.',
                '4. Preserve supported content that is not contradicted. Put unresolved ambiguity in pending_confirmations.',
                '5. Before returning JSON, check every latest instruction against all five report sections.',
            ]
        )
    return "\n".join(lines).strip()


def build_report_document(
    *,
    draft: WeeklyReportDocumentDraft,
    version: str,
    project_name: str | None,
    report_period: str | None = None,
) -> WeeklyReportDocument:
    completed = _clean_items(draft.completed_this_week)
    in_progress = _clean_items(draft.in_progress)
    risks = _clean_items(draft.risks_and_blockers)
    plans = _clean_items(draft.next_week_plan)
    confirmations = _clean_items(draft.pending_confirmations)
    if not any((completed, in_progress, risks, plans, confirmations)):
        confirmations = ["本周进度及下周安排待确认（现有资料不足以判断）。"]
    return WeeklyReportDocument(
        project_name=project_name or draft.project_name.strip(),
        version=version,
        report_period=report_period,
        completed_this_week=completed,
        in_progress=in_progress,
        risks_and_blockers=risks,
        next_week_plan=plans,
        pending_confirmations=confirmations,
        raw_markdown="",
        generated_at=datetime.now(UTC),
    )


def _clean_items(items: list[str]) -> list[str]:
    return [item.strip() for item in items if item.strip()]


def extract_report_period(task_name: str | None, task_notes: str | None) -> str | None:
    '''Return an explicitly supplied report period without inferring dates.'''

    for source in (task_name, task_notes):
        if not source:
            continue
        match = _REPORT_PERIOD_PATTERN.search(source)
        if match is not None:
            return '{} \u81f3 {}'.format(match.group('start'), match.group('end'))
    return None


def available_evidence_refs(material_pack: MaterialPack) -> list[str]:
    return [material.material_id for material in material_pack.materials]
