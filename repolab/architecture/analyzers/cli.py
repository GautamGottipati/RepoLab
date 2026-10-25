"""Command-line components: the parser, its commands, and interactive shells.

Found from the parsers every ecosystem uses -- argparse, click, typer, picocli,
cobra -- and from the interactive-shell signals detection already recorded.
"""

from __future__ import annotations

import re

from repolab.architecture.analyzers.base import AnalysisContext, slug, stem, title
from repolab.architecture.models import ArchitectureEdge, ArchitectureNode, EdgeType, NodeCategory
from repolab.architecture.source import find_all, is_peripheral, is_test_path

_PARSER = re.compile(
    r"argparse\.ArgumentParser\s*\(|@click\.(?:group|command)\s*\(|typer\.Typer\s*\(|"
    r"@Command\s*\(|&cobra\.Command\s*\{|\bcommander\b.*\bprogram\b|yargs\s*\("
)
_COMMANDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"add_parser\(\s*['\"](?P<name>[\w-]+)['\"]"),
    re.compile(
        r"@\w+\.command\((?:\s*(?:name\s*=\s*)?['\"](?P<name>[\w-]+)['\"])?[^)]*\)\s*\n(?:\s*@[^\n]*\n)*\s*def\s+(?P<fn>\w+)"
    ),
    re.compile(r"@Command\(\s*name\s*=\s*\"(?P<name>[\w-]+)\""),
    re.compile(r"Use:\s*\"(?P<name>[\w-]+)"),
    re.compile(r"\.command\(\s*['\"](?P<name>[\w-]+)"),
)

MAX_COMMANDS = 8


class CliAnalyzer:
    name = "cli"

    def analyze(self, context: AnalysisContext) -> None:
        for path, text in context.index.source_files():
            parser = next(find_all(_PARSER, text), None)
            if parser is None:
                continue
            line, _ = parser
            commands: list[tuple[int, str]] = []
            for pattern in _COMMANDS:
                for cmd_line, match in find_all(pattern, text):
                    name = match.groupdict().get("name") or match.groupdict().get("fn") or ""
                    name = name.replace("_", "-")
                    if name and name not in {c for _, c in commands}:
                        commands.append((cmd_line, name))
            commands.sort()

            cli = context.graph.add_node(
                ArchitectureNode(
                    id=f"cli:{slug(path)}",
                    label=f"{title(stem(path))} CLI"
                    if stem(path) not in {"main", "cli", "__main__"}
                    else "Command-line interface",
                    category=NodeCategory.CLI,
                    source_references=[context.index.ref(path, line)],
                    properties={"commands": [c for _, c in commands[:20]]},
                    confidence=0.85,
                    detected_because=f"{path} defines a command-line parser",
                )
            )
            total_lines = text.count("\n") + 1
            bounds = [line for line, _ in commands[1:]] + [total_lines + 1]
            for (cmd_line, name), next_start in zip(commands[:MAX_COMMANDS], bounds, strict=False):
                command = context.graph.add_node(
                    ArchitectureNode(
                        id=f"command:{slug(path)}:{slug(name)}",
                        label=f"`{name}` command",
                        category=NodeCategory.COMMAND,
                        source_references=[context.index.ref(path, cmd_line)],
                        properties={
                            "command": name,
                            "line_start": cmd_line,
                            "line_end": max(cmd_line, next_start - 1),
                        },
                        confidence=0.85,
                        detected_because=f"the parser in {path} registers the '{name}' command",
                    )
                )
                context.graph.add_edge(
                    ArchitectureEdge(
                        source=cli.id,
                        target=command.id,
                        type=EdgeType.ROUTES_TO,
                        evidence=[context.index.ref(path, cmd_line)],
                    )
                )

        shells: set[str] = set()
        for finding in context.outcome.run.of_kind("interactive_shell"):
            references = finding.references[:1]
            if (
                not references
                or is_peripheral(references[0].path)
                or is_test_path(references[0].path)
            ):
                continue
            path = references[0].path
            # The shell signals (readline, cmd.Cmd, an input() loop) are Python's,
            # so only Python files and extensionless launchers can carry them.
            suffix = (
                path.rsplit("/", 1)[-1].rpartition(".")[2] if "." in path.rsplit("/", 1)[-1] else ""
            )
            if suffix not in {"py", ""}:
                continue
            # `bin/cqlsh` and `bin/cqlsh.py` are one shell, launched two ways.
            key = path.rsplit(".", 1)[0] if suffix else path
            shells.add(key)
            # add_node merges, so the second launcher's file joins the first's.
            context.graph.add_node(
                ArchitectureNode(
                    id=f"cli:shell-{slug(key)}",
                    label=f"{title(stem(path))} interactive shell",
                    category=NodeCategory.CLI,
                    subtype="interactive_shell",
                    source_references=list(references),
                    confidence=0.8,
                    detected_because=f"{path} reads commands in a loop, holding a session open",
                )
            )

        self._shells_to_parsers(context)

    @staticmethod
    def _shells_to_parsers(context: AnalysisContext) -> None:
        """A shell whose launcher imports a parser module runs that parser.

        ``bin/cqlsh.py`` does ``from cqlshlib.cqlshmain import main``: the import
        line is the evidence that the shell and the parser are one path.
        """
        shells = [n for n in context.graph.nodes if n.subtype == "interactive_shell"]
        parsers = [
            n for n in context.graph.nodes if n.category is NodeCategory.CLI and n.subtype is None
        ]
        for shell in shells:
            for path in shell.paths:
                text = context.index.text(path) or ""
                for parser in parsers:
                    module = stem(next(iter(parser.paths)))
                    pattern = re.compile(
                        rf"^\s*(?:from\s+[\w.]*\b{re.escape(module)}\b|import\s+[\w.]*\b{re.escape(module)}\b)",
                        re.M,
                    )
                    hit = next(find_all(pattern, text), None)
                    if hit is not None:
                        context.graph.add_edge(
                            ArchitectureEdge(
                                source=shell.id,
                                target=parser.id,
                                type=EdgeType.CALLS,
                                evidence=[context.index.ref(path, hit[0])],
                                description=f"imports {module}",
                            )
                        )
