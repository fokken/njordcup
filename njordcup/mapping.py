"""Progressive component flyovers; every target belongs to an area before model calls."""
from copy import deepcopy
import json

from .index import affected_files
from .memory import save_memory
from .provider import ReviewError


def hierarchical_flyover(sources, targets, provider, path, index, refresh=False, analyze=True):
    from .flyover import OVERVIEW, PROMPT, fingerprint, make_payload, provider_identity, validate_overview
    old = json.loads(path.read_text()) if path.is_file() else {}
    if not analyze and not refresh and old.get("fingerprint") == fingerprint(sources, targets) and old.get("provider") == provider_identity(provider):
        return old, True
    compatible = old.get("version") == 2 and old.get("provider") == provider_identity(provider) and not refresh
    old_index = old.get("index_snapshot", {})
    affected = affected_files(old_index, index) if compatible else set(sources)
    old_areas = {a["component"]: (i, a) for i, a in enumerate(old.get("overview", {}).get("areas", []), 1) if "component" in a}
    target_set = set(targets)
    areas, component_to_id = [], {}
    for component, paths in sorted(index["components"].items()):
        selected = [p for p in paths if p in target_set]
        if not selected:
            continue
        component_to_id[component] = len(areas) + 1
        areas.append({"component": component, "title": component if component != "." else "Repository root",
                      "reason": "Component mapped locally; architectural analysis pending.",
                      "features": [], "attack_surfaces": [], "paths": selected})
    carried = []
    if compatible:
        for component, (old_id, area) in old_areas.items():
            new_id = component_to_id.get(component)
            if new_id is None or area["paths"] != areas[new_id - 1]["paths"] or affected.intersection(index["components"][component]):
                continue
            for attempt in old.get("reviews", []):
                if attempt["area_id"] != old_id:
                    continue
                dependencies = attempt["report"].get("read_dependencies", {})
                if any(index["files"].get(p, {}).get("hash") != h for p, h in dependencies.items()):
                    continue
                saved = deepcopy(attempt)
                saved["area_id"] = new_id
                saved["report"]["selected_area"] = {"id": new_id, **areas[new_id - 1]}
                carried.append(saved)
    changed = old.get("fingerprint") != fingerprint(sources, targets) or not compatible
    archives = list(old.get("archives", []))
    if old and changed:
        archives.append({k: v for k, v in old.items() if k != "archives"})
    snapshot = {"files": {p: {"hash": f["hash"]} for p, f in index["files"].items()}, "dependencies": index["dependencies"]}
    memory = {"version": 2, "fingerprint": fingerprint(sources, targets), "provider": provider_identity(provider),
              "overview": {"summary": f"{len(sources)} eligible files in {len(areas)} review components.",
                           "tech_stack": [], "dependencies": [], "areas": areas, "unknowns": []},
              "index_snapshot": snapshot, "index_stats": index["stats"], "reviews": carried,
              "archives": archives, "component_pages": {}, "coverage": {}, "mapping_errors": []}
    if compatible and not changed and old.get("sarif_scans"):
        areas.extend(deepcopy([a for a in old["overview"]["areas"] if "scan_id" in a]))
        memory["reviews"] = deepcopy(old.get("reviews", []))
        memory["sarif_scans"] = deepcopy(old["sarif_scans"])
        memory["active_sarif_scan"] = old["active_sarif_scan"]
    old_pages = old.get("component_pages", {}) if compatible else {}
    pending = []
    # Pages bound model input even when a component contains thousands of files.
    for component in component_to_id:
        paths = index["components"][component]
        for offset in range(0, len(paths), 30):
            page_paths = paths[offset:offset + 30]
            key = component + ":" + str(offset // 30)
            hashes = {p: index["files"][p]["hash"] for p in page_paths}
            previous = old_pages.get(key, {})
            if previous.get("hashes") == hashes and previous.get("status") == "complete" and not affected.intersection(page_paths):
                memory["component_pages"][key] = previous
            else:
                memory["component_pages"][key] = {"component": component, "paths": page_paths, "hashes": hashes, "status": "pending"}
                pending.append(key)

    def update():
        summaries = [p["overview"] for p in memory["component_pages"].values() if p["status"] == "complete"]
        memory["overview"]["tech_stack"] = sorted({s for overview in summaries for s in overview["tech_stack"]})
        memory["overview"]["dependencies"] = list({(d["name"], d["evidence_path"]): d for overview in summaries for d in overview["dependencies"]}.values())
        for area in areas:
            if "component" not in area:
                continue
            pages = [p for p in memory["component_pages"].values() if p["component"] == area["component"]]
            analyzed = [p for p in pages if p["status"] == "complete"]
            area["reason"] = f"Architectural pages analyzed: {len(analyzed)}/{len(pages)}. " + (analyzed[0]["overview"]["summary"][:800] if analyzed else "Analysis pending.")
            suggested = [a for p in analyzed for a in p["overview"]["areas"]]
            area["features"] = sorted({s for a in suggested for s in a["features"]})[:20]
            area["attack_surfaces"] = sorted({s for a in suggested for s in a["attack_surfaces"]})[:20]
        memory["coverage"] = {**index["stats"], "target_files": len(targets), "mapped_target_files": sum(len(a["paths"]) for a in areas),
                              "pages_total": len(memory["component_pages"]), "pages_complete": len(summaries),
                              "pages_pending": sum(p["status"] != "complete" for p in memory["component_pages"].values())}
        memory["overview"]["unknowns"] = ["Component summaries sample source; local indexing covers all eligible lines. Lexical call/import edges are incomplete, especially for dynamic dispatch."]
        if memory["coverage"]["pages_pending"]:
            memory["overview"]["unknowns"].append("Some architectural pages await analysis; rerun --flyover-only to continue.")
        save_memory(path, memory)

    update()
    if not analyze:
        return memory, False
    for key in pending:
        page = memory["component_pages"][key]
        page_sources = {p: sources[p] for p in page["paths"]}
        payload = make_payload(page_sources, page["paths"], char_budget=18000)
        payload["component"] = page["component"]
        payload["metadata"] = [{"path": p, "lines": index["files"][p]["lines"],
                                "symbols": index["files"][p]["symbols"][:15],
                                "imports": index["files"][p]["imports"][:15],
                                "signals": sorted({s for c in index["files"][p]["chunks"] for s in c["signals"]})}
                               for p in page["paths"]]
        try:
            overview = provider.ask(PROMPT + "\nThis is one page of a larger component. Summarize this page only.", payload, OVERVIEW)
            validate_overview(overview, page_sources, page["paths"])
            sampled = {s["path"] for s in payload["samples"]}
            if any(d["evidence_path"] not in sampled for d in overview["dependencies"]):
                raise ReviewError("Flyover cited dependency evidence outside sampled files")
            page.update(status="complete", overview=overview, coverage=payload["coverage"])
        except ReviewError as exc:
            memory["mapping_errors"].append(str(exc))
            update()
            break
        update()
    return memory, not pending and not changed
