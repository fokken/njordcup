"""Persist review attempts and derive progress for the active source snapshot."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import tempfile
from uuid import uuid4


def save_memory(path, memory):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            json.dump(memory, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def area_progress(memory):
    from .adjudication import ADJUDICATION_VERSION
    latest = {r["area_id"]: r for r in memory.get("reviews", [])}
    return [{"id": i, "title": area["title"],
             "status": ("incomplete" if area.get("sarif_candidates") and latest[i]["report"].get("adjudication_version") != ADJUDICATION_VERSION
                        else latest[i]["report"]["status"]) if i in latest else "unreviewed",
             "attempts": sum(r["area_id"] == i for r in memory.get("reviews", [])),
             "findings": len(latest[i]["report"].get("findings", [])) if i in latest else 0}
            for i, area in enumerate(memory["overview"]["areas"], 1)]


def record_review(path, memory, area_id, report, attempt_id=None):
    attempt = {"id": attempt_id or uuid4().hex, "area_id": area_id,
               "saved_at": datetime.now(timezone.utc).isoformat(), "report": deepcopy(report)}
    reviews = memory.setdefault("reviews", [])
    position = next((i for i, a in enumerate(reviews) if a["id"] == attempt_id), None)
    if position is None:
        reviews.append(attempt)
    else:
        reviews[position] = attempt
    save_memory(path, memory)
    return attempt


def review_context(memory, area_id=None):
    context = deepcopy(memory["overview"])
    if memory.get("version") == 2 or memory.get("sarif_scans"):
        area = context["areas"][area_id - 1] if area_id else None
        context = {"summary": context["summary"][:1500], "tech_stack": context["tech_stack"][:30],
                   "unknowns": context["unknowns"][:10],
                   "selected_area": {k: area[k] for k in ("title", "reason", "features", "attack_surfaces")} if area else None}
    if memory.get("reviews"):
        # Keep history bounded in prompts; full evidence and attempts remain on disk.
        summaries, size = [], 0
        for attempt in reversed(memory["reviews"]):
            report = attempt["report"]
            summary = {"area_id": attempt["area_id"], "status": report["status"],
                       "findings": [{k: f[k] for k in ("path", "line", "title", "cwe")}
                                    for f in report.get("findings", [])]}
            cost = len(json.dumps(summary))
            if size + cost > 8000:
                break
            summaries.append(summary)
            size += cost
        context["previous_reviews"] = summaries
        context["history_note"] = "Prior model assessments, not proof. Revalidate relevant issues against source; do not suppress findings merely because they were reported before. History may be truncated."
    return context


def summarize(memory):
    """Summarize the saved snapshot, without implying that files are still unchanged."""
    progress = area_progress(memory)
    latest = {r["area_id"]: r for r in memory.get("reviews", [])}
    findings = {}
    limitations, errors = [], []
    for area_id, attempt in latest.items():
        report = attempt["report"]
        limitations.extend({"area_id": area_id, "detail": item} for item in report.get("limitations", []))
        errors.extend({"area_id": area_id, "detail": item} for item in report.get("errors", []))
        for finding in report.get("findings", []):
            key = (finding["path"], finding["line"], finding["cwe"])
            if key not in findings:
                findings[key] = {**finding, "area_ids": []}
            findings[key]["area_ids"].append(area_id)
    counts = {status: sum(a["status"] == status for a in progress) for status in ("complete", "incomplete", "unreviewed")}
    severity = {level: sum(f["severity"] == level for f in findings.values()) for level in ("critical", "high", "medium", "low")}
    attempts = memory.get("reviews", [])
    usage = {k: sum(a["report"].get("usage", {}).get(k, 0) for a in attempts)
             for k in ("calls", "cache_hits", "input_tokens", "output_tokens", "retries")}
    from .sarif import scan_summary
    scanner = scan_summary(memory)
    return {"status": "summary", "audit_status": "complete" if progress and counts["complete"] == len(progress) and not memory.get("coverage", {}).get("pages_pending") and not (scanner and scanner["counts"]["inconclusive"]) else "in_progress",
            "summary": memory["overview"]["summary"], "tech_stack": memory["overview"]["tech_stack"],
            "snapshot_fingerprint": memory["fingerprint"],
            "snapshot_note": "Saved snapshot only; current filesystem contents have not been checked. Completion covers suggested areas, not the entire repository.",
            "areas": progress, "area_counts": {"total": len(progress), **counts},
            "remaining_areas": [a["id"] for a in progress if a["status"] != "complete"],
            "findings": list(findings.values()), "findings_by_severity": severity,
            "limitations": limitations, "errors": errors, "flyover_coverage": memory.get("coverage", {}),
            "flyover_unknowns": memory["overview"].get("unknowns", []),
            "review_attempts": len(attempts), "last_review_at": attempts[-1]["saved_at"] if attempts else None,
            "review_usage_all_attempts": usage, "archived_snapshots": len(memory.get("archives", [])),
            "index_stats": memory.get("index_stats", {}),
            "area_coverage": {str(i): a["report"].get("coverage", {}) for i, a in latest.items()},
            "sarif": scanner}
