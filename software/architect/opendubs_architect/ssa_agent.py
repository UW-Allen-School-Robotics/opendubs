"""The ARCHITECT agent (ssa's AgenticSession) behind demo_runner's Agent interface.

The session is built the way ssa's CLI builds it for `--robot opendubs`. Three things differ, all so
that what the supervisor approves is exactly what runs:

  * Helpers the model writes (write_primitive) are accepted without a terminal prompt. Approval
    happens once, on the whole bundle, by voice.
  * `bundle` copies the source of every library helper a program calls into the program itself, so
    the frozen program runs against the robot's primitives and nothing else.
  * The session's namespace refuses every motion (demo_runner.generation_namespace). Its tools may
    read the robot's state and ask the camera questions while writing, but never move it.

Needs ssa on the path (its repository root, for `config` and `ssa`) and its API settings in its
own .env, which ssa loads.
"""

from __future__ import annotations

import ast
import os

from opendubs_architect.demo_runner import generation_namespace


class _Console:
    """ssa prints its progress with rich; on the robot that goes to the runner's log."""

    def __init__(self, log=None):
        self._log = log

    def print(self, *args, **kwargs):
        if self._log:
            self._log(" ".join(str(a) for a in args))

    def __getattr__(self, name):                # rule(), status() and friends: ignored
        return lambda *a, **k: None


class SsaAgent:
    def __init__(self, namespace: dict, model: str | None = None, log=None):
        from dotenv import load_dotenv
        from ssa.agent.primitive_registry import PrimitiveRegistry
        from ssa.agent.session import AgenticSession
        from ssa.library.skill_store import SkillStore

        from opendubs_architect.config import OpenDubsConfig
        load_dotenv()
        if model is None:
            from config import ANTHROPIC_MODEL      # ssa's own config.py
            model = ANTHROPIC_MODEL
        cfg = OpenDubsConfig()
        self.registry = PrimitiveRegistry(store=SkillStore(db_path=cfg.skills_dir / "library.db"))
        self.session = AgenticSession(
            model=model, registry=self.registry, namespace=generation_namespace(namespace),
            console=_Console(log), approve_primitive_fn=lambda name, code: (True, code),
            api_spec=cfg.api_spec, robot_label=cfg.display_name, robot_name=cfg.name,
            skills_dir=cfg.skills_dir, disabled_tools=cfg.disabled_agent_tools,
            emit_scorers=False, emit_subtask_docs=False)
        self._generated = False

    def generate(self, instruction: str) -> str | None:
        self._generated = True
        return self.session.generate(instruction=instruction)

    def correct(self, correction: str) -> str | None:
        if not self._generated:
            return self.generate(correction)
        return self.session.correct(correction)

    def bundle(self, program: str) -> str:
        """The program with the source of every library helper it calls (and those they call)
        written above it. Helpers it defines itself are left alone."""
        names, sources = set(self.registry.list_names()), []
        seen: set = set()

        def called(code: str) -> set:
            tree = ast.parse(code)
            own = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in names} - own

        todo = sorted(called(program))
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            src = self.registry.get_source(name)
            if src:
                sources.append(src.rstrip())
                todo.extend(sorted(called(src) - seen))
        if not sources:
            return program
        header = "# Library helpers this program calls, copied in before approval.\n"
        return header + "\n\n".join(reversed(sources)) + "\n\n" + program


def ssa_root_on_path() -> None:
    """Put ssa's checkout on sys.path: $SSA_ROOT, as the README's PYTHONPATH line does."""
    import sys
    root = os.environ.get("SSA_ROOT")
    if root and root not in sys.path:
        sys.path.insert(0, root)
