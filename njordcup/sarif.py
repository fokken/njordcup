"""Import untrusted SARIF as investigation candidates, never as confirmed issues."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from .provider import ReviewError
from .adjudication import current_assessment


def load_sarif(path, root, sources):
    raw = path.read_bytes()
    try:
        return parse_sarif(raw, path, root, sources)
    except (AttributeError, TypeError, KeyError, ValueError, RecursionError) as exc:
        raise ReviewError("Malformed SARIF input; expected correctly typed SARIF 2.1.0 inline results") from exc


def parse_sarif(raw, path, root, sources):
    document = json.loads(raw)
    if document.get("version") != "2.1.0" or not isinstance(document.get("runs"), list):
        raise ReviewError("Expected SARIF 2.1.0 with a runs array")
    scan_id = hashlib.sha256(raw).hexdigest()
    candidates = []
    for run_number, run in enumerate(document["runs"]):
        if not isinstance(run, dict) or not isinstance(run.get("results", []), list):
            raise ReviewError("SARIF runs must be objects with inline results arrays")
        if run.get("externalPropertyFileReferences", {}).get("results"):
            raise ReviewError("External SARIF result files are unsupported; import a SARIF with inline results")
        artifacts = run.get("artifacts", [])
        bases = run.get("originalUriBaseIds", {})

        def location(item):
            physical = item.get("physicalLocation", {})
            artifact = physical.get("artifactLocation", {})
            if "uri" not in artifact and isinstance(artifact.get("index"), int) and 0 <= artifact["index"] < len(artifacts):
                artifact = {**artifacts[artifact["index"]].get("location", {}), **artifact}
            uri = artifact.get("uri", "")
            base_id, seen = artifact.get("uriBaseId"), set()
            while base_id:
                if base_id in seen or base_id not in bases:
                    return None
                seen.add(base_id)
                base = bases[base_id]
                uri = urljoin(base.get("uri", ""), uri)
                base_id = base.get("uriBaseId")
            parsed = urlsplit(uri)
            if not uri or parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
                return None
            local = Path(unquote(parsed.path))
            local = (root / local).resolve() if not local.is_absolute() else local.resolve()
            if not local.is_relative_to(root):
                return None
            name = local.relative_to(root).as_posix()
            region = physical.get("region", {})
            line = region.get("startLine", 1)
            end = region.get("endLine", line)
            if name not in sources or type(line) is not int or type(end) is not int or line < 1 or end < line or end > len(sources[name].splitlines()):
                return None
            return {"path": name, "line": line, "end_line": end}

        for result_number, result in enumerate(run.get("results", [])):
            candidate_id = f"{scan_id[:12]}:{run_number}:{result_number}"
            locations = result.get("locations", [])
            primary = location(locations[0]) if locations else None
            related = [location(l) for l in result.get("relatedLocations", [])]
            for flow in result.get("codeFlows", []):
                for thread in flow.get("threadFlows", []):
                    related.extend(location(l.get("location", {})) for l in thread.get("locations", []))
            rule_id = result.get("ruleId")
            rules = run.get("tool", {}).get("driver", {}).get("rules", [])
            if not rule_id:
                number = result.get("ruleIndex")
                rule_id = rules[number].get("id") if type(number) is int and 0 <= number < len(rules) else "unknown"
            rule = next((r for r in rules if r.get("id") == rule_id), {})
            cwes = sorted(set(re.findall(r"CWE-\d+", json.dumps(rule.get("properties", {})), re.I)))
            message = result.get("message", {})
            text = message.get("text", message.get("markdown", ""))
            description = rule.get("fullDescription", rule.get("shortDescription", {})).get("text", "")
            if not isinstance(text, str) or not isinstance(description, str):
                raise ReviewError("SARIF messages and rule descriptions must be strings")
            candidates.append({"id": candidate_id, "rule_id": str(rule_id), "level": result.get("level", "warning"),
                               "message": text,
                               "path": primary["path"] if primary else "", "line": primary["line"] if primary else 0,
                               "end_line": primary["end_line"] if primary else 0,
                               "related_locations": [l for l in related if l],
                               "unresolved_related_locations": sum(l is None for l in related),
                               "cwes": [c.upper() for c in cwes],
                               "rule_description": description,
                               "location_status": "resolved" if primary else "unresolved",
                               "location_note": "" if primary else "Location is absent, outside the repository, excluded, or has an invalid line range",
                               "suppressed": bool(result.get("suppressions")), "baseline_state": result.get("baselineState", "")})
    return {"id": scan_id, "source": str(path), "candidates": candidates}


def import_scan(memory, scan, index):
    """Group every located result into an explicitly selectable scanner investigation."""
    existing = memory.setdefault("sarif_scans", {})
    if scan["id"] in existing:
        # Reimport enriches legacy candidates with rule metadata without changing area IDs.
        candidates = {c["id"]: c for c in scan["candidates"]}
        for area_id in existing[scan["id"]]["area_ids"]:
            area = memory["overview"]["areas"][area_id - 1]
            area["sarif_candidates"] = [candidates[c["id"]] for c in area["sarif_candidates"]]
        existing[scan["id"]].update(scan)
        memory["active_sarif_scan"] = scan["id"]
        return
    groups = {}
    for candidate in scan["candidates"]:
        if candidate["location_status"] != "resolved":
            continue
        component = index["files"][candidate["path"]]["component"]
        groups.setdefault((component, candidate["rule_id"]), []).append(candidate)
    area_ids = []
    for (component, rule), candidates in sorted(groups.items()):
        for offset in range(0, len(candidates), 10):
            group = candidates[offset:offset + 10]
            area = {"title": f"Semgrep: {rule} ({component}, group {offset // 10 + 1})", "reason": f"Investigate {len(group)} scanner hypotheses",
                    "features": [], "attack_surfaces": [rule], "paths": sorted({c["path"] for c in group}),
                    "sarif_candidates": group, "scan_id": scan["id"]}
            memory["overview"]["areas"].append(area)
            area_ids.append(len(memory["overview"]["areas"]))
    existing[scan["id"]] = {**scan, "area_ids": area_ids}
    memory["active_sarif_scan"] = scan["id"]


def scan_summary(memory):
    scan = memory.get("sarif_scans", {}).get(memory.get("active_sarif_scan"))
    if not scan:
        return None
    latest = {a["area_id"]: a["report"] for a in memory.get("reviews", [])}
    assessments = {a["result_id"]: a for i in scan["area_ids"] for a in latest.get(i, {}).get("sarif_assessments", [])}
    rows = []
    for candidate in scan["candidates"]:
        assessment = assessments.get(candidate["id"], {"result_id": candidate["id"], "status": "inconclusive",
                                    "reason": candidate["location_note"] or "Investigation pending"})
        if candidate["id"] in assessments:
            assessment = current_assessment(assessment)
        rows.append({"rule_id": candidate["rule_id"], "path": candidate["path"], "line": candidate["line"], **assessment})
    return {"scan_id": scan["id"], "total": len(rows), "results": rows,
            "counts": {status: sum(r["status"] == status for r in rows) for status in ("confirmed", "not_confirmed", "inconclusive")}}
