import argparse
import json
import os
from pathlib import Path
import sys

from .agent import review
from .provider import OpenAIProvider, ReviewError
from .flyover import flyover, read_memory
from .repository import discover
from .memory import area_progress, record_review, review_context, summarize
from .memory import save_memory
from .index import build_index, CodeIndex
from .sarif import load_sarif, import_scan, scan_summary


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(prog="njordcup", description="Review code for evidence-backed security issues")
    parser.add_argument("repository", type=Path, nargs="?", default=Path.cwd())
    parser.add_argument("--base", help="Review changed working-tree files relative to this Git commit")
    parser.add_argument("--model", default=os.getenv("REVIEW_MODEL"))
    parser.add_argument("--base-url", default=os.getenv("REVIEW_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--api-key-env", default=os.getenv("REVIEW_API_KEY_ENV", "OPENAI_API_KEY"))
    parser.add_argument("--output-mode", choices=["json_schema", "json_object", "prompt"], default=os.getenv("REVIEW_OUTPUT_MODE", "json_schema"))
    parser.add_argument("--max-calls", type=positive, default=20)
    parser.add_argument("--max-tokens", type=positive, default=6000)
    parser.add_argument("--max-input-chars", type=positive, default=80000, help="Hard request character cap; not a tokenizer-based token limit")
    parser.add_argument("--batch-chars", type=positive, default=24000)
    parser.add_argument("--context-chars", type=positive, default=24000)
    parser.add_argument("--max-file-bytes", type=positive, default=2000000)
    parser.add_argument("--context-rounds", type=positive, default=3)
    parser.add_argument("--index-only", action="store_true", help="Build local component/symbol index without model calls")
    parser.add_argument("--sarif", type=Path, help="Import SARIF 2.1.0 scanner results as investigations")
    parser.add_argument("--investigate-all", action="store_true", help="Investigate every result in the imported SARIF scan, resuming unfinished work")
    parser.add_argument("--rerun", action="store_true", help="Start selected reviews afresh instead of resuming checkpoints")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--cache", type=Path, help="Opt-in local response cache directory")
    parser.add_argument("--output", type=Path, help="Write JSON report here instead of stdout")
    parser.add_argument("--dry-run", action="store_true", help="List review scope without contacting a model")
    parser.add_argument("--memory", type=Path, help="Memory file (default: REPO/.njordcup/memory.json)")
    parser.add_argument("--refresh-memory", action="store_true", help="Regenerate the architectural flyover")
    parser.add_argument("--area", type=positive, help="Approve review of a numbered area from saved memory")
    parser.add_argument("--flyover-only", action="store_true", help="Save the flyover without prompting or reviewing")
    parser.add_argument("--summary", action="store_true", help="Summarize saved audit progress without model calls")
    args = parser.parse_args(argv)
    root = args.repository.resolve()
    if not root.is_dir():
        parser.error("repository must be a directory")
    if not args.dry_run and not args.summary and not args.index_only and not args.model:
        parser.error("specify --model or REVIEW_MODEL")
    if args.area and (args.flyover_only or args.refresh_memory):
        parser.error("--area cannot be combined with --flyover-only or --refresh-memory")
    if args.summary and (args.area or args.flyover_only or args.refresh_memory or args.dry_run):
        parser.error("--summary cannot be combined with review or flyover actions")
    if args.investigate_all and (args.area or args.flyover_only or args.summary or args.dry_run or args.index_only):
        parser.error("--investigate-all cannot be combined with other action selectors")
    try:
        default_memory = root / ".njordcup" / "memory.json"
        legacy_memory = root / ".security-review" / "memory.json"
        if not default_memory.exists() and legacy_memory.is_file():
            default_memory = legacy_memory
        memory_path = (args.memory or default_memory).resolve()
        if args.output and args.output.resolve() == memory_path:
            raise ReviewError("--output must differ from --memory to preserve audit history")
        if args.summary:
            if not memory_path.is_file():
                raise ReviewError("No saved audit memory; run a flyover first")
            report = summarize(json.loads(memory_path.read_text()))
            report["memory_path"] = str(memory_path)
            rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
            if args.output:
                args.output.write_text(rendered)
            else:
                sys.stdout.write(rendered)
            return 0
        index_path = memory_path.with_name(memory_path.stem + ".index.json")
        if args.output and args.output.resolve() == index_path:
            raise ReviewError("--output must differ from the local index path")
        exclusions = list(args.exclude)
        for artifact in (memory_path, index_path, args.output, args.cache, args.sarif):
            if artifact is not None and artifact.resolve().is_relative_to(root):
                relative = artifact.resolve().relative_to(root).as_posix()
                exclusions.extend([relative, relative + "/*"])
        sources, targets, skipped = discover(root, args.base, exclusions, args.max_file_bytes)
        previous_index = json.loads(index_path.read_text()) if index_path.is_file() else None
        repository_index = build_index(sources, targets, previous_index, chunk_chars=max(32, args.batch_chars // 2 - 200))
        if not args.dry_run:
            save_memory(index_path, repository_index)
        if args.index_only:
            rendered = json.dumps({"status": "indexed", "index_path": str(index_path), "stats": repository_index["stats"],
                                   "components": repository_index["components"], "skipped": skipped}, indent=2) + "\n"
            if args.output:
                args.output.write_text(rendered)
            else:
                sys.stdout.write(rendered)
            return 0
        if args.dry_run:
            report = {"status": "dry_run", "targets": targets, "skipped": skipped,
                      "source_characters": sum(len(sources[p]) for p in targets)}
        else:
            provider = OpenAIProvider(args.model, args.max_calls, args.cache, args.base_url,
                                      args.api_key_env, args.output_mode, args.max_tokens, args.max_input_chars)
            if not targets and not args.sarif and not args.investigate_all:
                report = {"status": "no_targets", "skipped": skipped, "findings": []}
            else:
                if args.area or (args.investigate_all and not args.sarif):
                    if not memory_path.is_file():
                        raise ReviewError("Run a flyover first before selecting an area")
                    # An area number must never be reinterpreted against regenerated memory.
                    memory, reused = read_memory(memory_path, sources, targets, provider), True
                elif args.sarif:
                    from .mapping import hierarchical_flyover
                    memory, reused = hierarchical_flyover(sources, targets, provider, memory_path, repository_index,
                                                         args.refresh_memory, analyze=False)
                else:
                    memory, reused = flyover(sources, targets, provider, memory_path, args.refresh_memory, repository_index)
                if args.sarif:
                    scan = load_sarif(args.sarif, root, sources)
                    import_scan(memory, scan, repository_index)
                    save_memory(memory_path, memory)
                scanner = memory.get("sarif_scans", {}).get(memory.get("active_sarif_scan"))
                if args.investigate_all and not scanner:
                    raise ReviewError("Use --sarif FILE first or with --investigate-all")
                overview = memory["overview"]
                areas = overview["areas"]
                selected = args.area
                interactive = args.area is None and not args.flyover_only and not args.investigate_all and sys.stdin.isatty()
                completed = {a["id"] for a in area_progress(memory) if a["status"] == "complete"}
                queued = iter([i for i in scanner["area_ids"] if args.rerun or i not in completed] if args.investigate_all else [])
                session_reviews = []
                code_lookup = None
                while True:
                    if args.investigate_all:
                        selected = next(queued, None)
                    if selected is None and interactive and areas:
                        print(overview["summary"], file=sys.stderr)
                        for progress, area in zip(area_progress(memory), areas):
                            print(f"{progress['id']}. [{progress['status']}, {progress['findings']} findings] {area['title']}: {area['reason']}", file=sys.stderr)
                        print("Choose an area to review (completed areas can be rerun), or Enter to stop: ", end="", file=sys.stderr, flush=True)
                        try:
                            choice = input().strip()
                        except EOFError:
                            choice = ""
                        if choice:
                            if not choice.isdigit():
                                print("Expected an area number.", file=sys.stderr)
                                continue
                            selected = int(choice)
                    if selected is None:
                        break
                    if selected < 1 or selected > len(areas):
                        if interactive:
                            print("Area number out of range.", file=sys.stderr)
                            selected = None
                            continue
                        raise ReviewError("Area number out of range; no review started")
                    area = areas[selected - 1]
                    scoped = area["paths"] if area.get("sarif_candidates") else [p for p in targets if p in area["paths"]]
                    before = {k: getattr(provider, k, 0) for k in ("calls", "cache_hits", "input_tokens", "output_tokens")}
                    prior = next((a["report"] for a in reversed(memory.get("reviews", [])) if a["area_id"] == selected), None)
                    if args.rerun or (prior and prior["status"] == "complete"):
                        prior = None
                    attempt_id = None
                    def checkpoint(partial):
                        nonlocal attempt_id
                        saved = {**partial, "selected_area": {"id": selected, **area},
                                 "usage": {k: getattr(provider, k, 0) - value for k, value in before.items()}}
                        attempt_id = record_review(memory_path, memory, selected, saved, attempt_id)["id"]
                    if code_lookup is None:
                        code_lookup = CodeIndex(repository_index, sources)
                    report = review(sources, scoped, skipped, provider, args.batch_chars, args.context_chars, review_context(memory, selected),
                                    repository_index=repository_index, context_rounds=args.context_rounds, previous=prior,
                                    checkpoint=checkpoint, seeds=area.get("sarif_candidates"), code_lookup=code_lookup)
                    report["selected_area"] = {"id": selected, **area}
                    report["usage"] = {k: getattr(provider, k, 0) - value for k, value in before.items()}
                    attempt = record_review(memory_path, memory, selected, report, attempt_id)
                    session_reviews.append(attempt["report"])
                    if not interactive and not args.investigate_all:
                        break
                    print(f"Saved area {selected}: {report['status']}, {len(report.get('findings', []))} findings.", file=sys.stderr)
                    for finding in report.get("findings", []):
                        print(f"  {finding['severity']}: {finding['title']} ({finding['path']}:{finding['line']})", file=sys.stderr)
                    selected = None
                    if getattr(provider, "calls", 0) >= args.max_calls:
                        print("Session API call budget reached. Resume in a new invocation.", file=sys.stderr)
                        break
                if not session_reviews:
                    report = {"status": "awaiting_selection", "overview": overview,
                              "suggested_areas": [{"id": i, **a} for i, a in enumerate(areas, 1)],
                              "coverage": memory.get("coverage", {}), "skipped": skipped,
                              "next_step": "Run again with the same settings and --area NUMBER to approve a focused review"}
                elif len(session_reviews) > 1:
                    report = {"status": "incomplete" if any(r["status"] == "incomplete" for r in session_reviews) else "complete",
                              "reviews": session_reviews,
                              "findings": list({f["id"]: f for r in session_reviews for f in r.get("findings", [])}.values())}
                report["area_progress"] = area_progress(memory)
                report["sarif"] = scan_summary(memory)
                if args.investigate_all and report["sarif"]["counts"]["inconclusive"]:
                    report["status"] = "incomplete"
                elif args.investigate_all and not session_reviews:
                    report["status"] = "complete"
                report["max_request_chars"] = getattr(provider, "max_request_chars", 0)
                report["memory_path"] = str(memory_path)
                report["memory_reused"] = reused
                report["usage"] = {k: getattr(provider, k, 0) for k in ("calls", "cache_hits", "input_tokens", "output_tokens")}
        rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
        if args.output:
            args.output.write_text(rendered)
        else:
            sys.stdout.write(rendered)
        if report["status"] == "incomplete":
            return 2
        return 1 if report.get("findings") else 0
    except (OSError, ValueError, ReviewError, EOFError) as exc:
        print(f"njordcup: {exc}", file=sys.stderr)
        return 2
