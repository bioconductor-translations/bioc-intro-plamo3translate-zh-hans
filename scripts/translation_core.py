"""Lossless protection and reconstruction of translatable R Markdown prose.

The model sees prose with immutable inline values replaced by XML placeholders.
Fenced code, Carpentries div markers and document metadata never reach it.
This module deliberately has no MLX, Git, network or filesystem dependencies.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import json
import re
import unicodedata
from typing import Mapping


PLACEHOLDER = re.compile(r"<x(\d+)/>")
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})([^\r\n]*)")
DIV_FENCE = re.compile(r"^[ \t]*:{3,}")
PREFIX = re.compile(
    r"^([ \t]*(?:(?:[-+*]|\d+[.)])\s+|#{1,6}\s+|>\s*|"
    r"\[\^[^\]]+\]:\s*))"
)
SETEXT = re.compile(r"^[ \t]*(?:={2,}|-{2,})[ \t]*(?:\r?\n)?$")
# Order and spelling of these expressions preserve the original translation
# memory keys. More complicated code and destinations are scanned below.
PROTECTED = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]*(?:\\_[A-Za-z0-9]+)+"
    r"|\\[`*_{}\[\]()#+.!<>~-]"
    r"|`+[^`]*`+"
    r"|<kbd\b[^>]*>.*?</kbd>"
    r"|\]\((?:[^()\n]|\([^()\n]*\))*\)"
    r"|https?://[^\s<>\[\]]+"
    r"|\{#[^}]+\}"
    r"|\[\^[^\]]+\]"
    r"|@ref\([^)]+\)"
    r"|@[A-Za-z][A-Za-z0-9_.:-]*"
    r"|</?[A-Za-z][^>]*>", re.S
)


@dataclass
class Unit:
    id: int
    file: str
    source: str
    masked: str
    tokens: list[str]
    kind: str = "prose"
    context: str = ""


@dataclass
class _Piece:
    unit: int
    prefix: str
    suffix: str
    raw: str
    kind: str = "prose"
    slots: list[dict] = field(default_factory=list)


@dataclass
class Document:
    filename: str
    pieces: list[str | _Piece]
    units: list[Unit]
    source: str


def unit_key(unit: Unit | Mapping) -> str:
    """Stable identity, unaffected by a passage's line number or position.

    This is also the key used by the original reviewed translation memory.
    Model/prompt versions belong to the surrounding cache manifest.
    """
    masked = unit.masked if isinstance(unit, Unit) else unit["masked"]
    kind = unit.kind if isinstance(unit, Unit) else unit["kind"]
    return hashlib.sha256((masked + "\0" + kind).encode("utf-8")).hexdigest()


def _inline_spans(text: str) -> list[tuple[int, int]]:
    """Read arbitrary backtick delimiters and balanced link destinations."""
    spans: list[tuple[int, int]] = []
    html_literal = re.compile(
        r"<(?:pre|code|script|style)\b[^>]*>.*?</(?:pre|code|script|style)>"
        r"|<kbd\b[^>]*>.*?</kbd>|</?[A-Za-z][^>]*>", re.S | re.I
    )
    pos = 0
    while pos < len(text):
        html = html_literal.match(text, pos)
        if html:
            spans.append((pos, html.end()))
            pos = html.end()
            continue
        if text[pos] == "\\":
            pos += 2
            continue
        if text[pos] == "`":
            end = pos + 1
            while end < len(text) and text[end] == "`":
                end += 1
            delimiter = text[pos:end]
            closing = re.search(
                r"(?<!`)" + re.escape(delimiter) + r"(?!`)", text[end:]
            )
            if not closing:
                raise ValueError("Unclosed inline-code delimiter")
            stop = end + closing.end()
            spans.append((pos, stop))
            pos = stop
            continue
        if text.startswith("](", pos):
            depth, stop = 1, pos + 2
            while stop < len(text) and depth:
                if text[stop] == "\\":
                    stop += 2
                    continue
                if text[stop] == "(":
                    depth += 1
                elif text[stop] == ")":
                    depth -= 1
                stop += 1
            if depth:
                raise ValueError("Unclosed Markdown link destination")
            spans.append((pos, stop))
            pos = stop
            continue
        pos += 1
    return spans


def _mask_spans(text: str) -> list[tuple[int, int]]:
    special = _inline_spans(text)
    # HTML code is code even without backticks. Reference-link identifiers and
    # mathematical expressions are likewise immutable rather than prose.
    additions = re.compile(
        r"<(?:pre|code|script|style)\b[^>]*>.*?</(?:pre|code|script|style)>"
        r"|<!--.*?-->"
        r"|\[[^\[\]\n]+\]\[\]"
        r"|(?<=\])\[[^\[\]\n]*\]"
        r"|\[[^\[\]\r\n]+\](?![\[(])"
        r"|(?<!\\)\$\$.*?(?<!\\)\$\$"
        r"|(?<![\\$])\$(?!\$)[^$\n]+(?<!\\)\$"
        r"| {2,}\r?\n", re.S | re.I
    )
    candidates = special + [(m.start(), m.end()) for m in additions.finditer(text)]
    candidates += [(m.start(), m.end()) for m in PROTECTED.finditer(text)]
    candidates.sort(key=lambda span: (span[0], -span[1]))
    result: list[tuple[int, int]] = []
    for start, stop in candidates:
        if result and start < result[-1][1]:
            continue
        result.append((start, stop))
    return result


def mask(text: str) -> tuple[str, list[str]]:
    if PLACEHOLDER.search(text) or "<|plamo:" in text:
        raise ValueError("Source contains a reserved translation placeholder")
    tokens: list[str] = []
    output: list[str] = []
    cursor = 0
    for start, stop in _mask_spans(text):
        output.append(text[cursor:start])
        token = text[start:stop]
        left, right = "", ""
        if token.startswith("]("):
            token, left, right = token[2:-1], "](", ")"
        elif token.startswith(("http://", "https://")):
            while token and (
                token[-1] in ".,;:!?" or
                (token[-1] == ")" and token.count(")") > token.count("("))
            ):
                right = token[-1] + right
                token = token[:-1]
        output.append(f"{left}<x{len(tokens)}/>{right}")
        tokens.append(token)
        cursor = stop
    output.append(text[cursor:])
    return re.sub(r"\s*\n\s*", " ", "".join(output)).strip(), tokens


def unmask(text: str, tokens: list[str]) -> str:
    expected = Counter(f"<x{i}/>" for i in range(len(tokens)))
    actual = Counter(match[0] for match in PLACEHOLDER.finditer(text))
    if expected != actual:
        raise ValueError(
            f"Protected placeholders changed: missing {dict(expected - actual)}, "
            f"extra {dict(actual - expected)}"
        )
    if "<|plamo:" in text or re.search(r"<\s*/?x\d+\b", PLACEHOLDER.sub("", text)):
        raise ValueError("Model output contains malformed/reserved placeholders")

    def restore(match: re.Match) -> str:
        token = tokens[int(match[1])]
        if token.startswith("{#"):
            return " " + token
        # CJK letters are valid citation-key characters to Pandoc.
        # Whitespace prevents an adjacent CJK word from extending the ID.
        return " " + token + " " if token.startswith("@") else token

    return PLACEHOLDER.sub(restore, text)


def _append_literal(pieces: list, text: str) -> None:
    if pieces and isinstance(pieces[-1], str):
        pieces[-1] += text
    else:
        pieces.append(text)


def _table_cells(line: str) -> list[str]:
    """Split pipe tables, ignoring pipes escaped or inside inline code."""
    body = line.rstrip("\r\n")
    positions = []
    cursor, delimiter = 0, None
    while cursor < len(body):
        char = body[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "`":
            end = cursor + 1
            while end < len(body) and body[end] == "`":
                end += 1
            run = body[cursor:end]
            if delimiter is None:
                delimiter = run
            elif delimiter == run:
                delimiter = None
            cursor = end
            continue
        if char == "|" and delimiter is None:
            positions.append(cursor)
        cursor += 1
    if delimiter:
        raise ValueError("Unclosed inline code in table row")
    if not positions or body[:positions[0]].strip() or body[positions[-1]+1:].strip():
        raise ValueError("Only pipe tables with outer | delimiters are supported")
    return [body[a+1:b] for a, b in zip(positions, positions[1:])]


def _table_policy(rows: list[list[str]]) -> set[int]:
    header, data = rows[0], rows[2:]
    narrative = set()
    for index, heading in enumerate(header):
        if index == 0:
            continue
        if re.search(r"\b(description|notes?|explanation|meaning|definition|details)\b",
                     heading, re.I):
            narrative.add(index)
        elif any(len(row[index].split()) >= 6 for row in data):
            narrative.add(index)
    return narrative


def _commented_fence(line: str) -> tuple[str, str] | None:
    match = re.match(r"^[ \t]*<!--[ \t]*(`{3,}|~{3,})(.*)", line)
    if not match:
        return None
    tail = re.sub(r"[ \t]*(?:-->)?[\r\n]*$", "", match[2]).strip()
    return match[1], tail


def _comment_groups(source: str) -> list[dict]:
    records: list[dict] = []
    offset = 0
    code_marker = None
    html_tag = None
    commented_code = False
    narrative_open = False
    for number, raw in enumerate(source.splitlines(keepends=True), 1):
        position = offset
        offset += len(raw)
        fence = FENCE.match(raw)
        if html_tag is not None:
            if re.search(r"</" + html_tag + r">", raw, re.I):
                html_tag = None
            continue
        if code_marker is not None:
            if fence and fence[1][0] == code_marker[0] and len(fence[1]) >= len(code_marker) and not fence[2].strip():
                code_marker = None
            continue
        if fence and not narrative_open:
            code_marker = fence[1]
            continue
        html = re.match(r"^[ \t]*<(script|style|pre|code)\b", raw, re.I)
        if html and not narrative_open:
            if not re.search(r"</" + html[1] + r">", raw, re.I):
                html_tag = html[1]
            continue
        commented = _commented_fence(raw)
        if commented:
            if not commented_code and commented[1]:
                commented_code = True
            elif commented_code and not commented[1]:
                commented_code = False
            continue
        if commented_code:
            continue
        opened = bool(re.match(r"^[ \t]*<!--", raw))
        if not narrative_open and not opened:
            continue
        closed = "-->" in raw
        body = raw.rstrip("\r\n")
        start_match = re.match(r"[ \t]*<!--[ \t]*" if opened else r"[ \t]*", body)
        suffix_match = re.search(r"[ \t]*-->[ \t]*$" if closed else r"[ \t]*$", body)
        start, end = start_match.end(), suffix_match.start()
        if end < start:
            raise ValueError(f"Malformed HTML comment on line {number}")
        records.append(dict(
            line=number, start=position, end=offset,
            prefix=body[:start], content=body[start:end],
            suffix=body[end:] + raw[len(body):],
        ))
        narrative_open = not closed
    if code_marker is not None or commented_code or narrative_open or html_tag:
        raise ValueError("Unclosed code block or narrative HTML comment")

    groups: list[list[dict]] = []
    marker_pattern = re.compile(r"^(?:[-+*]\s+|\d+[.)]\s+|#{1,6}\s+)")
    for record in records:
        previous = groups[-1][-1] if groups else None
        first_marker = marker_pattern.match(groups[-1][0]["content"]) if groups else None
        heading = first_marker and first_marker[0].lstrip().startswith("#")
        if (not previous or source[previous["end"]:record["start"]].strip()
                or marker_pattern.match(record["content"]) or heading):
            groups.append([record])
        else:
            groups[-1].append(record)
    result = []
    for slots in groups:
        marker = marker_pattern.match(slots[0]["content"])
        if marker:
            slots[0] = dict(slots[0])
            slots[0]["prefix"] += marker[0]
            slots[0]["content"] = slots[0]["content"][len(marker[0]):]
        start, stop = slots[0]["start"], slots[-1]["end"]
        result.append(dict(start=start, stop=stop, raw=source[start:stop], slots=slots,
                           source="\n".join(slot["content"] for slot in slots)))
    return result


def extract_document(text: str, filename: str) -> Document:
    """Extract prose while retaining enough source data for exact protection.

    Unsupported/unclosed structures raise ValueError before model inference.
    Narrative comments are grouped across their soft-wrapped HTML wrappers.
    """
    if "<|plamo:" in text or PLACEHOLDER.search(text):
        raise ValueError(f"{filename}: reserved model tokens in source")
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    pieces: list[str | _Piece] = []
    units: list[Unit] = []

    def literal(value: str) -> None:
        _append_literal(pieces, value)

    def add_unit(value: str, raw: str, prefix: str = "", suffix: str | None = None,
                 kind: str = "prose") -> None:
        masked, tokens = mask(value)
        if not re.search(r"[A-Za-z]{2}", PLACEHOLDER.sub("", masked)):
            literal(raw)
            return
        index = len(units)
        units.append(Unit(index, filename, value, masked, tokens, kind))
        pieces.append(_Piece(index, prefix, newline if suffix is None else suffix, raw, kind))

    i = 0
    if lines and lines[0].strip() == "---":
        literal(lines[0])
        i = 1
        closed = False
        while i < len(lines):
            line = lines[i]
            i += 1
            if line.strip() == "---":
                literal(line)
                closed = True
                break
            if line.startswith("title:"):
                value = line.split(":", 1)[1].strip()
                if not value or value.startswith(("|", ">")):
                    raise ValueError(f"{filename}: use a single-line YAML title")
                if value.startswith('"'):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError as error:
                        raise ValueError("Invalid quoted YAML title") from error
                elif value.startswith("'"):
                    if not value.endswith("'"):
                        raise ValueError("Unclosed quoted YAML title")
                    value = value[1:-1].replace("''", "'")
                add_unit(value, line, "title: ", kind="title")
            else:
                literal(line)
        if not closed:
            raise ValueError(f"{filename}: unclosed YAML front matter")

    while i < len(lines):
        line = lines[i]
        start = i
        fence = FENCE.match(line)
        if fence:
            marker = fence[1]
            block = [line]
            i += 1
            while i < len(lines):
                block.append(lines[i])
                candidate = FENCE.match(lines[i])
                i += 1
                if candidate and candidate[1][0] == marker[0] and len(candidate[1]) >= len(marker) and not candidate[2].strip():
                    break
            else:
                raise ValueError(f"{filename}:{start+1}: unclosed code fence")
            literal("".join(block))
            continue
        # Do not send reference definitions, indented code, horizontal rules or
        # commented code to the model. Narrative comments are inserted below.
        reference = re.match(r"^[ \t]{0,3}\[[^\]]+\]:\s*\S", line)
        is_footnote = re.match(r"^[ \t]*\[\^", line)
        nested_list = re.match(r"^[ \t]*(?:[-+*]|\d+[.)])\s+", line)
        indented = re.match(r"^(?: {4}|\t)", line) and not nested_list
        raw_html = re.match(r"^[ \t]*<(script|style|pre|code)\b", line, re.I)
        if raw_html:
            tag = raw_html[1]
            while True:
                literal(lines[i])
                i += 1
                if re.search(r"</" + tag + r">", lines[i-1], re.I):
                    break
                if i == len(lines):
                    raise ValueError(f"{filename}:{start+1}: unclosed HTML code block")
            continue
        if (not line.strip() or DIV_FENCE.match(line) or SETEXT.match(line)
                or re.match(r"^[ \t]*(?:<!--|-->)", line)
                or indented or (reference and not is_footnote)):
            literal(line)
            i += 1
            if "<!--" in line and "-->" not in line and "```" not in line and "~~~" not in line:
                while i < len(lines):
                    literal(lines[i])
                    i += 1
                    if "-->" in lines[i-1]:
                        break
                else:
                    raise ValueError(f"{filename}:{start+1}: unclosed HTML comment")
            continue
        if line.lstrip().startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            rows = [_table_cells(row) for row in table_lines]
            if len(rows) < 2 or not all(re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in rows[1]):
                raise ValueError(f"{filename}:{start+1}: malformed pipe table")
            if any(len(row) != len(rows[0]) for row in rows):
                raise ValueError(f"{filename}:{start+1}: inconsistent table columns")
            narrative = _table_policy(rows)
            for number, (raw, cells) in enumerate(zip(table_lines, rows)):
                if number == 1:
                    literal(raw)
                    continue
                leading = raw[:raw.index("|")+1]
                literal(leading)
                for column, cell in enumerate(cells):
                    if number and column not in narrative:
                        literal(cell + "|")
                    else:
                        add_unit(cell.strip(), cell + "|", " ", " |", "table")
                literal(raw[raw.rfind("|")+1:])
            continue
        match = PREFIX.match(line)
        prefix = match[1] if match else ""
        contents = [line[len(prefix):].rstrip("\r\n")]
        i += 1
        heading = bool(re.match(r"^[ \t]*#{1,6}\s", prefix))
        if prefix.lstrip().startswith(">") and contents[0].lstrip().startswith(">"):
            raise ValueError(f"{filename}:{start+1}: nested blockquotes need review")
        while not heading and i < len(lines):
            following = lines[i]
            indentation = re.match(r"^[ \t]+", following)
            width = len(indentation[0].expandtabs(4)) if indentation else 0
            continuation_width = 0
            if re.match(r"^[ \t]*\[\^", prefix):
                continuation_width = 4
            elif re.match(r"^[ \t]*(?:[-+*]|\d+[.)])\s", prefix):
                continuation_width = len(prefix.expandtabs(4))
            indented_code = bool(re.match(r"^(?: {4}|\t)", following))
            if continuation_width and continuation_width <= width < continuation_width + 4:
                indented_code = False
            boundary = (not following.strip() or PREFIX.match(following)
                        or FENCE.match(following) or DIV_FENCE.match(following)
                        or SETEXT.match(following)
                        or re.match(r"^[ \t]*(?:<!--|\||<(?:script|pre|code|style)\b)", following)
                        or indented_code)
            if boundary:
                if prefix.lstrip().startswith(">") and re.match(r"^>\s+\S", following):
                    contents.append(re.sub(r"^>\s*", "", following).rstrip("\r\n"))
                    i += 1
                    continue
                break
            contents.append(following.rstrip("\r\n").lstrip())
            i += 1
        raw = "".join(lines[start:i])
        suffix = newline if raw.endswith(("\n", "\r")) else ""
        add_unit("\n".join(contents), raw, prefix, suffix)

    # Comments can share literal pieces with blank lines or fenced code. Match
    # their original character offsets, never search/replace arbitrary text.
    groups = _comment_groups(text)
    if groups:
        rebuilt: list[str | _Piece] = []
        offset, group_index = 0, 0
        for piece in pieces:
            raw = piece if isinstance(piece, str) else piece.raw
            stop = offset + len(raw)
            cursor = offset
            while group_index < len(groups) and groups[group_index]["start"] < stop:
                group = groups[group_index]
                if not isinstance(piece, str) or group["start"] < offset or group["stop"] > stop:
                    raise ValueError("Narrative comment overlaps translatable content")
                _append_literal(rebuilt, raw[cursor-offset:group["start"]-offset])
                masked, tokens = mask(group["source"])
                if re.search(r"[A-Za-z]{2}", PLACEHOLDER.sub("", masked)):
                    index = len(units)
                    units.append(Unit(index, filename, group["source"], masked, tokens, "comment"))
                    rebuilt.append(_Piece(index, "", "", group["raw"], "comment", group["slots"]))
                else:
                    _append_literal(rebuilt, group["raw"])
                cursor = group["stop"]
                group_index += 1
            if isinstance(piece, str):
                _append_literal(rebuilt, raw[cursor-offset:])
            else:
                rebuilt.append(piece)
            offset = stop
        if group_index != len(groups) or offset != len(text):
            raise ValueError("Source reconstruction offsets did not match")
        pieces = rebuilt
    return Document(filename, pieces, units, text)


def _partition(text: str, count: int) -> list[str]:
    atoms: list[str] = []
    for value in re.findall(r"<x\d+/>|.", text, re.S):
        if atoms and len(value) == 1 and (unicodedata.combining(value) or value in "\ufe0e\ufe0f"):
            atoms[-1] += value
        else:
            atoms.append(value)
    chunks, start = [], 0
    for index in range(count-1):
        ideal = start + round((len(atoms)-start)/(count-index))
        if ideal <= start:
            chunks.append("")
            continue
        radius = max(2, round((ideal-start)/4))
        candidates = range(max(start+1, ideal-radius), min(len(atoms), ideal+radius)+1)
        friendly = [end for end in candidates if atoms[end-1][-1:] in " 。、.!?！？;；"]
        end = min(friendly, key=lambda end: abs(end-ideal)) if friendly else min(ideal, len(atoms))
        chunks.append("".join(atoms[start:end]).strip())
        start = end
    chunks.append("".join(atoms[start:]).strip())
    return chunks


def _render_comment(piece: _Piece, unit: Unit, translated: str) -> str:
    unmask(translated, unit.tokens)
    chunks = _partition(translated, len(piece.slots))
    output, cursor = [], piece.slots[0]["start"]
    origin = cursor
    for slot, chunk in zip(piece.slots, chunks):
        output.append(piece.raw[cursor-origin:slot["start"]-origin])
        chunk = PLACEHOLDER.sub(lambda match: unit.tokens[int(match[1])], chunk)
        if "<!--" in chunk or "-->" in chunk:
            raise ValueError("A comment translation contains HTML comment markers")
        output.append(slot["prefix"] + chunk + slot["suffix"])
        cursor = slot["end"]
    output.append(piece.raw[cursor-origin:])
    return "".join(output)


def render_document(doc: Document, translations: Mapping[str, str]) -> str:
    output = []
    for piece in doc.pieces:
        if isinstance(piece, str):
            output.append(piece)
            continue
        unit = doc.units[piece.unit]
        key = unit_key(unit)
        if key not in translations:
            raise ValueError(f"{doc.filename}: missing translation for {key}")
        translated = re.sub(r"\s*\n\s*", " ", translations[key].strip())
        if not translated:
            raise ValueError(f"{doc.filename}: empty translation for {key}")
        if piece.kind == "comment":
            output.append(_render_comment(piece, unit, translated))
            continue
        body = unmask(translated, unit.tokens).rstrip()
        if re.match(r"^[ \t]*#{1,6}\s", piece.prefix):
            identifiers = [token for token in unit.tokens if token.startswith("{#")]
            if identifiers:
                body = re.sub(r"\s*\{#[^}]+\}\s*", "", body).strip()
                body += " " + " ".join(identifiers)
        if piece.kind == "title":
            body = json.dumps(body, ensure_ascii=False)
        elif "\n" in body or "\r" in body:
            # Only protected hard breaks can remain after model whitespace is
            # normalized. Indent their continuation within a list/footnote.
            continuation = ""
            if piece.prefix.lstrip().startswith(">"):
                continuation = piece.prefix
            elif piece.prefix:
                continuation = " " * len(piece.prefix)
            body = re.sub(r"\r?\n", lambda match: match[0] + continuation, body)
        output.append(piece.prefix + body + piece.suffix)
    result = "".join(output)
    validate_document(doc.source, result, doc.filename)
    return result


def _snapshot(text: str, filename: str,
              table_policies: list[set[int]] | None = None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    lines = text.splitlines(keepends=True)
    offset = 0
    blocked: list[tuple[int, int]] = []
    code_start = None
    code_marker = None
    commented_start = None
    for number, line in enumerate(lines, 1):
        start, offset = offset, offset + len(line)
        fence = FENCE.match(line)
        if code_start is not None:
            if fence and fence[1][0] == code_marker[0] and len(fence[1]) >= len(code_marker) and not fence[2].strip():
                result["code blocks"].append(text[code_start:offset])
                blocked.append((code_start, offset))
                code_start = code_marker = None
            continue
        if fence:
            code_start, code_marker = start, fence[1]
            continue
        if DIV_FENCE.match(line):
            result["Carpentries fences"].append(line)
        commented = _commented_fence(line)
        if commented:
            if commented_start is None and commented[1]:
                commented_start = start
            elif commented_start is not None and not commented[1]:
                result["commented code"].append(text[commented_start:offset])
                blocked.append((commented_start, offset))
                commented_start = None
    if code_start is not None or commented_start is not None:
        raise ValueError(f"{filename}: unclosed code fence")
    # Conservatively retained indented code can contain characters which are
    # Markdown delimiters only outside code (for example a literal backtick).
    position = 0
    for piece in extract_document(text, filename).pieces:
        raw = piece if isinstance(piece, str) else piece.raw
        if isinstance(piece, str):
            line_start = position
            for line in raw.splitlines(keepends=True):
                if re.match(r"^(?: {4}|\t)", line):
                    blocked.append((line_start, line_start + len(line)))
                line_start += len(line)
        position += len(raw)
    chars = list(text)
    for start, stop in blocked:
        chars[start:stop] = ["\n" if char == "\n" else " " for char in text[start:stop]]
    outside = "".join(chars)
    inline_spans = _inline_spans(outside)
    destinations = []
    for start, stop in inline_spans:
        value = outside[start:stop]
        if value.startswith("]("):
            category = "link destinations"
        elif value.startswith("<"):
            category = "HTML literals"
        else:
            category = "inline code"
        result[category].append(value)
        destinations.append((start, stop))
    pattern = re.compile(
        r"<kbd>.*?</kbd>|\{#[^}\r\n]+\}|\[\^[^\]\r\n]+\]"
        r"|@ref\([^)\r\n]+\)|@[A-Za-z][A-Za-z0-9_.:-]*"
        r"|</?[A-Za-z][^>\r\n]*>", re.S
    )
    inline_blank = list(outside)
    for start, stop in destinations:
        inline_blank[start:stop] = ["\n" if c == "\n" else " " for c in outside[start:stop]]
    remaining = "".join(inline_blank)
    result["inline metadata"] = [match[0] for match in pattern.finditer(remaining)]
    result["additional inline literals"] = re.findall(
        r"<(?:pre|code|script|style)\b[^>]*>.*?</(?:pre|code|script|style)>"
        r"|(?<!\\)\$\$.*?(?<!\\)\$\$"
        r"|(?<![\\$])\$(?!\$)[^$\n]+(?<!\\)\$"
        r"|(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*(?:\\_[A-Za-z0-9]+)+"
        r"|\\[`*_{}\[\]()#+.!<>~-]", remaining, re.S | re.I
    )
    result["reference labels"] = re.findall(
        r"\[[^\[\]\r\n]+\]\[\]|\[[^\[\]\r\n]+\](?![\[(])", remaining
    )
    # Bare URL labels and destinations are checked independently: translated
    # labels may move, while every original URL must remain literal.
    for match in re.finditer(r"https?://[^\s<>\[\]]+", remaining):
        value = match[0].rstrip(".,;:!?)\"]}")
        result["bare URLs"].append(value)
    for group in _comment_groups(outside):
        result["comment wrappers"].extend(re.findall(r"<!--|-->", group["raw"]))
    for match in re.finditer(r"<!--.*?-->", outside, re.S):
        line_start = outside.rfind("\n", 0, match.start()) + 1
        if outside[line_start:match.start()].strip():
            result["inline comments"].append(match[0])

    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError(f"{filename}: unclosed YAML front matter")
        result["YAML except title"] = [line for line in lines[:end+1] if not line.startswith("title:")]

    # Check structural markers without constraining translated line wrapping.
    clean_lines = outside.splitlines(keepends=True)
    for line in clean_lines:
        if re.match(r"^[ \t]*<!--", line):
            continue
        heading = re.match(r"^[ \t]*(#{1,6})\s+", line)
        if heading:
            result["heading levels"].append(heading[1])
        marker = re.match(r"^([ \t]*(?:[-+*]|\d+[.)])\s+)", line)
        if marker:
            result["list markers"].append(marker[1])
        reference = re.match(r"^[ \t]{0,3}\[[^\]]+\]:\s*\S", line)
        if reference and not re.match(r"^[ \t]*\[\^", line):
            result["reference definitions"].append(line)

    index = 0
    table_count = 0
    while index < len(clean_lines):
        if not clean_lines[index].lstrip().startswith("|"):
            index += 1
            continue
        table = []
        while index < len(clean_lines) and clean_lines[index].lstrip().startswith("|"):
            table.append(_table_cells(clean_lines[index]))
            index += 1
        if len(table) < 2:
            raise ValueError(f"{filename}: incomplete table")
        if table_policies is not None:
            if table_count >= len(table_policies):
                raise ValueError(f"{filename}: unexpected extra table")
            narrative = table_policies[table_count]
        else:
            narrative = _table_policy(table)
        table_count += 1
        result["table policies"].append(json.dumps(sorted(narrative)))
        result["table shape"].extend(str(len(row)) for row in table)
        result["table separators"].append("|".join(cell.strip() for cell in table[1]))
        for row in table[2:]:
            result["table literals"].extend(cell.strip() for col, cell in enumerate(row) if col not in narrative)
    return dict(result)


def validate_document(source: str, output: str, filename: str = "episode.Rmd") -> None:
    """Reject any changed protected content before a caller writes output."""
    if PLACEHOLDER.search(output) or "<|plamo:" in output:
        raise ValueError(f"{filename}: untranslated model placeholders remain")
    before = _snapshot(source, filename)
    table_policies = [set(json.loads(item)) for item in before.get("table policies", [])]
    after = _snapshot(output, filename, table_policies)
    unordered = {"inline code", "HTML literals", "inline metadata", "link destinations", "bare URLs",
                 "reference labels", "additional inline literals"}
    for category in before.keys() | after.keys():
        expected, actual = before.get(category, []), after.get(category, [])
        equal = Counter(expected) == Counter(actual) if category in unordered else expected == actual
        if not equal:
            raise ValueError(f"{filename}: protected {category} changed")
    # This also protects conservative literal regions such as indented code,
    # raw HTML code blocks and reference definitions. Ignore surrounding blank
    # lines so ordinary prose reflow does not constrain validation.
    cursor = 0
    for piece in extract_document(source, filename).pieces:
        if not isinstance(piece, str) or not piece.strip():
            continue
        literal = piece.strip("\r\n")
        found = output.find(literal, cursor)
        if found < 0:
            raise ValueError(f"{filename}: protected literal region changed")
        cursor = found + len(literal)
