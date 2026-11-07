"""Preservation checks for the English-to-Simplified-Chinese lesson workflow.

These fixtures deliberately include syntax that can look like translatable prose.
They run without MLX, model weights, network access, or an R installation.
"""
import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import translation_core as core


LESSON = r'''---
source: Rmd
title: An English lesson
teaching: 10
exercises: 15
---

```{r setup, include=FALSE, fig.cap="English caption"}
# Keep this English comment.
label <- "Do not translate this value"
```

:::::::::::::::::::: objectives

- Learn about R.

::::::::::::::::::::::::::::::::

## Inspecting data {#stable-id}

Read [the guide](https://example.org/a_(b)/index.html) and compare `r nrow(x)` with `x$gene`.

Use <kbd>Ctrl</kbd> and <kbd>Enter</kbd> with gene\_description.

See @Broman:2018 and @ref(sec:genvec).

:::::::::::::::::::: challenge

## A challenge

1. Find the value.

   ```{r answer, eval=FALSE}
   x$gene[1]
   ```

:::::::: solution

## Solution

The answer is shown below.

````text
Keep this raw text.
```
This triple fence is part of the four-backtick example.
````

::::::::::::::::

::::::::::::::::::::::::::::::::
'''


def identity_translations(document):
    """Keep model input unchanged unless a test supplies a deliberate edit."""
    return {core.unit_key(unit): unit.masked for unit in document.units}


def translated_lesson(source=LESSON, filename="lesson.Rmd"):
    document = core.extract_document(source, filename)
    translations = identity_translations(document)
    for unit in document.units:
        translations[core.unit_key(unit)] = (
            unit.masked.replace("An English lesson", "简体中文课程")
            .replace("Learn about R.", "学习 R。")
            .replace("Inspecting data", "检查数据")
            .replace("A challenge", "练习")
            .replace("Find the value.", "求出该值。")
            .replace("Solution", "答案")
            .replace("The answer is shown below.", "答案如下。")
        )
    return document, core.render_document(document, translations)


