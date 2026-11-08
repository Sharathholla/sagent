"""sagent command line interface."""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional

from . import __version__, audit, config, doctor, providers, redact, skills as skills_mod, style
from .errors import SagentError


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-y", "--yes", action="store_true", help="skip confirmation for state-changing actions")
    p.add_argument("--dry-run", action="store_true", help="show what write actions would do without executing them")
    return p


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    parser = argparse.ArgumentParser(
        prog="sagent",
        description="CLI agent powered by your own AI login (Claude Code or Antigravity agy) with Bitbucket, "
                    "Jira, Confluence, Slack, AWS and Azure tools.",
        epilog="Examples:\n"
               "  sagent run \"Which Jira tickets are assigned to me?\"   (skills matched from keywords)\n"
               "  sagent run bitbucket \"Review PR 42 in repo payments-api\"   (skill named explicitly)\n"
               "  sagent chat\n\n"
               "`ask` is an alias of `run`. `sagent <skill> <task...>` is shorthand for `sagent run <skill> <task...>`.\n\n"
               + "\n".join(providers.switch_lines()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"sagent {__version__}")
    sub = parser.add_subparsers(dest="action", metavar="<action>", required=True)

    sub.add_parser("init", help="create ~/.sagent and a config template")
    sub.add_parser("doctor", help="verify config, credentials and connectivity")
    sub.add_parser("skills", help="list available skills")
    use = sub.add_parser("use", help="choose the AI backend: claude or agy (Antigravity)")
    use.add_argument("backend", nargs="?", help="claude or agy; omit to pick from a menu")

    run = sub.add_parser("run", aliases=["ask"], parents=[common],
                         help="run a task; skills are matched from your text, or name one first")
    run.add_argument("task", nargs="+", help="[skill] <task...>; an optional leading skill name or .md path is loaded explicitly")
    run.add_argument("-s", "--skill", action="append", default=[], help="also load this skill (repeatable)")

    chat = sub.add_parser("chat", parents=[common], help="interactive session")
    chat.add_argument("-s", "--skill", action="append", default=[], help="load this skill (repeatable)")

    serve = sub.add_parser("mcp-serve", parents=[common])
    sub._choices_actions = [a for a in sub._choices_actions if a.dest != "mcp-serve"]  # hide from help
    serve.set_defaults(hidden=True)
    return parser


def _load() -> config.Config:
    cfg = config.load()
    for warning in config.permission_warnings(cfg.path):
        style.warning(redact.mask(warning))
    return cfg


def cmd_init(args) -> int:
    home = config.ensure_home()
    created = config.create_config_file()
    state = style.green("created") if created else style.dim("already exists, left untouched")
    print(f"{style.bold('sagent home')} : {home}")
    print(f"{style.bold('config')}      : {config.config_path()} ({state})")
    print(f"{style.bold('user skills')} : {config.user_skills_dir()}")
    print(f"{style.bold('audit logs')}  : {config.logs_dir()}")
    print(f"\nNext: edit the config, fill in the services you use, then run {style.cyan('sagent doctor')}.")
    return 0


def cmd_skills(args) -> int:
    catalog = skills_mod.discover()
    if not catalog:
        style.warning("No skills found.")
    for skill in catalog.values():
        print(f"{style.paint(f'{skill.name:<12}', 'bold', 'green')} {style.dim(f'[{skill.source}]')} {skill.description}")
    return 0


def cmd_doctor(args) -> int:
    return doctor.run(_load())


def _session(args, cfg):
    from .agent import Session  # imported lazily: only needed for AI commands

    if not cfg.path.exists():
        style.note("no config yet; run `sagent init` to enable integrations.")
    return Session(cfg, dry_run=args.dry_run, assume_yes=args.yes)


def cmd_run(args) -> int:
    cfg = _load()
    session = _session(args, cfg)
    words = list(args.task)
    refs = list(args.skill)
    # A leading word that names a skill (or an existing .md file) is loaded explicitly.
    if len(words) > 1:
        first = words[0]
        key = re.sub(r"(-skills)?(\.md)?$", "", first.lower())
        if key in session.catalog or first.lower().endswith(".md"):
            refs.append(first)
            words = words[1:]
    task = " ".join(words)
    selected = [skills_mod.resolve(r, session.catalog) for r in refs]
    for skill in skills_mod.select(task, session.catalog):
        if skill not in selected:
            selected.append(skill)
    audit.log("run", skills=[s.name for s in selected], task=task[:500])
    print(redact.mask(session.ask(task, selected)))
    return 0


def cmd_chat(args) -> int:
    cfg = _load()
    session = _session(args, cfg)
    explicit = [skills_mod.resolve(r, session.catalog) for r in args.skill]
    audit.log("chat_start", skills=[s.name for s in explicit])
    print(style.heading("sagent chat") + style.dim(". Commands: /skills, /help, /exit. Write actions ask for confirmation."))
    if args.dry_run:
        print(style.yellow("(dry-run: write actions will not execute)"))
    while True:
        try:
            line = input(style.paint("you> ", "bold", "blue")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            break
        if line == "/help":
            print(style.dim("Ask anything. Skills are auto-selected by topic. /skills lists them; /exit quits."))
            continue
        if line == "/skills":
            cmd_skills(args)
            continue
        selected = explicit + [s for s in skills_mod.select(line, session.catalog) if s not in explicit]
        try:
            print("\n" + style.paint("sagent> ", "bold", "green") + redact.mask(session.ask(line, selected)) + "\n")
        except SagentError as exc:
            style.error(redact.mask(str(exc)) + "\n")
    return 0


def cmd_use(args) -> int:
    if args.backend and providers.normalize(args.backend) is None:
        raise SagentError(f"Unknown AI backend '{args.backend}'. Choose: claude or agy.")
    name = providers.configure(args.backend)
    if name == providers.AGY:
        from . import agent

        exe = agent.find_agy(_load())
        if exe and agent.ensure_agy_mcp(exe):
            style.success("\nRegistered sagent's tools in agy's MCP config (agy mcp list). "
                          "Remove with: agy mcp remove sagent")
    return 0


def cmd_mcp_serve(args) -> int:
    from . import mcp_server

    def flag(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")

    # agy launches this server from its own MCP config, so per-run flags arrive as env vars.
    return mcp_server.serve(config.load(), args.dry_run or flag("SAGENT_MCP_DRY_RUN"),
                            args.yes or flag("SAGENT_MCP_YES"))


HANDLERS = {
    "init": cmd_init,
    "doctor": cmd_doctor,
    "skills": cmd_skills,
    "use": cmd_use,
    "ask": cmd_run,
    "run": cmd_run,
    "chat": cmd_chat,
    "mcp-serve": cmd_mcp_serve,
}


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        build_parser().print_help()
        return 0
    # Shorthand: `sagent bitbucket list repos` == `sagent run bitbucket "list repos"`.
    if len(argv) > 1 and argv[0] not in HANDLERS and not argv[0].startswith("-"):
        if argv[0].lower() in skills_mod.discover():
            argv = ["run"] + argv
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.action](args)
    except SagentError as exc:
        style.error(redact.mask(str(exc)))
        return 1
    except KeyboardInterrupt:
        print("\n" + style.yellow("interrupted", sys.stderr), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
