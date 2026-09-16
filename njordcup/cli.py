import argparse
import json
import os
from pathlib import Path
import sys
import math
import time

from .agent import review
from .provider import OpenAIProvider, ReviewError
from .flyover import flyover, read_memory
from .repository import discover
from .memory import area_progress, record_review, review_context, summarize
from .memory import save_memory
from .index import build_index, CodeIndex
from .sarif import load_sarif, import_scan, scan_summary
from .errors import RunStopped
from .runtime import RunControl, handle_signals


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    control = RunControl()
    with handle_signals(control):
        return run(argv, control)


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return number


def duration(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive duration")
    return number


def stop_exit(reason):
    return {"cancelled": 130, "sigterm": 143, "deadline": 124}.get(reason, 2)


def run(argv, control):
    parser = argparse.ArgumentParser(prog="njordcup", description="Review code for evidence-backed security issues")
    parser.add_argument("repository", type=Path, nargs="?", default=Path.cwd())
    parser.add_argument("--base", help="Review changed working-tree files relative to this Git commit")
    parser.add_argument("--model", default=os.getenv("REVIEW_MODEL"))
    parser.add_argument("--base-url", default=os.getenv("REVIEW_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--api-key-env", default=os.getenv("REVIEW_API_KEY_ENV", "OPENAI_API_KEY"))
    parser.add_argument("--output-mode", choices=["json_schema", "json_object", "prompt"], default=os.getenv("REVIEW_OUTPUT_MODE", "json_schema"))
    parser.add_argument("--max-calls", type=positive, default=20)
    parser.add_argument("--request-timeout", type=duration, default=120, help="Socket I/O timeout in seconds per attempt")
    parser.add_argument("--max-retries", type=nonnegative, default=2, help="Transient retries per call; each attempt counts toward --max-calls")
    parser.add_argument("--retry-base", type=duration, default=1, help="Initial retry backoff in seconds")
    parser.add_argument("--retry-max-delay", type=duration, default=30, help="Maximum retry delay including Retry-After")
    parser.add_argument("--max-seconds", type=duration, help="Cooperative time budget for this invocation")
    parser.add_argument("--max-tokens", type=positive, default=6000)
    parser.add_argument("--context-window", type=positive, help="Configured server context tokens, including output")
    parser.add_argument("--bytes-per-token", type=duration, default=1, help="Input token estimate divisor (default: conservative 1 byte/token)")
    parser.add_argument("--token-margin", type=nonnegative, default=1024, help="Reserved tokens for server formatting overhead")
    parser.add_argument("--max-input-chars", type=positive, default=80000, help="Hard request character cap; not a tokenizer-based token limit")
    parser.add_argument("--batch-chars", type=positive, default=24000)
    parser.add_argument("--context-chars", type=positive, default=24000)
    parser.add_argument("--max-file-bytes", type=positive, default=2000000)
    parser.add_argument("--context-rounds", type=positive, default=3)
    parser.add_argument("--index-only", action="store_true", help="Build local component/symbol index without model calls")
    parser.add_argument("--sarif", type=Path, help="Import SARIF 2.1.0 scanner results as investigations")
    parser.add_argument("--investigate-all", action="store_true", help="Investigate every result in the imported SARIF scan, resuming unfinished work")
    parser.add_argument("--automatic", "--auto", action="store_true", help="Audit all mapped source components without prompts; resume completed work")
    parser.add_argument("--implementation-analysis", action="store_true", help="Describe implementation and functionality in a separate saved result")
    parser.add_argument("--implementation-report", action="store_true", help="Generate an offline HTML report of saved implementation analysis")
    parser.add_argument("--implementation-file", type=Path, help="Implementation-analysis result to save or reuse")
    parser.add_argument("--report", action="store_true", help="Generate an offline HTML report from saved memory")
    parser.add_argument("--rerun", action="store_true", help="Start selected reviews afresh instead of resuming checkpoints")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--include", action="append", default=[], help="Restrict eligible files to repository-relative globs; repeatable")
    parser.add_argument("--index-mode", choices=["auto", "text"], default="auto", help="Use optional language parsing or generic text-only indexing")
    parser.add_argument("--cache", type=Path, help="Opt-in local response cache directory")
    parser.add_argument("--output", type=Path, help="Output file (HTML with report modes, otherwise JSON)")
    parser.add_argument("--dry-run", action="store_true", help="List review scope without contacting a model")
    parser.add_argument("--memory", type=Path, help="Memory file (default: REPO/.njordcup/memory.json)")
    parser.add_argument("--refresh-memory", action="store_true", help="Regenerate the architectural flyover")
    parser.add_argument("--area", type=positive, help="Approve review of a numbered area from saved memory")
    parser.add_argument("--flyover-only", action="store_true", help="Save the flyover without prompting or reviewing")
    parser.add_argument("--summary", action="store_true", help="Summarize saved audit progress without model calls")
    args = parser.parse_args(argv)
    if args.max_seconds:
        control.deadline = time.monotonic() + args.max_seconds
    root = args.repository.resolve()
    if not root.is_dir():
        parser.error("repository must be a directory")
    if not args.dry_run and not args.summary and not args.report and not args.implementation_report and not args.index_only and not args.model:
        parser.error("specify --model or REVIEW_MODEL")
    if args.area and (args.flyover_only or args.refresh_memory):
        parser.error("--area cannot be combined with --flyover-only or --refresh-memory")
    if args.summary and (args.area or args.flyover_only or args.refresh_memory or args.dry_run):
        parser.error("--summary cannot be combined with review or flyover actions")
    if args.investigate_all and (args.area or args.flyover_only or args.summary or args.dry_run or args.index_only):
        parser.error("--investigate-all cannot be combined with other action selectors")
    if args.automatic and (args.area or args.flyover_only or args.summary or args.report or args.dry_run or args.index_only or args.investigate_all or args.sarif):
        parser.error("--automatic cannot be combined with other action selectors or --sarif")
    if args.report and (args.area or args.flyover_only or args.summary or args.refresh_memory or args.dry_run or args.index_only or args.investigate_all or args.sarif):
        parser.error("--report cannot be combined with audit actions")
    if args.implementation_analysis and (args.automatic or args.report or args.area or args.summary or args.flyover_only or args.dry_run or args.index_only or args.investigate_all or args.sarif):
        parser.error("--implementation-analysis cannot be combined with other action selectors")
    if args.implementation_report and (args.implementation_analysis or args.automatic or args.report or args.area or args.summary or args.flyover_only or args.dry_run or args.index_only or args.investigate_all or args.sarif or args.refresh_memory):
        parser.error("--implementation-report cannot be combined with other action selectors")
    try:
        default_memory = root / ".njordcup" / "memory.json"
        legacy_memory = root / ".security-review" / "memory.json"
        if not default_memory.exists() and legacy_memory.is_file():
            default_memory = legacy_memory
        memory_path = (args.memory or default_memory).resolve()
        html_path = memory_path.with_name(memory_path.stem + ".report.html")
        if args.report and not args.output:
            args.output = html_path
        if args.output and args.output.resolve() == memory_path:
            raise ReviewError("--output must differ from --memory to preserve audit history")
        index_path = memory_path.with_name(memory_path.stem + ".index.json")
        if args.output and args.output.resolve() == index_path:
            raise ReviewError("--output must differ from the local index path")
        implementation_path = (args.implementation_file or memory_path.with_name(memory_path.stem + ".implementation.json")).resolve()
        if implementation_path in {memory_path, index_path, html_path}:
            raise ReviewError("Implementation results must be separate from audit memory, index, and HTML report")
        if args.output and args.output.resolve() == implementation_path and not args.implementation_analysis:
            raise ReviewError("--output must not overwrite saved implementation analysis")
        implementation_html_path = implementation_path.with_suffix(".html")
        if args.implementation_report:
            if not implementation_path.is_file():
                raise ReviewError("No saved implementation analysis; run --implementation-analysis first")
            from .reporting import write_html
            destination = args.output or implementation_html_path
            if destination.resolve() in {memory_path, index_path, implementation_path, html_path}:
                raise ReviewError("Implementation report must not overwrite audit or implementation results")
            write_html(destination, json.loads(implementation_path.read_text()), implementation=True)
            print(f"Implementation HTML report saved to {destination}")
            return 0
        if args.summary or args.report:
            if not memory_path.is_file():
                raise ReviewError("No saved audit memory; run a flyover first")
            report = summarize(json.loads(memory_path.read_text()))
            report["memory_path"] = str(memory_path)
            if args.report:
                from .reporting import write_html
                write_html(args.output, report)
                print(f"HTML report saved to {args.output}")
                return 0
            rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
            if args.output:
                args.output.write_text(rendered)
            else:
                sys.stdout.write(rendered)
            return 0
        exclusions = list(args.exclude)
        for artifact in (memory_path, index_path, html_path, implementation_path, implementation_html_path, args.output, args.cache, args.sarif):
            if artifact is not None and artifact.resolve().is_relative_to(root):
                relative = artifact.resolve().relative_to(root).as_posix()
                exclusions.extend([relative, relative + "/*"])
        sources, targets, skipped = discover(root, args.base, exclusions, args.max_file_bytes, args.include)
        control.check()
        previous_index = json.loads(index_path.read_text()) if index_path.is_file() else None
        repository_index = build_index(sources, targets, previous_index, chunk_chars=max(32, args.batch_chars // 2 - 200), index_mode=args.index_mode)
        control.check()
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
                                      args.api_key_env, args.output_mode, args.max_tokens, args.max_input_chars,
                                      request_timeout=args.request_timeout, max_retries=args.max_retries,
                                      context_window=args.context_window, bytes_per_token=args.bytes_per_token, token_margin=args.token_margin,
                                      retry_base=args.retry_base, retry_max_delay=args.retry_max_delay, control=control,
                                      on_retry=lambda event: print(f"{event['reason']}; retry {event['retry']} in {event['delay_seconds']:.1f}s", file=sys.stderr))
            from .implementation import analyze_implementation, load_implementation, review_implementation_context
            implementation = load_implementation(implementation_path, sources, targets, repository_index)
            if args.implementation_analysis:
                report = analyze_implementation(sources, targets, provider, implementation_path, repository_index,
                                                refresh=args.rerun or args.refresh_memory)
            elif not targets and not args.sarif and not args.investigate_all:
                report = {"status": "no_targets", "skipped": skipped, "findings": []}
            else:
                if args.area or (args.investigate_all and not args.sarif):
                    if not memory_path.is_file():
                        raise ReviewError("Run a flyover first before selecting an area")
                    # An area number must never be reinterpreted against regenerated memory.
                    memory, reused = read_memory(memory_path, sources, targets, provider, index_mode=args.index_mode), True
                elif args.sarif or args.automatic:
                    from .mapping import hierarchical_flyover
                    memory, reused = hierarchical_flyover(sources, targets, provider, memory_path, repository_index,
                                                         args.refresh_memory, analyze=args.automatic, implementation=implementation)
                else:
                    memory, reused = flyover(sources, targets, provider, memory_path, args.refresh_memory, repository_index, implementation=implementation)
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
                interactive = args.area is None and not args.flyover_only and not args.investigate_all and not args.automatic and sys.stdin.isatty()
                completed = {a["id"] for a in area_progress(memory) if a["status"] == "complete"}
                automatic_ids = [i for i, area in enumerate(areas, 1) if "component" in area]
                queue_ids = scanner["area_ids"] if args.investigate_all else automatic_ids if args.automatic else []
                queued = iter(i for i in queue_ids if args.rerun or i not in completed)
                notified = set()
                session_reviews = []
                code_lookup = None
                while True:
                    control.check()
                    if args.investigate_all or args.automatic:
                        selected = next(queued, None)
                    if selected is None and interactive and areas:
                        print(overview["summary"], file=sys.stderr)
                        for progress, area in zip(area_progress(memory), areas):
                            print(f"{progress['id']}. [{progress['status']}, {progress['findings']} findings] {area['title']}: {area['reason']}", file=sys.stderr)
                        print("Choose an area to review (completed areas can be rerun), or Enter to stop: ", end="", file=sys.stderr, flush=True)
                        try:
                            control.prompting = True
                            choice = input().strip()
                        except EOFError:
                            choice = ""
                        finally:
                            control.prompting = False
                        control.check()
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
                    before = {k: getattr(provider, k, 0) for k in ("calls", "cache_hits", "input_tokens", "output_tokens", "retries")}
                    prior = next((a["report"] for a in reversed(memory.get("reviews", [])) if a["area_id"] == selected), None)
                    if args.rerun or (prior and prior["status"] == "complete"):
                        prior = None
                    attempt_id = None
                    def checkpoint(partial):
                        nonlocal attempt_id
                        saved = {**partial, "selected_area": {"id": selected, **area},
                                 "usage": {k: getattr(provider, k, 0) - value for k, value in before.items()}}
                        attempt_id = record_review(memory_path, memory, selected, saved, attempt_id)["id"]
                        if args.automatic:
                            for finding in partial.get("findings", []):
                                key = (finding["path"], finding["line"], finding["cwe"])
                                if key not in notified:
                                    notified.add(key)
                                    print("Potential issue saved: " + json.dumps({k: finding[k] for k in
                                          ("severity", "title", "path", "line", "cwe")}, ensure_ascii=True),
                                          file=sys.stderr, flush=True)
                    if code_lookup is None:
                        code_lookup = CodeIndex(repository_index, sources)
                    context = review_context(memory, selected)
                    implementation_context = review_implementation_context(implementation, scoped)
                    if implementation_context:
                        context["implementation_analysis"] = implementation_context
                    report = review(sources, scoped, skipped, provider, args.batch_chars, args.context_chars, context,
                                    repository_index=repository_index, context_rounds=args.context_rounds, previous=prior,
                                    checkpoint=checkpoint, seeds=area.get("sarif_candidates"), code_lookup=code_lookup)
                    report["selected_area"] = {"id": selected, **area}
                    report["usage"] = {k: getattr(provider, k, 0) - value for k, value in before.items()}
                    attempt = record_review(memory_path, memory, selected, report, attempt_id)
                    session_reviews.append(attempt["report"])
                    if report.get("stop_reason"):
                        break
                    if not interactive and not args.investigate_all and not args.automatic:
                        break
                    print(f"Saved area {selected}: {report['status']}, {len(report.get('findings', []))} findings.", file=sys.stderr)
                    for finding in ([] if args.automatic else report.get("findings", [])):
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
                    if memory.get("mapping_errors"):
                        report.update(status="incomplete", errors=memory["mapping_errors"],
                                      next_step="Rerun --flyover-only to finish architectural analysis")
                elif len(session_reviews) > 1:
                    report = {"status": "incomplete" if any(r["status"] == "incomplete" for r in session_reviews) else "complete",
                              "reviews": session_reviews,
                              "findings": list({f["id"]: f for r in session_reviews for f in r.get("findings", [])}.values())}
                report["area_progress"] = area_progress(memory)
                stopped = next((r["stop_reason"] for r in session_reviews if r.get("stop_reason")), None)
                if stopped:
                    report["stop_reason"] = stopped
                report["sarif"] = scan_summary(memory)
                if args.investigate_all or args.automatic:
                    latest = {a["area_id"]: a["report"] for a in memory.get("reviews", [])}
                    report["findings"] = list({f["id"]: f for i in queue_ids
                                               for f in latest.get(i, {}).get("findings", [])}.values())
                    incomplete = ((args.investigate_all and report["sarif"]["counts"]["inconclusive"]) or stopped or
                                  (args.automatic and memory.get("coverage", {}).get("pages_pending")) or
                                  any(a["status"] != "complete" for a in report["area_progress"]
                                      if a["id"] in queue_ids))
                    report["status"] = "incomplete" if incomplete else "complete"
                    report.pop("next_step", None)
                report["implementation_analysis"] = {"reused": bool(implementation), "path": str(implementation_path)}
                report["max_request_chars"] = getattr(provider, "max_request_chars", 0)
                report["index_stats"] = repository_index["stats"]
                report["memory_path"] = str(memory_path)
                report["memory_reused"] = reused
                report["usage"] = {k: getattr(provider, k, 0) for k in ("calls", "cache_hits", "input_tokens", "output_tokens", "retries")}
        rendered = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
        if args.output:
            args.output.write_text(rendered)
        else:
            sys.stdout.write(rendered)
        if report.get("stop_reason"):
            return stop_exit(report["stop_reason"])
        if report["status"] == "incomplete":
            return 2
        return 1 if report.get("findings") else 0
    except RunStopped as exc:
        report = {"status": "incomplete", "stop_reason": exc.reason, "errors": [str(exc)],
                  "memory_path": str(memory_path) if "memory_path" in locals() else None}
        if "memory" in locals():
            report["sarif"] = scan_summary(memory)
            report["area_progress"] = area_progress(memory)
        rendered = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered)
        else:
            sys.stdout.write(rendered)
        return stop_exit(exc.reason)
    except (OSError, ValueError, ReviewError, EOFError) as exc:
        print(f"njordcup: {exc}", file=sys.stderr)
        return 2