class LessonPreservationTests(unittest.TestCase):
    def test_translates_prose_while_preserving_nested_fences_and_code(self):
        document, output = translated_lesson()
        self.assertIn("简体中文课程", output)
        self.assertIn("- 学习 R。", output)
        self.assertIn("## 检查数据", output)
        self.assertIn("1. 求出该值。", output)
        self.assertIn("## 答案", output)
        fence_lines = lambda text: [
            line for line in text.splitlines(keepends=True)
            if re.match(r"^\s*:{3,}", line)
        ]
        self.assertEqual(fence_lines(LESSON), fence_lines(output))
        for literal in (
            '```{r setup, include=FALSE, fig.cap="English caption"}\n'
            '# Keep this English comment.\n'
            'label <- "Do not translate this value"\n```\n',
            "   ```{r answer, eval=FALSE}\n   x$gene[1]\n   ```\n",
            "````text\nKeep this raw text.\n```\n"
            "This triple fence is part of the four-backtick example.\n````\n",
        ):
            self.assertIn(literal, output)
            self.assertFalse(any(literal.strip() in unit.source for unit in document.units))
        core.validate_document(LESSON, output, "lesson.Rmd")

    def test_yaml_keys_and_non_title_values_are_unchanged(self):
        _, output = translated_lesson()
        for line in ("source: Rmd", "teaching: 10", "exercises: 15"):
            self.assertIn(line + "\n", output)
        title_line = next(line for line in output.splitlines() if line.startswith("title:"))
        self.assertEqual(json.loads(title_line.partition(":")[2].strip()), "简体中文课程")

    def test_heading_identifiers_are_restored_at_the_end(self):
        source = "## Inspecting data {#stable-id}\n"
        document = core.extract_document(source, "heading.Rmd")
        self.assertEqual(len(document.units), 1)
        unit = document.units[0]
        marker = unit.masked[unit.masked.index("<x"):]
        output = core.render_document(document, {core.unit_key(unit): "数据 " + marker + " 的检查"})
        self.assertRegex(output.rstrip(), r"^## .* \{#stable-id\}$")
        self.assertEqual(output.count("{#stable-id}"), 1)
        core.validate_document(source, output, "heading.Rmd")

    def test_citations_have_boundaries_next_to_japanese_text(self):
        source = "See @Broman:2018 and @ref(sec:genvec).\n"
        document = core.extract_document(source, "citations.Rmd")
        unit = document.units[0]
        markers = re.findall(r"<x\d+/>", unit.masked)
        self.assertEqual(len(markers), 2)
        output = core.render_document(document, {
            core.unit_key(unit): "日本語" + markers[0] + "を参照。" + markers[1] + "も参照。"
        })
        for token in unit.tokens:
            self.assertIn(" " + token + " ", output)
        core.validate_document(source, output, "citations.Rmd")

    def test_citations_have_boundaries_next_to_chinese_text(self):
        source = "See @Broman:2018 and @ref(sec:genvec).\n"
        document = core.extract_document(source, "citations.Rmd")
        unit = document.units[0]
        markers = re.findall(r"<x\d+/>", unit.masked)
        output = core.render_document(document, {
            core.unit_key(unit): "参见" + markers[0] + "和" + markers[1] + "。"
        })
        for token in unit.tokens:
            self.assertIn(" " + token + " ", output)
        core.validate_document(source, output, "citations.Rmd")

    def test_missing_translation_fails_instead_of_returning_partial_english(self):
        document = core.extract_document(LESSON, "lesson.Rmd")
        translations = identity_translations(document)
        translations.pop(core.unit_key(document.units[-1]))
        with self.assertRaises((ValueError, KeyError)):
            core.render_document(document, translations)

    def test_validator_rejects_protected_content_changes(self):
        _, output = translated_lesson()
        mutations = {
            "chunk option": ("include=FALSE", "include=TRUE"),
            "chunk string": ('"Do not translate this value"', '"Changed value"'),
            "fence class": (":::::::::::::::::::: objectives", ":::::::::::::::::::: 目的"),
            "inline R": ("`r nrow(x)`", "`r ncol(x)`"),
            "URL": ("https://example.org/a_(b)/index.html", "https://example.org/other.html"),
            "keyboard value": ("<kbd>Ctrl</kbd>", "<kbd>Control</kbd>"),
            "heading ID": ("{#stable-id}", "{#renamed-id}"),
            "citation ID": ("@Broman:2018", "@Broman:2019"),
            "cross-reference": ("@ref(sec:genvec)", "@ref(sec:other)"),
        }
        for label, (old, new) in mutations.items():
            with self.subTest(label=label):
                self.assertIn(old, output)
                changed = output.replace(old, new, 1)
                with self.assertRaises(ValueError):
                    core.validate_document(LESSON, changed, "lesson.Rmd")

    def test_validator_keeps_code_block_order(self):
        first = '```{r first}\nprint("first")\n```\n'
        second = '```{r second}\nprint("second")\n```\n'
        source = first + "\nA paragraph.\n\n" + second
        swapped = second + "\n段落です。\n\n" + first
        with self.assertRaises(ValueError):
            core.validate_document(source, swapped, "blocks.Rmd")

    def test_unclosed_code_fence_fails_closed(self):
        with self.assertRaises(ValueError):
            core.extract_document("A paragraph.\n\n```{r}\nx <- 1\n", "unclosed.Rmd")

    def test_indented_code_is_preserved_and_independently_validated(self):
        code = '    # English code comment\n    x <- "literal value"\n'
        source = "Narrative prose.\n\n" + code + "\nFollowing prose.\n"
        document = core.extract_document(source, "indented.Rmd")
        translations = {
            core.unit_key(unit): unit.masked.replace("Narrative prose.", "前の本文です。")
            .replace("Following prose.", "後の本文です。") for unit in document.units
        }
        output = core.render_document(document, translations)
        self.assertIn(code, output)
        self.assertIn("前の本文です。", output)
        self.assertIn("後の本文です。", output)
        self.assertFalse(any("English code comment" in unit.source for unit in document.units))
        with self.assertRaises(ValueError):
            core.validate_document(source, output.replace('"literal value"', '"changed value"'), "indented.Rmd")


