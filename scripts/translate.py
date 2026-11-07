#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "mlx-lm==0.32.2; sys_platform == 'darwin' and platform_machine == 'arm64'",
#   "mlx==0.32.4; sys_platform == 'darwin' and platform_machine == 'arm64'",
#   "transformers==5.22.2; sys_platform == 'darwin' and platform_machine == 'arm64'",
#   "numba==0.68.1; sys_platform == 'darwin' and platform_machine == 'arm64'",
#   "huggingface-hub==2.4.0; sys_platform == 'darwin' and platform_machine == 'arm64'",
# ]
# ///
"""Incrementally translate upstream English episodes with PLaMo, without checkout.

Reviewed translations replay with Python's standard library alone. MLX and the
model are loaded only if new source prose needs a translation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tempfile
from typing import Any

from translation_core import (
    Document, Unit, extract_document, render_document, unit_key, unmask,
    validate_document,
)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
PROMPT_VERSION = 1


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_json(path: Path) -> dict | list:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def git(*args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def source_files(args, config: dict) -> tuple[dict[str, str], dict]:
    if args.source_dir:
        source = args.source_dir.resolve()
        if not source.is_dir():
            raise ValueError(f"English source directory does not exist: {source}")
        output = args.output_dir.resolve()
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("The English source directory and translated output directory must differ and must not be nested inside each other")
        paths = sorted(p for p in source.rglob("*") if p.suffix.lower() == ".rmd" and p.is_file())
        files = {p.relative_to(source).as_posix(): p.read_bytes().decode("utf-8") for p in paths}
        provenance = {"directory": str(source)}
    else:
        upstream = config["upstream"]
        if args.fetch:
            git("fetch", "--", upstream["remote"], upstream["branch"])
        ref = args.source_ref or upstream["ref"]
        try:
            revision = git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
        except ValueError as error:
            raise ValueError(f"Cannot read English source ref {ref!r}. Fetch upstream first.\n{error}") from error
        names = git("ls-tree", "-r", "--name-only", "-z", revision, "--", "episodes").split(b"\0")
        files = {}
        for raw in names:
            if not raw:
                continue
            name = raw.decode("utf-8")
            path = PurePosixPath(name)
            if path.suffix.lower() != ".rmd":
                continue
            relative = path.relative_to("episodes").as_posix()
            files[relative] = git("show", f"{revision}:{name}").decode("utf-8")
        provenance = {"ref": ref, "revision": revision}
    if not files:
        raise ValueError("No English .Rmd files found")
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe source path: {name}")
    return files, provenance


def record_matches(record: dict | None, unit: Unit) -> bool:
    return bool(record and record.get("source") == unit.masked
                and record.get("kind") == unit.kind
                and unit.file in record.get("files", [record.get("file")]))


def memory_translation(record: dict | None, unit: Unit, profile: str,
                       target_language: str) -> str | None:
    if not record_matches(record, unit):
        return None
    effective = {**record, **record.get("variants", {}).get(unit.file, {})}
    # Reviewed records survive model updates, but never a target-language change.
    # Legacy records inherit the language of the surrounding memory catalog.
    if effective.get("model", {}).get("target_language", target_language) != target_language:
        return None
    if effective.get("review_status", "reviewed") == "machine" and effective.get("profile") != profile:
        return None
    return effective["translation"]


def safe_relative(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".rmd":
        raise ValueError(f"Unsafe managed episode path: {name}")


@dataclass
class Request:
    unit: Unit
    context: str
    glossary: list[dict[str, str]]


def make_request(doc: Document, unit: Unit, config: dict, glossary: list, contexts: dict) -> Request:
    context = config["context"]
    if unit.file in config.get("mouse_dataset_files", []):
        context += " The gene expression dataset is from mice; male and female refer to the sex of mice."
    override = contexts.get(unit_key(unit))
    extra = []
    if record_matches(override, unit):
        context += "\n" + override["context"]
        extra = override.get("glossary", [])
    index = doc.units.index(unit)
    neighbors = doc.units[max(0, index - 2):index + 4]
    context += "\nSurrounding source passages:\n" + "\n\n".join(u.source for u in neighbors)[:6000]
    keep = [{"source": f"<x{i}/>", "target": f"<x{i}/>"} for i in range(len(unit.tokens))]
    return Request(unit, context, glossary + extra + keep)


class MLXTranslator:
    """Lazy backend using the exact pinned PFN model and its custom tokenizer."""

    def __init__(self, config: dict, model_dir: Path | None, cache_dir: Path):
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ValueError("Generating new translations requires an Apple Silicon Mac. Cached replay works on other systems.")
        os.environ.setdefault("HF_HOME", str(cache_dir / "huggingface"))
        os.environ.setdefault("HF_MODULES_CACHE", str(cache_dir / "huggingface-modules"))
        try:
            from huggingface_hub import snapshot_download
            from mlx_lm import load
            from mlx_lm.generate import batch_generate
            from mlx_lm.sample_utils import make_sampler
        except ImportError as error:
            raise ValueError("Install the model dependencies with: uv run scripts/translate.py [options]") from error
        self.settings = config["model"]
        if model_dir is None:
            model_dir = Path(snapshot_download(
                self.settings["id"], revision=self.settings["revision"],
                ignore_patterns=["figures/*"],
            ))
        if not (model_dir / "config.json").is_file():
            raise ValueError(f"Not a local PLaMo model directory: {model_dir}")
        model_config = read_json(model_dir / "config.json")
        if (model_config.get("model_type") != "plamo3"
                or model_config.get("hidden_size") != 4096
                or model_config.get("num_hidden_layers") != 32
                or model_config.get("quantization", {}).get("bits") != 4):
            raise ValueError("The supplied model directory is not PLaMo 3 Translate 8B MLX 4-bit")
        metadata = model_dir / ".cache" / "huggingface" / "download" / "config.json.metadata"
        lines = metadata.read_text().splitlines() if metadata.is_file() else []
        revision = lines[0] if lines else model_dir.resolve().name
        if revision != self.settings["revision"]:
            raise ValueError("Cannot verify the pinned model revision. Use the default download or a Hugging Face snapshot/local-dir retaining its revision metadata.")
        self.model, self.tokenizer = load(
            str(model_dir), trust_remote_code=True,
            tokenizer_config={"trust_remote_code": True},
        )
        self.tokenizer.add_eos_token("<|plamo:tag|>")
        self.generate = batch_generate
        self.sampler = make_sampler(temp=self.settings["temperature"])

    def translate_batch(self, requests: list[Request]) -> list[str]:
        prompts, limits = [], []
        for request in requests:
            prompt = self.tokenizer.apply_chat_template(
                [{"role": "user", "content": request.unit.masked}],
                source_lang=self.settings["source_language"],
                target_lang=self.settings["target_language"],
                form=self.settings["form"], context=request.context,
                glossary=request.glossary, add_generation_prompt=True, tokenize=False,
            )
            tokens = self.tokenizer.encode(prompt, add_special_tokens=False)
            limit = max(192, len(self.tokenizer.encode(request.unit.masked)) * 3 + 160)
            if len(tokens) + limit > 32768:
                raise ValueError(f"Passage exceeds the model context limit: {request.unit.file}:{request.unit.id}")
            prompts.append(tokens)
            limits.append(limit)
        result = self.generate(
            self.model, self.tokenizer, prompts, max_tokens=limits,
            sampler=self.sampler, completion_batch_size=len(requests),
            prefill_batch_size=min(4, len(requests)), return_token_ids=True,
        )
        if any(len(tokens) >= limit for tokens, limit in zip(result.token_ids, limits)):
            raise ValueError("A model response reached its generation limit; no episode files were written")
        return result.texts


def cache_key(profile: str, unit: Unit) -> str:
    return digest(canonical([profile, unit.file, unit_key(unit)]))


def load_cache(path: Path) -> dict:
    records = {}
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                records[record["key"]] = record
            except (ValueError, KeyError) as error:
                raise ValueError(f"Invalid cache record {path}:{number}; repair or remove that line") from error
    return records


def apply_patches(source: str, translated: str, name: str, patches: list) -> tuple[str, list, list]:
    applied, skipped = [], []
    for patch in patches:
        if patch["file"] != name:
            continue
        count = patch.get("expected_occurrences", 1)
        if source.count(patch["source_guard"]) != count:
            skipped.append({"id": patch["id"], "reason": "upstream source changed"})
            continue
        if translated.count(patch["before"]) == count:
            translated = translated.replace(patch["before"], patch["after"])
            applied.append(patch["id"])
        elif translated.count(patch["after"]) == count:
            applied.append(patch["id"])
        else:
            skipped.append({"id": patch["id"], "reason": "translation changed; review adaptation"})
    return translated, applied, skipped


def file_snapshot(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Not a regular output file: {path}")
    return path.read_bytes() if path.exists() else None


def atomic_write_set(writes: dict[Path, bytes | None], snapshots: dict[Path, bytes | None]) -> None:
    """Validate concurrent edits first, stage everything, and roll back failed writes."""
    for path, before in snapshots.items():
        if file_snapshot(path) != before:
            raise ValueError(f"File changed while translation was running: {path}")
    if not writes:
        return
    modes = {path: path.stat().st_mode & 0o777 if path.exists() else 0o644 for path in writes}
    staged = {}
    changed = []
    try:
        for path, data in writes.items():
            if data is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".translation-", dir=path.parent)
            staged[path] = Path(temporary)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.chmod(temporary, modes[path])
        for path, data in writes.items():
            if data is None:
                path.unlink()
            else:
                os.replace(staged[path], path)
            changed.append(path)
    except Exception:
        for path in reversed(changed):
            before = snapshots[path]
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
                os.chmod(path, modes[path])
        raise
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    source = cli.add_mutually_exclusive_group()
    source.add_argument("--source-ref", help="Git ref containing English episodes (default: upstream/main)")
    source.add_argument("--source-dir", type=Path, help="Directory containing English Rmd files")
    cli.add_argument("--fetch", action="store_true", help="Fetch the configured upstream branch first")
    cli.add_argument("--output-dir", type=Path, default=ROOT / "episodes")
    cli.add_argument("--cache-dir", type=Path, default=ROOT / ".cache" / "translation")
    cli.add_argument("--config", type=Path, default=ROOT / "translation" / "config.json")
    cli.add_argument("--model-dir", type=Path, help="Existing copy of the pinned model weights")
    cli.add_argument("--batch-size", type=int, default=8)
    cli.add_argument("--cache-only", action="store_true", help="Fail if any translation needs model inference")
    cli.add_argument("--dry-run", action="store_true", help="Show translation requirements without loading MLX or writing files")
    cli.add_argument("--check", action="store_true", help="Replay cached translations and fail if output differs; write nothing")
    cli.add_argument("--overwrite", action="store_true", help="Explicitly replace locally edited translated outputs")
    cli.add_argument("--prune", action="store_true", help="Remove unchanged managed episodes deleted upstream")
    cli.add_argument("--update-memory", action="store_true", help="Save new translations and provenance to versioned translation resources")
    cli.add_argument("--no-patches", action="store_true", help="Omit the separately recorded local build adaptations")
    return cli


def run(argv=None, backend_factory=None) -> int:
    args = parser().parse_args(argv)
    if args.batch_size < 1:
        raise ValueError("--batch-size must be positive")
    if args.source_dir and args.fetch:
        raise ValueError("--fetch is only available with Git source refs")
    config_path = args.config.resolve()
    config = read_json(config_path)
    if config.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported translation configuration version")
    resources = {key: (config_path.parent / config[key]).resolve()
                 for key in ("memory", "glossary", "contexts", "patches", "manifest")}
    resource_snapshots = {path: file_snapshot(path) for path in resources.values()}
    memory = read_json(resources["memory"])
    target_language = config["model"]["target_language"]
    memory_language = memory.get("model", {}).get("target_language")
    if memory_language != target_language:
        raise ValueError(f"Translation memory target language {memory_language!r} does not match "
                         f"configured target {target_language!r}. Select memory for the target "
                         "language or start with an empty catalog for that language.")
    glossary = read_json(resources["glossary"])
    contexts = read_json(resources["contexts"])["records"]
    patches = [] if args.no_patches else read_json(resources["patches"])["patches"]
    manifest = read_json(resources["manifest"])
    profile = digest(canonical({"prompt_version": PROMPT_VERSION,
                                "model": config["model"], "glossary": glossary,
                                "contexts": contexts, "context": config["context"],
                                "mouse_dataset_files": config.get("mouse_dataset_files", [])}))
    files, provenance = source_files(args, config)
    docs = {name: extract_document(text, name) for name, text in files.items()}
    prose = " ".join(unit.source for doc in docs.values() for unit in doc.units)
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", prose))
    if cjk > 100 and cjk > len(prose) / 4:
        raise ValueError("Source prose appears to be translated CJK text already; select the upstream English ref")
    cache_path = args.cache_dir / "cache.jsonl"
    cache = load_cache(cache_path)
    translations, needed, seeded, cached = {name: {} for name in docs}, [], 0, 0
    for doc in docs.values():
        for unit in doc.units:
            key = unit_key(unit)
            approved = memory["records"].get(key)
            record = cache.get(cache_key(profile, unit))
            saved = memory_translation(approved, unit, profile, target_language)
            if saved is not None:
                translations[unit.file][key] = saved
                seeded += 1
            elif record_matches(record, unit) and record.get("profile") == profile:
                translations[unit.file][key] = record["translation"]
                cached += 1
            else:
                needed.append(make_request(doc, unit, config, glossary, contexts))
    # Same source text repeated within one chapter needs only one model request.
    pending = {cache_key(profile, request.unit): request for request in needed}
    print(f"English source: {provenance.get('revision', provenance.get('directory'))}")
    print(f"{len(files)} episodes; {sum(len(d.units) for d in docs.values())} passages; "
          f"{seeded} translation-memory replays; {cached} runtime-cache replays; "
          f"{len(pending)} new/changed passages need PLaMo")
    state_path = args.cache_dir / f"state-{digest(str(args.output_dir.resolve()))[:16]}.json"
    state_snapshot = file_snapshot(state_path)
    state = read_json(state_path) if state_path.exists() else manifest
    managed = state.get("files", {})
    for name in managed:
        safe_relative(name)
    stale = sorted(name for name in set(managed) - set(files)
                   if (args.output_dir / name).exists())
    if stale:
        print("Previously managed episodes absent upstream: " + ", ".join(stale))
    if args.dry_run:
        for request in pending.values():
            print(f"  needs translation: {request.unit.file}:{request.unit.id} {request.unit.masked[:100]}")
        return 0
    if pending and (args.cache_only or args.check):
        raise ValueError(f"{len(pending)} uncached passages. Use MLX generation or provide reviewed translations; no files were written.")
    target_paths = {name: args.output_dir / name for name in files}
    if args.prune:
        target_paths.update({name: args.output_dir / name for name in stale})
    for path in target_paths.values():
        if not path.resolve().is_relative_to(args.output_dir.resolve()):
            raise ValueError(f"Output path leaves the selected directory: {path}")
    snapshots = {path: file_snapshot(path) for path in target_paths.values()}
    # Inspect existing output protection before any costly inference.
    if not args.check and not args.overwrite:
        for name, path in target_paths.items():
            before = snapshots[path]
            expected = managed.get(name, {}).get("translated_sha256")
            if before is not None and expected and digest(before) != expected:
                raise ValueError(f"Translated output has local edits: {path}. Review it, or use --overwrite explicitly.")
            if before is not None and not expected:
                # Fully cached identical outputs are checked again after rendering.
                if pending:
                    raise ValueError(f"Existing output has no generation record: {path}. Use --overwrite explicitly.")
    if pending:
        backend = (backend_factory or MLXTranslator)(config, args.model_dir, args.cache_dir.resolve())
        requests = list(pending.values())
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        with cache_path.open("a", encoding="utf-8") as stream:
            for offset in range(0, len(requests), args.batch_size):
                batch = requests[offset:offset + args.batch_size]
                results = backend.translate_batch(batch)
                if len(results) != len(batch):
                    raise ValueError("Model returned the wrong number of translations; no episodes were written")
                for request, result in zip(batch, results):
                    result = re.sub(r"\s*\n\s*", " ", result.strip())
                    try:
                        unmask(result, request.unit.tokens)
                    except ValueError as error:
                        report = {"file": request.unit.file, "source": request.unit.source,
                                  "translation": result, "error": str(error)}
                        (args.cache_dir / "review-needed.json").write_bytes(json_bytes(report))
                        raise ValueError(f"Protected-token validation failed for {request.unit.file}:{request.unit.id}; "
                                         "see cache review-needed.json. No episodes were written.") from error
                    unit = request.unit
                    translations[unit.file][unit_key(unit)] = result
                    record = {"key": cache_key(profile, unit), "source_key": unit_key(unit),
                              "source": unit.masked, "kind": unit.kind, "file": unit.file,
                              "translation": result, "profile": profile}
                    stream.write(canonical(record) + "\n")
                    stream.flush()
                print(f"Translated {min(offset + len(batch), len(requests))}/{len(requests)} changed passages", flush=True)
    outputs, adaptations = {}, {}
    skipped = []
    for name, doc in docs.items():
        text = render_document(doc, translations[name])
        validate_document(files[name], text, name)
        text, applied, omitted = apply_patches(files[name], text, name, patches)
        adaptations[name] = applied
        skipped.extend({"file": name, **item} for item in omitted)
        outputs[name] = text.encode("utf-8")
    for item in skipped:
        print(f"Review build adaptation {item['file']}:{item['id']}: {item['reason']}")
    differences = [name for name, data in outputs.items() if snapshots[target_paths[name]] != data]
    if args.check:
        if differences or args.prune and any(snapshots[target_paths[name]] is not None for name in stale):
            raise ValueError("Generated translation differs: " + ", ".join(differences + (stale if args.prune else [])))
        print("All cached translations match the current episode files; no files written.")
        return 0
    for name in differences:
        before = snapshots[target_paths[name]]
        expected = managed.get(name, {}).get("translated_sha256")
        if before is not None and not expected and not args.overwrite:
            raise ValueError(f"Existing output has no generation record: {target_paths[name]}. Use --overwrite explicitly.")
    writes = {target_paths[name]: outputs[name] for name in differences}
    if args.prune:
        writes.update({target_paths[name]: None for name in stale if snapshots[target_paths[name]] is not None})
    next_state = {"schema_version": SCHEMA_VERSION, "upstream": provenance,
                  "model": config["model"], "files": {
                      name: {"source_sha256": digest(files[name]), "translated_sha256": digest(data),
                             "build_patches": adaptations[name]}
                      for name, data in outputs.items()}}
    if not args.prune:
        # Keep ownership of stale outputs so a later --prune can remove them.
        next_state["files"].update({name: managed[name] for name in stale
                                   if (args.output_dir / name).is_file()})
    if args.update_memory:
        # Current passages and their filenames are persisted alongside their output.
        for doc in docs.values():
            for unit in doc.units:
                key = unit_key(unit)
                record = memory["records"].get(key)
                output = translations[unit.file][key]
                if memory_translation(record, unit, profile, target_language) == output:
                    continue
                variant = {"translation": output, "review_status": "machine",
                           "profile": profile, "model": config["model"]}
                if record:
                    record["files"] = sorted(set(record["files"] + [unit.file]))
                    record.setdefault("variants", {})[unit.file] = variant
                else:
                    memory["records"][key] = {"source": unit.masked, "kind": unit.kind,
                                              "files": [unit.file], **variant}
        manifest.update(next_state)
        for path, data in ((resources["memory"], json_bytes(memory)), (resources["manifest"], json_bytes(manifest))):
            snapshots[path] = resource_snapshots[path]
            if snapshots[path] != data:
                writes[path] = data
    snapshots[state_path] = state_snapshot
    writes[state_path] = json_bytes(next_state)
    atomic_write_set(writes, snapshots)
    print(f"Wrote {len(differences)} changed {target_language} episodes; {len(outputs) - len(differences)} unchanged.")
    if skipped:
        print("Upstream or translation changes require review of the listed local build adaptations.")
    return 0


def main() -> int:
    try:
        return run()
    except (ValueError, OSError, UnicodeError) as error:
        print(f"translation: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
