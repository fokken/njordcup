"""Deterministic evidence checks around model SARIF assessments."""
from copy import deepcopy

ADJUDICATION_VERSION = 2


def adjudicate(assessments, seeds, findings, lookup, loaded, limitations):
    candidates = {s["id"]: s for s in seeds}
    output = []
    for proposed in assessments:
        assessment = deepcopy(proposed)
        seed = candidates[assessment["result_id"]]
        errors = list(limitations)
        if assessment["rule_id"] != seed.get("rule_id", "unknown"):
            errors.append("Assessment rule does not match the scanner rule")
        if not assessment["scanner_claim"].strip() or not assessment["reason"].strip():
            errors.append("Missing scanner claim or claim-specific explanation")
        valid = []
        for citation in assessment["evidence"]:
            path, line = citation["path"], citation["line"]
            quote = citation["quote"].splitlines()
            end = line + len(quote) - 1
            ranges = [lookup.chunks[c] for c in loaded if lookup.chunks[c]["path"] == path]
            supplied = all(any(c["start"] <= n <= c["end"] for c in ranges) for n in range(line, end + 1))
            if (line < 1 or not quote or not citation["quote"].strip() or not citation["explanation"].strip()
                    or lookup.lines.get(path, [])[line - 1:end] != quote or not supplied):
                errors.append("Evidence quote/location is invalid or was not supplied to the model")
            else:
                valid.append((citation, end))
        status = assessment["status"]
        if status != "inconclusive":
            if assessment["missing_context"]:
                errors.append("Assessment reports missing context")
            if not any(c["role"] == "reported_location" and c["path"] == seed["path"]
                       and c["line"] <= seed.get("end_line", seed["line"]) and end >= seed["line"] for c, end in valid):
                errors.append("Missing exact evidence anchored to this scanner location")
        if status == "confirmed":
            if assessment["claim_relation"] != "supports":
                errors.append("Confirmation does not explain support for the scanner claim")
            match = next((f for f in findings if f["path"] == assessment["finding_path"]
                          and f["line"] == assessment["finding_line"] and f["cwe"] == assessment["finding_cwe"]), None)
            if match is None:
                errors.append("Confirmation does not reference an exact verified finding")
            expected_cwes = seed.get("cwes", [])
            if expected_cwes and assessment["finding_cwe"] not in expected_cwes:
                errors.append("Verified finding CWE does not match scanner rule metadata")
            locations = [seed] + seed.get("related_locations", [])
            if not any(l["path"] == assessment["finding_path"] and l["line"] <= assessment["finding_line"] <= l.get("end_line", l["line"]) for l in locations):
                errors.append("Verified finding is unrelated to the scanner's reported locations")
            if not any(c["role"] == "support" and c["path"] == assessment["finding_path"]
                       and c["line"] <= assessment["finding_line"] <= end for c, end in valid):
                errors.append("Missing cited support at the verified finding")
        elif status == "not_confirmed":
            if assessment["claim_relation"] != "refutes" or not any(c["role"] == "counterevidence" for c, _ in valid):
                errors.append("Dismissal requires exact counterevidence that refutes the scanner claim")
            if assessment["finding_path"] or assessment["finding_line"] or assessment["finding_cwe"]:
                errors.append("Dismissal unexpectedly references a confirmed finding")
        assessment["proposed_status"] = status
        assessment["validation_errors"] = list(dict.fromkeys(errors))
        assessment["adjudication_version"] = ADJUDICATION_VERSION
        if errors:
            assessment.update(status="inconclusive", reason="; ".join(assessment["validation_errors"]),
                              finding_path="", finding_line=0, finding_cwe="")
        output.append(assessment)
    return output


def current_assessment(assessment):
    """Older decisions must not silently satisfy the stronger evidence contract."""
    if assessment.get("adjudication_version") == ADJUDICATION_VERSION or assessment.get("status") == "inconclusive":
        return assessment
    return {**assessment, "status": "inconclusive", "reason": "Requires re-adjudication under the current evidence policy"}