class InlineProtectionTests(unittest.TestCase):
    def test_balanced_urls_and_url_labels_are_separate_protected_values(self):
        source = (
            "Read [the guide](https://example.org/a_(b)/index.html), "
            "[https://example.org/reference](https://example.org/reference), and "
            r"[colors](https://www.cookbook-r.com/Graphs/Colors_\(ggplot2\)/)."
        )
        masked, tokens = core.mask(source)
        output = core.unmask(masked, tokens)
        self.assertEqual(source, output)
        self.assertIn(
            r"](https://www.cookbook-r.com/Graphs/Colors_\(ggplot2\)/).", output
        )
        # An URL label must not swallow its closing bracket and destination.
        self.assertIn("[<x", masked)
        self.assertRegex(masked, r"\[<x\d+/>\]\(<x\d+/>\)")

    def test_inline_code_identifiers_and_keyboard_literals_are_immutable(self):
        source = r"Use `x$gene`, `r nrow(x)`, hsapiens\_homolog\_associated\_gene\_name and <kbd>Ctrl</kbd>."
        masked, tokens = core.mask(source)
        self.assertIn("`x$gene`", tokens)
        self.assertIn("`r nrow(x)`", tokens)
        self.assertIn(r"hsapiens\_homolog\_associated\_gene\_name", tokens)
        self.assertIn("<kbd>Ctrl</kbd>", tokens)
        self.assertEqual(core.unmask(masked, tokens), source)

    def test_missing_duplicate_and_unknown_placeholders_are_rejected(self):
        masked, tokens = core.mask("Use `x` and `y`.")
        for invalid in (
            masked.replace("<x0/>", "", 1),
            masked + " <x0/>",
            masked + " <x999/>",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                core.unmask(invalid, tokens)

    def test_inline_tokens_can_move_with_japanese_word_order(self):
        source = "Use `first` before `second`.\n"
        document = core.extract_document(source, "ordering.Rmd")
        unit = document.units[0]
        output = core.render_document(document, {core.unit_key(unit): "<x1/>の前に<x0/>を使用します。"})
        self.assertLess(output.index("`second`"), output.index("`first`"))
        core.validate_document(source, output, "ordering.Rmd")

    def test_reflowed_link_labels_are_not_mistaken_for_reference_identifiers(self):
        source = (
            "Read [a link](https://example.org/guide) and some\n"
            "continued prose[^note].\n\n[^note]: A note.\n"
        )
        document = core.extract_document(source, "footnote.Rmd")
        translations = identity_translations(document)
        for unit in document.units:
            translations[core.unit_key(unit)] = (
                "本文の[リンク](<x0/>)と続きの説明<x1/>。"
                if "[a link]" in unit.source else "脚注の説明です。"
            )
        output = core.render_document(document, translations)
        self.assertIn("[リンク](https://example.org/guide)", output)
        self.assertEqual(output.count("[^note]"), 2)
        core.validate_document(source, output, "footnote.Rmd")

    def test_backticks_inside_html_code_and_attributes_are_not_markdown_delimiters(self):
        source = 'Press <kbd title="`">Ctrl</kbd> and read <code>x <- `literal</code>.'
        masked, tokens = core.mask(source)
        self.assertEqual(core.unmask(masked, tokens), source)
        self.assertNotIn("Ctrl", masked)
        self.assertNotIn("literal", masked)

    def test_reference_link_identifiers_remain_literal_when_labels_translate(self):
        source = (
            'Read [the guide][docs], [docs], and [docs][].\n\n'
            '[docs]: https://example.org/guide "English reference title"\n'
        )
        document = core.extract_document(source, "reference-links.Rmd")
        translations = {
            core.unit_key(unit): unit.masked.replace("Read", "参照").replace("the guide", "ガイド")
            for unit in document.units
        }
        output = core.render_document(document, translations)
        self.assertIn("[ガイド][docs]", output)
        self.assertIn("[docs][]", output)
        self.assertIn('[docs]: https://example.org/guide "English reference title"', output)
        with self.assertRaises(ValueError):
            core.validate_document(source, output.replace("[docs]", "[renamed]", 1), "reference-links.Rmd")


class TableProtectionTests(unittest.TestCase):
    def test_example_values_remain_literal_but_headers_translate(self):
        source = r'''| Good Name | Good Alternative | Avoid |
| --- | --- | --- |
| Max\_temp\_C | MaxTemp | Maximum Temp (°C) |
| cell\_type | CellType | Cell Type |
'''
        document = core.extract_document(source, "examples.Rmd")
        translations = identity_translations(document)
        for unit in document.units:
            translations[core.unit_key(unit)] = {
                "Good Name": "適切な名前", "Good Alternative": "代替案", "Avoid": "避ける例"
            }.get(unit.masked, unit.masked)
        output = core.render_document(document, translations)
        self.assertIn("適切な名前", output)
        for line in source.splitlines()[1:]:
            self.assertIn(line, output)
        core.validate_document(source, output, "examples.Rmd")

    def test_data_column_names_stay_literal_while_descriptions_translate(self):
        source = '''| Column | Description |
| --- | --- |
| gene | The name of the gene that was measured |
| sample | The sample identifier |
'''
        document = core.extract_document(source, "description.Rmd")
        translations = identity_translations(document)
        for unit in document.units:
            translations[core.unit_key(unit)] = {
                "Column": "列", "Description": "説明",
                "The name of the gene that was measured": "測定した遺伝子の名前",
                "The sample identifier": "サンプル識別子",
            }.get(unit.masked, unit.masked)
        output = core.render_document(document, translations)
        self.assertIn("| gene |", output)
        self.assertIn("| sample |", output)
        self.assertIn("測定した遺伝子の名前", output)
        core.validate_document(source, output, "description.Rmd")
        with self.assertRaises(ValueError):
            core.validate_document(source, output.replace("| gene |", "| genes |"), "description.Rmd")


class CommentProtectionTests(unittest.TestCase):
    def test_translates_notes_without_touching_commented_code_or_swallowing_prose(self):
        source = r'''<!-- Notes about numeric factors. -->

<!-- The levels are converted -->

<!-- to numbers using `as.numeric()`. -->

<!-- ```{r} -->

<!-- years <- factor(c(1990, 1983)) -->

<!-- as.numeric(years) # Keep this code comment. -->

<!-- ```

<!-- The levels are used for indexing. -->

<!-- A normal multiline note
continues on this line. -->

## Renaming factors

Visible prose after the commented code.
'''
        document = core.extract_document(source, "comments.Rmd")
        translations = identity_translations(document)
        comments = [unit for unit in document.units if unit.kind == "comment"]
        self.assertTrue(comments)
        self.assertTrue(any("Renaming factors" in unit.source for unit in document.units))
        for unit in document.units:
            if unit.kind == "comment":
                markers = " ".join(re.findall(r"<x\d+/>", unit.masked))
                translations[core.unit_key(unit)] = "因子の水準について説明します。" + markers
            else:
                translations[core.unit_key(unit)] = unit.masked.replace(
                    "Renaming factors", "因子の名称変更"
                ).replace("Visible prose after the commented code.", "コメントの後にある本文です。")
        output = core.render_document(document, translations)
        code = "<!-- ```{r} -->\n\n<!-- years <- factor(c(1990, 1983)) -->\n\n<!-- as.numeric(years) # Keep this code comment. -->\n\n<!-- ```\n"
        self.assertIn(code, output)
        self.assertEqual(source.count("<!--"), output.count("<!--"))
        self.assertEqual(source.count("-->"), output.count("-->"))
        self.assertIn("## 因子の名称変更", output)
        self.assertIn("コメントの後にある本文です。", output)
        self.assertNotIn("Notes about numeric factors", output)
        core.validate_document(source, output, "comments.Rmd")
        with self.assertRaises(ValueError):
            core.validate_document(source, output.replace("years <- factor", "years <- ordered"), "comments.Rmd")


class FakeBackend:
    """A deterministic translator used to exercise orchestration, not quality."""

    def __init__(self, invalid_source=None):
        self.seen = []
        self.invalid_source = invalid_source

    def translate_batch(self, requests):
        result = []
        for request in requests:
            self.seen.append(request.unit.source)
            if self.invalid_source and self.invalid_source in request.unit.source:
                result.append("缺少占位符的译文。")
            else:
                markers = " ".join(re.findall(r"<x\d+/>", request.unit.masked))
                result.append("用于测试的简体中文译文。" + markers)
        return result


class TranslationCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = importlib.import_module("translate")

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="translation-tests-")
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.source = self.base / "english"
        self.source.mkdir()
        self.output = self.base / "chinese"
        self.cache = self.base / "cache"
        self.arguments = [
            "--source-dir", str(self.source),
            "--output-dir", str(self.output),
            "--cache-dir", str(self.cache),
            "--config", str(ROOT / "translation" / "config.json"),
        ]

    def write_source(self, text, filename="new-lesson.Rmd"):
        path = self.source / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def copy_translation_resources(self):
        """Give memory-mutating tests their own config and resource catalog."""
        original = ROOT / "translation" / "config.json"
        config = json.loads(original.read_text())
        directory = self.base / "resources"
        directory.mkdir()
        for key in ("memory", "glossary", "contexts", "patches", "manifest"):
            source = (original.parent / config[key]).resolve()
            destination = directory / source.name
            destination.write_bytes(source.read_bytes())
            config[key] = destination.name
        path = directory / "config.json"
        path.write_text(json.dumps(config, ensure_ascii=False))
        self.arguments[self.arguments.index("--config") + 1] = str(path)
        return path, config, directory

    def run_cli(self, extra=(), backend=None, forbid_backend=False):
        def factory(config, model_dir, cache_dir):
            if forbid_backend:
                self.fail("A cache-only/check/dry-run operation tried to load the model")
            return backend if backend is not None else FakeBackend()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return self.cli.run(self.arguments + list(extra), backend_factory=factory)

    def test_cached_replay_and_code_only_update_never_load_the_model(self):
        self.write_source("A unique incremental fixture paragraph.\n\n```{r}\nx <- 1\n```\n")
        backend = FakeBackend()
        self.run_cli(backend=backend)
        self.assertEqual(len(backend.seen), 1)
        first = (self.output / "new-lesson.Rmd").read_text()
        self.run_cli(["--cache-only"], forbid_backend=True)
        self.assertEqual((self.output / "new-lesson.Rmd").read_text(), first)

        self.write_source("A unique incremental fixture paragraph.\n\n```{r}\nx <- 2\n```\n")
        self.run_cli(["--cache-only"], forbid_backend=True)
        updated = (self.output / "new-lesson.Rmd").read_text()
        self.assertIn("x <- 2", updated)
        self.assertNotIn("x <- 1", updated)
        self.assertEqual(first.replace("x <- 1", "x <- 2"), updated)

    def test_new_prose_translates_once_without_retranslating_shifted_units(self):
        self.write_source("An unchanged cache-fixture paragraph.\n")
        self.run_cli()
        self.write_source("A newly inserted cache-fixture paragraph.\n\nAn unchanged cache-fixture paragraph.\n")
        backend = FakeBackend()
        self.run_cli(backend=backend)
        self.assertEqual(backend.seen, ["A newly inserted cache-fixture paragraph."])

    def test_memory_for_another_target_language_is_rejected_before_inference(self):
        _, config, directory = self.copy_translation_resources()
        self.write_source("A target-language isolation fixture paragraph.\n")
        memory_path = directory / config["memory"]
        memory = json.loads(memory_path.read_text())
        memory["model"]["target_language"] = (
            "ja" if config["model"]["target_language"] != "ja" else "zh-hans"
        )
        memory_path.write_text(json.dumps(memory, ensure_ascii=False))
        before = memory_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Translation memory target language.*does not match"):
            self.run_cli(["--update-memory"], forbid_backend=True)
        self.assertEqual(memory_path.read_bytes(), before)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.cache.exists())

    def test_reviewed_record_or_variant_from_another_language_is_not_replayed(self):
        _, config, directory = self.copy_translation_resources()
        source = "A reviewed target-language fixture paragraph.\n"
        self.write_source(source)
        unit = core.extract_document(source, "new-lesson.Rmd").units[0]
        other_language = "ja" if config["model"]["target_language"] != "ja" else "zh-hans"
        translation = {"translation": "別の言語の訳文です。", "review_status": "reviewed",
                       "model": {**config["model"], "target_language": other_language}}
        memory_path = directory / config["memory"]
        memory = json.loads(memory_path.read_text())
        for use_variant in (False, True):
            with self.subTest(use_variant=use_variant):
                record = {"source": unit.masked, "kind": unit.kind, "files": [unit.file]}
                if use_variant:
                    record.update({"translation": "正确的基础译文。", "review_status": "reviewed",
                                   "variants": {unit.file: translation}})
                else:
                    record.update(translation)
                memory["records"] = {core.unit_key(unit): record}
                memory_path.write_text(json.dumps(memory, ensure_ascii=False))
                with self.assertRaisesRegex(ValueError, "1 uncached passages"):
                    self.run_cli(["--cache-only"], forbid_backend=True)
                self.assertFalse(self.output.exists())

    def test_reviewed_same_language_memory_survives_model_revision_changes(self):
        config_path, config, directory = self.copy_translation_resources()
        source = "A reviewed model-revision fixture paragraph.\n"
        self.write_source(source)
        unit = core.extract_document(source, "new-lesson.Rmd").units[0]
        memory_path = directory / config["memory"]
        memory = json.loads(memory_path.read_text())
        memory["records"] = {core.unit_key(unit): {
            "source": unit.masked, "kind": unit.kind, "files": [unit.file],
            "translation": "已审核的译文。", "review_status": "reviewed",
            "profile": "an-earlier-prompt-profile", "model": dict(config["model"]),
        }}
        memory_path.write_text(json.dumps(memory, ensure_ascii=False))
        config["model"]["revision"] = "0" * 40
        config_path.write_text(json.dumps(config, ensure_ascii=False))
        self.run_cli(["--cache-only"], forbid_backend=True)
        self.assertEqual((self.output / unit.file).read_text(), "已审核的译文。\n")

    def test_identical_prose_in_different_lessons_keeps_its_own_translation(self):
        self.write_source("A context-sensitive repeated fixture paragraph.\n", "first.Rmd")
        self.write_source("A context-sensitive repeated fixture paragraph.\n", "second.Rmd")

        class PerFileBackend:
            def translate_batch(self, requests):
                return ["第一课的译文。" if request.unit.file == "first.Rmd"
                        else "第二课的译文。" for request in requests]

        self.run_cli(backend=PerFileBackend())
        self.assertIn("第一课", (self.output / "first.Rmd").read_text())
        self.assertIn("第二课", (self.output / "second.Rmd").read_text())
        self.run_cli(["--cache-only"], forbid_backend=True)
        self.assertIn("第一课", (self.output / "first.Rmd").read_text())
        self.assertIn("第二课", (self.output / "second.Rmd").read_text())

    def test_persisted_machine_memory_is_invalidated_by_prompt_profile_changes(self):
        config_path, config, directory = self.copy_translation_resources()
        self.write_source("A persisted-machine-memory fixture paragraph.\n")
        self.run_cli(["--update-memory"])
        memory = json.loads((directory / config["memory"]).read_text())
        document = core.extract_document((self.source / "new-lesson.Rmd").read_text(), "new-lesson.Rmd")
        record = memory["records"][core.unit_key(document.units[0])]
        self.assertEqual(record["review_status"], "machine")
        self.assertTrue(record["profile"])
        # Removing the local model cache proves replay uses persisted memory.
        (self.cache / "cache.jsonl").unlink()
        self.run_cli(["--cache-only"], forbid_backend=True)
        output = (self.output / "new-lesson.Rmd").read_bytes()
        original_config = config_path.read_bytes()
        glossary_path = directory / config["glossary"]
        original_glossary = glossary_path.read_bytes()
        for changed in ("context", "glossary", "mouse_dataset_files", "model_revision"):
            with self.subTest(changed=changed):
                config_path.write_bytes(original_config)
                glossary_path.write_bytes(original_glossary)
                updated = json.loads(original_config)
                if changed == "context":
                    updated["context"] += " A revised translation context."
                elif changed == "glossary":
                    glossary = json.loads(original_glossary)
                    glossary.append({"source": "fixture", "target": "测试数据"})
                    glossary_path.write_text(json.dumps(glossary, ensure_ascii=False))
                elif changed == "mouse_dataset_files":
                    updated["mouse_dataset_files"] = ["new-lesson.Rmd"]
                else:
                    updated["model"]["revision"] = "0" * 40
                config_path.write_text(json.dumps(updated, ensure_ascii=False))
                with self.assertRaises(ValueError):
                    self.run_cli(["--cache-only"], forbid_backend=True)
                self.assertEqual((self.output / "new-lesson.Rmd").read_bytes(), output)

    def test_update_memory_preserves_distinct_translations_for_each_file(self):
        self.copy_translation_resources()
        self.write_source("An identical persisted-variant fixture paragraph.\n", "first.Rmd")
        self.write_source("An identical persisted-variant fixture paragraph.\n", "second.Rmd")

        class PerFileBackend:
            def translate_batch(self, requests):
                return ["第一个文件的译文。" if request.unit.file == "first.Rmd"
                        else "第二个文件的译文。" for request in requests]

        self.run_cli(["--update-memory"], backend=PerFileBackend())
        expected = {name: (self.output / name).read_bytes() for name in ("first.Rmd", "second.Rmd")}
        self.assertNotEqual(expected["first.Rmd"], expected["second.Rmd"])
        for name in expected:
            (self.output / name).unlink()
        (self.cache / "cache.jsonl").unlink()
        self.run_cli(["--cache-only"], forbid_backend=True)
        for name, content in expected.items():
            self.assertEqual((self.output / name).read_bytes(), content)

    def test_concurrent_memory_edit_aborts_writes_without_losing_human_changes(self):
        _, config, directory = self.copy_translation_resources()
        self.write_source("A concurrent-memory-edit fixture paragraph.\n")
        memory_path = directory / config["memory"]
        manifest_path = directory / config["manifest"]
        original_manifest = manifest_path.read_bytes()

        class EditingBackend(FakeBackend):
            edited_bytes = None

            def translate_batch(self, requests):
                data = json.loads(memory_path.read_text())
                data["human_annotation"] = "翻译过程中手动添加的审核记录。"
                self.edited_bytes = (json.dumps(data, ensure_ascii=False) + "\n").encode()
                memory_path.write_bytes(self.edited_bytes)
                return super().translate_batch(requests)

        backend = EditingBackend()
        with self.assertRaises(ValueError):
            self.run_cli(["--update-memory"], backend=backend)
        self.assertEqual(memory_path.read_bytes(), backend.edited_bytes)
        self.assertEqual(manifest_path.read_bytes(), original_manifest)
        self.assertFalse(self.output.exists())

    def test_cache_only_miss_does_not_write_any_output(self):
        self.write_source("A never translated cache-only fixture paragraph.\n")
        with self.assertRaises(ValueError):
            self.run_cli(["--cache-only"], forbid_backend=True)
        self.assertFalse(self.output.exists())

    def test_dry_run_has_no_model_or_filesystem_side_effects(self):
        self.write_source("A never translated dry-run fixture paragraph.\n")
        self.run_cli(["--dry-run"], forbid_backend=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.cache.exists())

    def test_check_uses_cached_results_without_rewriting_output(self):
        self.write_source("A check-mode fixture paragraph.\n")
        self.run_cli()
        target = self.output / "new-lesson.Rmd"
        before = target.read_bytes()
        before_mtime = target.stat().st_mtime_ns
        self.run_cli(["--check"], forbid_backend=True)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(target.stat().st_mtime_ns, before_mtime)

    def test_invalid_translation_cannot_partially_write_a_batch_of_lessons(self):
        self.write_source("First atomic-output fixture paragraph.\n", "first.Rmd")
        self.write_source("Second atomic-output fixture uses `x$gene`.\n", "second.Rmd")
        with self.assertRaises(ValueError):
            self.run_cli(backend=FakeBackend(invalid_source="Second atomic-output"))
        self.assertFalse((self.output / "first.Rmd").exists())
        self.assertFalse((self.output / "second.Rmd").exists())

    def test_dirty_output_is_preserved_until_overwrite_is_explicit(self):
        self.write_source("A dirty-output fixture paragraph.\n")
        self.run_cli()
        target = self.output / "new-lesson.Rmd"
        edited = target.read_text() + "\n手动修改的正文。\n"
        target.write_text(edited)
        with self.assertRaises(ValueError):
            self.run_cli(["--cache-only"], forbid_backend=True)
        self.assertEqual(target.read_text(), edited)
        self.run_cli(["--cache-only", "--overwrite"], forbid_backend=True)
        self.assertNotIn("手动修改的正文", target.read_text())

    def test_source_directory_cannot_be_used_as_output_directory(self):
        self.write_source("An English source-preservation fixture paragraph.\n")
        before = (self.source / "new-lesson.Rmd").read_bytes()
        args = self.arguments.copy()
        args[args.index("--output-dir") + 1] = str(self.source)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ValueError):
                self.cli.run(args, backend_factory=lambda *unused: self.fail("Model should not load"))
        self.assertEqual((self.source / "new-lesson.Rmd").read_bytes(), before)

    def test_nested_source_and_output_directories_are_rejected_before_writing(self):
        for source_contains_output in (True, False):
            with self.subTest(source_contains_output=source_contains_output):
                directory = self.base / ("source-parent" if source_contains_output else "output-parent")
                source = directory if source_contains_output else directory / "english"
                output = directory / "chinese" if source_contains_output else directory
                source.mkdir(parents=True)
                original = b"A nested-directory safety fixture paragraph.\n"
                (source / "fixture.Rmd").write_bytes(original)
                args = self.arguments.copy()
                args[args.index("--source-dir") + 1] = str(source)
                args[args.index("--output-dir") + 1] = str(output)
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(ValueError):
                        self.cli.run(args, backend_factory=lambda *unused: self.fail("Model should not load"))
                self.assertEqual((source / "fixture.Rmd").read_bytes(), original)
                self.assertEqual(list(directory.rglob("*.Rmd")), [source / "fixture.Rmd"])
                self.assertFalse(self.cache.exists())

    def test_parent_output_symlink_cannot_escape_the_selected_directory(self):
        self.write_source("A parent-symlink safety fixture paragraph.\n", "nested/fixture.Rmd")
        self.output.mkdir()
        outside = self.base / "outside-output"
        outside.mkdir()
        protected = outside / "fixture.Rmd"
        original = "输出目录之外的现有文件。\n".encode()
        protected.write_bytes(original)
        (self.output / "nested").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_cli(forbid_backend=True)
        self.assertEqual(protected.read_bytes(), original)
        self.assertFalse(self.cache.exists())

    def test_prune_retains_ownership_after_an_update_without_pruning(self):
        self.write_source("A retained-episode fixture paragraph.\n", "retained.Rmd")
        self.write_source("A removed-episode fixture paragraph.\n", "removed.Rmd")
        self.run_cli()
        (self.source / "removed.Rmd").unlink()
        self.run_cli(["--cache-only"], forbid_backend=True)
        self.assertTrue((self.output / "removed.Rmd").exists())
        self.run_cli(["--cache-only", "--prune"], forbid_backend=True)
        self.assertFalse((self.output / "removed.Rmd").exists())
        self.assertTrue((self.output / "retained.Rmd").exists())

    def test_prune_does_not_remove_locally_edited_or_unmanaged_files(self):
        self.write_source("A retained-prune fixture paragraph.\n", "retained.Rmd")
        self.write_source("A dirty-prune fixture paragraph.\n", "removed.Rmd")
        self.run_cli()
        (self.source / "removed.Rmd").unlink()
        removed = self.output / "removed.Rmd"
        removed.write_text(removed.read_text() + "\n手动添加的备注。\n")
        unmanaged = self.output / "personal-notes.Rmd"
        unmanaged.write_text("翻译工具管理范围之外的备注。\n")
        with self.assertRaises(ValueError):
            self.run_cli(["--cache-only", "--prune"], forbid_backend=True)
        self.assertIn("手动添加的备注", removed.read_text())
        self.assertEqual(unmanaged.read_text(), "翻译工具管理范围之外的备注。\n")

    def test_write_failure_restores_the_entire_previous_output_set(self):
        self.write_source("First rollback fixture paragraph.\n", "first.Rmd")
        self.write_source("Second rollback fixture paragraph.\n", "second.Rmd")
        self.run_cli()
        first = self.output / "first.Rmd"
        second = self.output / "second.Rmd"
        before = {first: first.read_bytes(), second: second.read_bytes()}
        # Include changed inline values so even the fake translation yields a
        # different output for both episodes before the second rename fails.
        self.write_source("First changed rollback fixture uses `new_first`.\n", "first.Rmd")
        self.write_source("Second changed rollback fixture uses `new_second`.\n", "second.Rmd")
        replace = self.cli.os.replace

        def fail_second(source, destination):
            if Path(destination) == second:
                raise OSError("Simulated failure while replacing the second episode")
            return replace(source, destination)

        with mock.patch.object(self.cli.os, "replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                self.run_cli()
        self.assertEqual(first.read_bytes(), before[first])
        self.assertEqual(second.read_bytes(), before[second])
        self.assertFalse(list(self.output.glob(".translation-*")))

    def test_reviewed_upstream_revision_replays_all_seven_delivered_lessons(self):
        manifest = json.loads((ROOT / "translation" / "source-manifest.json").read_text())
        revision = manifest["upstream"]["revision"]
        available = subprocess.run(
            ["git", "cat-file", "-e", revision + "^{commit}"], cwd=ROOT,
            capture_output=True,
        )
        if available.returncode:
            self.skipTest("The original upstream Git revision is not available locally")
        for filename, expected in manifest["files"].items():
            source = subprocess.check_output(
                ["git", "show", revision + ":episodes/" + filename], cwd=ROOT,
            )
            self.assertEqual(hashlib.sha256(source).hexdigest(), expected["source_sha256"])
            (self.source / filename).write_bytes(source)
        self.run_cli(["--cache-only"], forbid_backend=True)
        for filename, expected in manifest["files"].items():
            with self.subTest(filename=filename):
                result = (self.output / filename).read_bytes()
                self.assertEqual(hashlib.sha256(result).hexdigest(), expected["translated_sha256"])

    def test_changed_upstream_code_is_not_overwritten_by_an_old_build_patch(self):
        manifest = json.loads((ROOT / "translation" / "source-manifest.json").read_text())
        revision = manifest["upstream"]["revision"]
        fetched = subprocess.run(
            ["git", "show", revision + ":episodes/40-visualization.Rmd"], cwd=ROOT,
            capture_output=True, text=True,
        )
        if fetched.returncode:
            self.skipTest("The original upstream Git revision is not available locally")
        self.assertIn("    blue_theme()\n", fetched.stdout)
        updated_source = fetched.stdout.replace("    blue_theme()\n", "    theme_minimal()\n", 1)
        self.write_source(updated_source, "40-visualization.Rmd")
        self.run_cli(["--cache-only"], forbid_backend=True)
        output = (self.output / "40-visualization.Rmd").read_text()
        self.assertIn("    theme_minimal()\n", output)
        self.assertNotIn("    blue_theme()\n", output)


if __name__ == "__main__":
    unittest.main()
