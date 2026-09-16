"""Explicit, bounded reductions of optional request context. Never trim target code."""


def shrink_payload(payload):
    notes = payload.setdefault("budget_notes", [])

    def note(message):
        if message not in notes:
            notes.append(message)

    for key in ("related_files", "metadata", "architectural_memory", "previous_candidates"):
        if payload.get(key):
            payload.pop(key)
            note(f"Omitted {key} to fit request budget")
            return True
    # Flyovers explicitly sample code; maintain accurate coverage after reduction.
    samples = payload.get("samples", [])
    if samples:
        largest = max(samples, key=lambda sample: len(sample["source"]))
        if len(largest["source"]) > 256:
            largest["source"] = largest["source"][:len(largest["source"]) // 2]
            largest["truncated"] = True
        elif len(samples) > 1:
            samples.pop()
        elif len(payload.get("inventory", [])) > 1:
            keep = {sample["path"] for sample in samples}
            removable = [p for p in payload["inventory"] if p not in keep]
            if not removable:
                return False
            payload["inventory"].remove(removable[-1])
            payload["target_paths"] = [p for p in payload["target_paths"] if p in payload["inventory"]]
        else:
            return False
        coverage = payload["coverage"]
        coverage.update(sampled_files=len(samples), listed_files=len(payload["inventory"]),
                        truncated_samples=sum(s["truncated"] for s in samples),
                        omitted_files=coverage["eligible_files"] - len(samples))
        note("Flyover samples or inventory reduced to fit request budget")
        return True
    targets = set(payload.get("target_chunks", []))
    # Do not alter arbitrary provider payloads, only the review protocol.
    if targets:
        for entry in reversed(payload.get("files", [])):
            if entry["id"] not in targets:
                payload["files"].remove(entry)
                note("Requested source context omitted to fit request budget")
                return True
    return False
