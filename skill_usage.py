#!/usr/bin/env python3
"""Offline statistics of confirmed SKILL.md reads in local Codex session logs.

The numbers are a conservative lower bound of confirmed reads of a skill's
SKILL.md. They are NOT successful skill invocations or harness completions.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
SOURCE = "codex-session-logs"
METRIC = "confirmed-skill-reads"
SCOPE = "skill-name-across-local-codex-sessions"
WINDOWS = (7, 30)
MAX_LINE_BYTES = 8 * 1024 * 1024
STALE_AFTER = timedelta(hours=24)
CLOCK_TOLERANCE = timedelta(minutes=5)
COVERAGE_KEYS = (
    "files_scanned", "malformed_lines", "unreadable_files", "oversized_lines",
    "unconfirmed_reads", "duplicate_events", "inherited_events",
)
PARTIAL_KEYS = ("malformed_lines", "unreadable_files", "oversized_lines", "unconfirmed_reads")
REPORT_KEYS = {"schema_version", "source", "metric", "scope", "generated_at",
               "window_end", "coverage", "skills"}
STAT_KEYS = {"reads", "conversations", "last_read"}


class ReportError(ValueError):
    """Raised when a usage report does not satisfy the contract."""


# ---------------------------------------------------------------- helpers
def parse_time(value, *, assume_utc=False) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a string")
    text = value[:-1] + "+00:00" if value[-1] in "Zz" else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        if not assume_utc:
            raise ValueError("timestamp must be timezone-aware")
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _aware_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _reject_duplicates(pairs):
    result = {}
    for key, val in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = val
    return result


def _reject_constant(name):
    raise ValueError(f"invalid JSON constant: {name}")


def loads_strict(text: str):
    return json.loads(text, object_pairs_hook=_reject_duplicates,
                      parse_constant=_reject_constant)


def load_report(path: Path):
    """Load a report file; returns None when it does not exist."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return loads_strict(text)


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _check_time(value, what):
    try:
        return parse_time(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ReportError(f"{what}: invalid timestamp") from exc


# ------------------------------------------------------------- validation
def _validate_stat(stat, days, window_end, what):
    if not isinstance(stat, dict) or set(stat) != STAT_KEYS:
        raise ReportError(f"{what}: invalid keys")
    reads, convs, last = stat["reads"], stat["conversations"], stat["last_read"]
    if not _is_count(reads) or not _is_count(convs):
        raise ReportError(f"{what}: counts must be non-negative integers")
    if convs > reads:
        raise ReportError(f"{what}: conversations exceed reads")
    if reads == 0:
        if convs or last is not None:
            raise ReportError(f"{what}: empty stat must have no conversations or last_read")
        return 0, 0, None
    if convs < 1 or last is None:
        raise ReportError(f"{what}: reads require conversations and last_read")
    when = _check_time(last, what)
    if not window_end - timedelta(days=days) <= when < window_end:
        raise ReportError(f"{what}: last_read outside window")
    return reads, convs, when


def validate_report(report) -> dict:
    if not isinstance(report, dict) or not REPORT_KEYS <= set(report) <= REPORT_KEYS | {"is_fixture"}:
        raise ReportError("report keys are invalid")
    if (not _is_count(report["schema_version"]) or report["schema_version"] != SCHEMA_VERSION):
        raise ReportError("unsupported schema_version")
    for key, expected in (("source", SOURCE), ("metric", METRIC), ("scope", SCOPE)):
        if report[key] != expected or not isinstance(report[key], str):
            raise ReportError(f"invalid {key}")
    if "is_fixture" in report and report["is_fixture"] is not True:
        raise ReportError("is_fixture must be true when present")
    generated = _check_time(report["generated_at"], "generated_at")
    window_end = _check_time(report["window_end"], "window_end")
    if generated + CLOCK_TOLERANCE < window_end:
        raise ReportError("snapshot ends after generation")
    coverage = report["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != set(COVERAGE_KEYS):
        raise ReportError("coverage keys are invalid")
    if not all(_is_count(coverage[key]) for key in COVERAGE_KEYS):
        raise ReportError("coverage values must be non-negative integers")
    skills = report["skills"]
    if not isinstance(skills, dict):
        raise ReportError("skills must be an object")
    for name, entry in skills.items():
        if not isinstance(name, str) or not name:
            raise ReportError("invalid skill name")
        if not isinstance(entry, dict) or set(entry) != {str(d) for d in WINDOWS}:
            raise ReportError(f"{name}: windows must be exactly 7 and 30")
        r7, c7, l7 = _validate_stat(entry["7"], 7, window_end, f"{name}/7")
        r30, c30, l30 = _validate_stat(entry["30"], 30, window_end, f"{name}/30")
        if (r7 > r30 or c7 > c30 or (l7 is not None and l7 != l30)
                or (l7 is None and l30 is not None and l30 >= window_end - timedelta(days=7))):
            raise ReportError(f"{name}: 7-day window exceeds 30-day window")
    return report


# ----------------------------------------------------------------- assess
def assess(report, name, days, harness="codex", now=None) -> dict:
    if type(days) is not int or days not in WINDOWS:
        raise ValueError("days must be 7 or 30")
    if not isinstance(name, str) or not name:
        raise ValueError("name must be a non-empty string")
    now = _aware_utc(now)
    result = {
        "status": "unavailable", "reads": None, "conversations": None,
        "last_read": None, "generated_at": None, "window_end": None,
        "scope": SCOPE, "source": SOURCE, "coverage": None, "partial": False,
        "stale": False, "is_fixture": False, "days": days,
    }
    if harness != "codex":
        result["status"] = "unsupported"
        return result
    if report is None:
        return result
    try:
        validate_report(report)
        window_end = parse_time(report["window_end"])
        if window_end > now + CLOCK_TOLERANCE:
            raise ReportError("window_end is in the future")
    except (ReportError, OverflowError, RecursionError) as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result
    stat = report["skills"].get(name, {}).get(str(days), {"reads": 0, "conversations": 0, "last_read": None})
    coverage = dict(report["coverage"])
    result.update(
        status="ready", reads=stat["reads"], conversations=stat["conversations"],
        last_read=stat["last_read"], generated_at=report["generated_at"],
        window_end=report["window_end"], coverage=coverage,
        partial=any(coverage[key] for key in PARTIAL_KEYS),
        stale=now - window_end > STALE_AFTER,
        is_fixture=report.get("is_fixture") is True,
    )
    return result


# -------------------------------------------------- read-command parsing
_QUOTED = r"""(?:"(?:[^"\\]|\\.)*"|'[^']*')"""
_TOKEN = re.compile(_QUOTED + r"""|[^\s"']+""")
_QUOTE_RE = re.compile(_QUOTED)
_SHELLS = {"powershell", "pwsh", "bash", "sh", "zsh", "cmd"}
_SHELL_FLAGS = {"-c", "-lc", "-command", "/c", "-noprofile", "-nologo", "-l"}
_PYTHONS = {"python", "python3", "py"}
_SKILL_PATH = re.compile(r"(?:^|/)([^/]+)/SKILL\.md$")
_READ_ALL_TEXT = re.compile(
    r"\[(?:System\.)?IO\.File\]::ReadAllText\(\s*(" + _QUOTED + r")\s*(?:,\s*[\w.\[\]:]+\s*)?\)")
_PY_READ_TEXT = re.compile(
    r"(?:from\s+pathlib\s+import\s+Path\s*;\s*)?(?:print\(\s*)?Path\(\s*(" + _QUOTED
    + r")\s*\)\.read_text\(\s*(?:encoding\s*=\s*" + _QUOTED + r")?\s*\)\s*\)?")
_NESTED_CMD = re.compile(r"tools\.exec_command\(\s*\{\s*cmd\s*:\s*(" + _QUOTED + ")")
_SED_SCRIPT = re.compile(r"\d+(?:,\d+)?p")
# command -> (flags without value, flags with value, flags followed by the path)
_READERS = {
    "get-content": ({"-raw"}, {"-encoding", "-totalcount", "-head", "-first", "-tail"}, {"-path", "-literalpath"}),
    "gc": ({"-raw"}, {"-encoding", "-totalcount", "-head", "-first", "-tail"}, {"-path", "-literalpath"}),
    "cat": ({"-n"}, set(), set()),
    "head": (set(), {"-n"}, set()),
    "sed": ({"-n"}, set(), set()),
}


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _literal(token: str):
    """Decode a quoted string literal safely; None when unsupported."""
    try:
        value = ast.literal_eval(token)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def skill_name_from_path(path) -> str | None:
    if not isinstance(path, str) or re.search(r"[$%*?{}()`\r\n]", path):
        return None
    match = _SKILL_PATH.search(path.replace("\\", "/"))
    if not match or match.group(1) in {".", ".."}:
        return None
    return match.group(1)


def _simple_reader_path(tokens: list[str]) -> str | None:
    spec = _READERS.get(_unquote(tokens[0]).lower())
    if spec is None:
        return None
    plain, valued, pathy = spec
    args, paths, i = [_unquote(t) for t in tokens[1:]], [], 0
    is_head = _unquote(tokens[0]).lower() == "head"
    while i < len(args):
        arg, low = args[i], args[i].lower()
        if low in pathy and i + 1 < len(args):
            paths.append(args[i + 1])
            i += 2
        elif low in valued and i + 1 < len(args):
            i += 2
        elif low in plain or (is_head and re.fullmatch(r"-\d+", arg)):
            i += 1
        elif _unquote(tokens[0]).lower() == "sed" and _SED_SCRIPT.fullmatch(arg):
            i += 1
        elif arg.startswith("-"):
            return None
        else:
            paths.append(arg)
            i += 1
    return paths[0] if len(paths) == 1 else None


def paths_in_command(cmd: str, depth: int = 0) -> list[str]:
    """Return SKILL.md paths that a single simple read command reads."""
    if not isinstance(cmd, str):
        return []
    cmd = cmd.strip()
    for pattern in (_READ_ALL_TEXT, _PY_READ_TEXT):
        match = pattern.fullmatch(cmd)
        if match:
            return [_unquote(match.group(1))]
    if re.search(r"[;&|<>`$\r\n]", _QUOTE_RE.sub('""', cmd)):
        return []
    tokens = _TOKEN.findall(cmd)
    if not tokens:
        return []
    head = _unquote(tokens[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
    head = head[:-4] if head.endswith(".exe") else head
    if depth == 0 and len(tokens) >= 3 and (head in _SHELLS or head in _PYTHONS):
        flags = {t.lower() for t in tokens[1:-1]}
        if (head in _SHELLS and flags and flags <= _SHELL_FLAGS) or (head in _PYTHONS and flags == {"-c"}):
            return paths_in_command(_unquote(tokens[-1]), depth + 1)
        return []
    path = _simple_reader_path(tokens)
    return [path] if path else []


def literal_read_paths(command):
    """Split literal shell statements without treating quoted examples as reads."""
    if not isinstance(command, str):
        return []
    pieces = re.findall(_QUOTED + r"|[^;'\"\r\n]+|[;\r\n]", command)
    if ''.join(pieces) != command:  # Unterminated or unsupported quoting.
        return []
    result, statement, commented = [], [], False
    for token in pieces + ['\n']:
        if token in (';', '\r', '\n'):
            if not commented:
                result.extend(paths_in_command(''.join(statement)))
            statement = []
            if token != ';':
                commented = False
        elif not commented:
            if token[:1] not in ('"', "'") and '#' in token:
                commented = True
            statement.append(token)
    return result


def skills_from_call(name: str, arguments) -> tuple[list[str], bool]:
    """Return (skill names, needs_exec_exit_code) for a tool call."""
    name = name.rsplit(".", 1)[-1] if isinstance(name, str) else ""
    commands, native = [], []
    if name in {"exec_command", "shell_command"} and isinstance(arguments, dict):
        commands.append(arguments.get("cmd", arguments.get("command")))
    elif name == "exec" and isinstance(arguments, str):
        commands.extend(_literal(m) for m in _NESTED_CMD.findall(arguments))
    elif name in {"read", "read_file"} and isinstance(arguments, dict):
        native.append(arguments.get("path", arguments.get("file_path")))
    paths = [p for cmd in commands for p in literal_read_paths(cmd)] + native
    names = []
    for path in paths:
        skill = skill_name_from_path(path)
        if skill and skill not in names:
            names.append(skill)
    return names, bool(commands)


# ------------------------------------------------------- output parsing
_EXIT_TEXT = re.compile(r"(?:Process exited with code|exit_code\"?\s*[:=])\s*(-?\d+)")
_CHUNK_SPLIT = re.compile(r"(?m)^(?=Chunk ID:)")
_FRONTMATTER = re.compile(r"(?:^|\n)---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
_FM_NAME = re.compile(r"^name:[ \t]*(.+?)[ \t]*$", re.M)


def _exit_code(node, inherited):
    if isinstance(node.get("exit_code"), int) and not isinstance(node["exit_code"], bool):
        return node["exit_code"]
    meta = node.get("metadata")
    if isinstance(meta, dict) and type(meta.get("exit_code")) is int:
        return meta["exit_code"]
    return inherited


def _walk(value, exit_code, failed, out, depth=0):
    """Collect (text, exit_code, failed) chunks, decoding JSON wrappers."""
    if depth > 8:
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[":
            try:
                return _walk(loads_strict(stripped), exit_code, failed, out, depth + 1)
            except ValueError:
                return
        if "\\n" in value and "\n" not in value:
            value = value.replace("\\r\\n", "\n").replace("\\n", "\n")
        for chunk in _CHUNK_SPLIT.split(value):
            match = _EXIT_TEXT.search(chunk)
            code = exit_code if exit_code is not None else (int(match.group(1)) if match else None)
            out.append((chunk, code, failed))
    elif isinstance(value, list):
        for item in value:
            _walk(item, exit_code, failed, out, depth + 1)
    elif isinstance(value, dict):
        failed = (failed or value.get("isError") is True or bool(value.get("error"))
                  or ('exit_code' in value and type(value['exit_code']) is not int)
                  or value.get("status") in ("error", "failed", "cancelled", "timed_out"))
        code = _exit_code(value, exit_code)
        failed = failed or (code is not None and code != 0)
        for key in ("output", "text", "content"):
            if key in value:
                _walk(value[key], code, failed, out, depth + 1)


def confirmed_names(output, skills: list[str], needs_exit_code: bool) -> set[str]:
    """Skill names whose SKILL.md frontmatter appears in a successful chunk."""
    chunks: list = []
    _walk(output, None, False, chunks)
    confirmed = set()
    for text, code, failed in chunks:
        if failed or (code is not None and code != 0) or (needs_exit_code and code != 0):
            continue
        for match in _FRONTMATTER.finditer(text):
            found = _FM_NAME.search(match.group(1))
            if found and _unquote(found.group(1).strip()) in skills:
                confirmed.add(_unquote(found.group(1).strip()))
    return confirmed


# ------------------------------------------------------------ collection
def is_link(path) -> bool:
    """True for symlinks and Windows junctions/reparse points."""
    if os.path.islink(path):
        return True
    try:
        attrs = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & 0x400)


def session_files(codex_home: Path, onerror=None):
    for sub in ("sessions", "archived_sessions"):
        base = codex_home / sub
        if not base.is_dir() or is_link(base):
            continue
        for root, dirs, files in os.walk(base, followlinks=False, onerror=onerror):
            dirs[:] = sorted(d for d in dirs if not is_link(os.path.join(root, d)))
            for file in sorted(files):
                full = os.path.join(root, file)
                if file.endswith(".jsonl") and not is_link(full):
                    yield full


def iter_lines(handle, size: int):
    """Yield bytes lines (None for oversized) within the initially captured size."""
    remaining = size
    while remaining > 0:
        line = handle.readline(min(MAX_LINE_BYTES + 1, remaining))
        if not line:
            return
        remaining -= len(line)
        if len(line) > MAX_LINE_BYTES:
            while remaining > 0 and not line.endswith(b"\n"):
                line = handle.readline(min(1 << 20, remaining))
                remaining -= len(line)
                if not line:
                    break
            yield None
        else:
            yield line


class Tally:
    def __init__(self, as_of: datetime):
        self.end = as_of
        self.cut30 = as_of - timedelta(days=30)
        self.cut7 = as_of - timedelta(days=7)
        self.coverage = dict.fromkeys(COVERAGE_KEYS, 0)
        self.seen: set = set()
        self.skills: dict = {}

    def in_window(self, when: datetime) -> bool:
        return self.cut30 <= when < self.end

    def add_read(self, skill: str, when: datetime, conversation: str):
        slot = self.skills.setdefault(skill, {d: [0, set(), None] for d in WINDOWS})
        for days, cutoff in ((30, self.cut30), (7, self.cut7)):
            if when >= cutoff:
                stat = slot[days]
                stat[0] += 1
                stat[1].add(conversation)
                stat[2] = when if stat[2] is None or when > stat[2] else stat[2]

    def report(self, generated_at: datetime) -> dict:
        skills = {}
        for name in sorted(self.skills):
            skills[name] = {
                str(days): {"reads": reads, "conversations": len(convs),
                            "last_read": iso(last) if last else None}
                for days, (reads, convs, last) in self.skills[name].items()
            }
        return {
            "schema_version": SCHEMA_VERSION, "source": SOURCE, "metric": METRIC,
            "scope": SCOPE, "generated_at": iso(generated_at), "window_end": iso(self.end),
            "coverage": dict(self.coverage), "skills": skills,
        }


def _scan_record(record: dict, state: dict, tally: Tally):
    kind, payload = record.get("type"), record.get("payload")
    if not isinstance(payload, dict):
        return
    if kind == "session_meta":
        if state["created"] is None:
            raw = payload.get("timestamp", record.get("timestamp"))
            state["created"] = parse_time(raw, assume_utc=True)
            if isinstance(payload.get("id"), str):
                state["id"] = payload["id"]
        return
    if kind != "response_item":
        return
    ptype, call_id = payload.get("type"), payload.get("call_id")
    is_call = ptype in {"function_call", "custom_tool_call"}
    if not (is_call or ptype in {"function_call_output", "custom_tool_call_output"}) or not isinstance(call_id, str):
        return
    when = parse_time(record.get("timestamp"), assume_utc=True)
    if state["created"] is not None and when < state["created"]:
        tally.coverage["inherited_events"] += 1
        return
    if is_call:
        arguments = payload.get("arguments", payload.get("input"))
        if ptype == "function_call" and isinstance(arguments, str):
            arguments = loads_strict(arguments)
        names, needs_code = skills_from_call(payload.get("name"), arguments)
        if names:
            state["calls"][call_id] = (names, needs_code, when)
        return
    call = state["calls"].pop(call_id, None)
    if call is None or not tally.in_window(when):
        return
    names, needs_code, _ = call
    confirmed = confirmed_names(payload.get("output"), names, needs_code)
    tally.coverage["unconfirmed_reads"] += len(names) - len(confirmed)
    for skill in sorted(confirmed):
        key = (call_id, skill, when)
        if key in tally.seen:
            tally.coverage["duplicate_events"] += 1
        else:
            tally.seen.add(key)
            tally.add_read(skill, when, state["id"])


def _scan_file(path: str, tally: Tally):
    state = {"created": None, "id": "file:" + path, "calls": {}}
    with open(path, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        for line in iter_lines(handle, size):
            if line is None:
                tally.coverage["oversized_lines"] += 1
                continue
            if not line.strip():
                continue
            try:
                record = loads_strict(line.decode("utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("record is not an object")
                _scan_record(record, state, tally)
            except (ValueError, TypeError, AttributeError, RecursionError, OverflowError):
                tally.coverage["malformed_lines"] += 1
    for names, _, when in state["calls"].values():
        if tally.in_window(when):
            tally.coverage["unconfirmed_reads"] += len(names)


def collect(codex_home: Path, as_of: datetime | None = None) -> dict:
    codex_home = Path(codex_home)
    if not codex_home.is_dir():
        raise FileNotFoundError("Codex home directory does not exist")
    if not any((codex_home / name).is_dir() and not is_link(codex_home / name)
               for name in ('sessions', 'archived_sessions')):
        raise FileNotFoundError("No local Codex session directories were found")
    tally = Tally(_aware_utc(as_of))
    def directory_error(_):
        tally.coverage["unreadable_files"] += 1
    for path in session_files(codex_home, directory_error):
        try:
            _scan_file(path, tally)
        except OSError:
            tally.coverage["unreadable_files"] += 1
        else:
            tally.coverage["files_scanned"] += 1
    return tally.report(datetime.now(timezone.utc))


def write_report(report: dict, output: Path):
    """Atomically write a report, refusing to replace anything but a report."""
    output = Path(output)
    validate_report(report)
    if is_link(output):
        raise ValueError("refusing a linked report destination")
    previous = None
    if output.exists():
        with output.open('rb') as stream:
            previous = stream.read(2 * 1024 * 1024 + 1)
        if len(previous) > 2 * 1024 * 1024:
            raise ValueError("existing destination is not a bounded usage report")
        validate_report(loads_strict(previous.decode('utf-8-sig')))
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".usage-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
        if previous is None:
            os.link(temp, output)  # Fails if another writer created the destination.
        else:
            if is_link(output) or output.read_bytes() != previous:
                raise ValueError("report changed during collection; retry")
            os.replace(temp, output)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("collect", help="write a fresh aggregate usage snapshot")
    cmd.add_argument("--codex-home", required=True, type=Path)
    cmd.add_argument("--output", required=True, type=Path)
    cmd.add_argument("--as-of", help="timezone-aware ISO time (default: now)")
    args = parser.parse_args(argv)
    try:
        as_of = parse_time(args.as_of) if args.as_of else None
        report = collect(args.codex_home, as_of)
        write_report(report, args.output)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.output} ({len(report['skills'])} skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
