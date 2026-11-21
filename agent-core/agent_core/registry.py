"""Agent registry, session memory, and declarative config loading."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from .agent import Agent, InMemoryMemory
from .nodes import build_agent_from_config
from .runtime import Runtime, _validate_run_id
from .tools import Tool, ToolRegistry


class AgentRegistry:
    """Process-local mapping of agent names to configured agents."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._factories: dict[str, Callable[..., Agent]] = {}
        self._lock = threading.RLock()

    def register(self, agent: Agent) -> None:
        if not isinstance(agent, Agent):
            raise TypeError("agent must be an Agent instance")
        with self._lock:
            if agent.spec.name in self._agents or agent.spec.name in self._factories:
                raise ValueError(f"agent already registered: {agent.spec.name}")
            self._agents[agent.spec.name] = agent
            self._factories[agent.spec.name] = lambda memory=None, agent=agent: agent

    def register_factory(self, name: str, factory: Callable[..., Agent]) -> None:
        """Register a buildable agent; the factory receives ``memory=``."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("agent name must be a nonempty string")
        if not callable(factory):
            raise TypeError("agent factory must be callable")
        with self._lock:
            if name in self._agents or name in self._factories:
                raise ValueError(f"agent already registered: {name}")
            master = factory(memory=None)
            if not isinstance(master, Agent):
                raise TypeError("agent factory must return an Agent instance")
            if master.spec.name != name:
                raise ValueError(f"agent factory builds a different name: {master.spec.name!r} != {name!r}")
            self._agents[name] = master
            self._factories[name] = factory

    def unregister(self, name: str) -> bool:
        """Remove an agent registration; returns whether it existed."""
        if not isinstance(name, str):
            return False
        with self._lock:
            existed = name in self._agents or name in self._factories
            self._agents.pop(name, None)
            self._factories.pop(name, None)
            return existed

    def build(self, name: str, *, memory: Any = None) -> Agent | None:
        """Build an agent instance, optionally bound to session memory."""
        if not isinstance(name, str):
            return None
        with self._lock:
            factory = self._factories.get(name)
        if factory is None:
            return self.get(name)
        try:
            return factory(memory=memory)
        except TypeError:
            return factory()

    def get(self, name: str) -> Agent | None:
        if not isinstance(name, str):
            return None
        with self._lock:
            return self._agents.get(name)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._agents)

    def descriptions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": agent.spec.name,
                    "instructions": agent.spec.instructions,
                    "description": agent.spec.description,
                    "metadata": dict(agent.spec.metadata),
                    "graph": agent.describe(),
                }
                for agent in (self._agents[name] for name in sorted(self._agents))
            ]


class SessionManager:
    """Per-session memory instances shared by an agent's runs."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None
        self._memories: dict[str, InMemoryMemory] = {}
        self._run_locks: dict[str, threading.RLock] = {}
        self._lock = threading.RLock()

    def memory_for(self, session_id: str) -> InMemoryMemory:
        _validate_run_id(session_id)
        with self._lock:
            memory = self._memories.get(session_id)
            if memory is None:
                memory = (FileSessionMemory(self._directory, session_id)
                          if self._directory is not None else InMemoryMemory())
                self._memories[session_id] = memory
            return memory

    def run_lock(self, session_id: str) -> threading.RLock:
        """Return the lock serializing runs that share one session."""
        _validate_run_id(session_id)
        with self._lock:
            lock = self._run_locks.get(session_id)
            if lock is None:
                lock = threading.RLock()
                self._run_locks[session_id] = lock
            return lock

    def snapshot(self, session_id: str) -> dict[str, Any] | None:
        _validate_run_id(session_id)
        with self._lock:
            memory = self._memories.get(session_id)
            if memory is None and self._directory is not None and (self._directory / f"{session_id}.json").is_file():
                memory = FileSessionMemory(self._directory, session_id)
                self._memories[session_id] = memory
        return None if memory is None else memory.snapshot()

    def ids(self) -> list[str]:
        with self._lock:
            ids = set(self._memories)
            if self._directory is not None and self._directory.is_dir():
                ids.update(path.stem for path in self._directory.glob("*.json")
                           if not path.name.startswith("."))
            return sorted(ids)

    def clear(self, session_id: str) -> bool:
        _validate_run_id(session_id)
        with self._lock:
            run_lock = self._run_locks.get(session_id)
        if run_lock is not None:
            run_lock.acquire()
        with self._lock:
            try:
                path = self._directory / f"{session_id}.json" if self._directory is not None else None
                existed = session_id in self._memories or (path is not None and path.is_file())
                if path is not None:
                    path.unlink(missing_ok=True)
                self._memories.pop(session_id, None)
                return existed
            finally:
                if run_lock is not None:
                    run_lock.release()


class FileSessionMemory(InMemoryMemory):
    """JSON backed session memory with atomic writes and versioned records."""

    def __init__(self, directory: Path, session_id: str) -> None:
        _validate_run_id(session_id)
        self._path = directory / f"{session_id}.json"
        initial: Mapping[str, Any] = {}
        if self._path.is_file():
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                if (not isinstance(payload, dict) or payload.get("schema_version") != 1
                        or payload.get("session_id") != session_id
                        or not isinstance(payload.get("values"), dict)):
                    raise ValueError("invalid session record")
                initial = payload["values"]
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError("invalid session record") from exc
        super().__init__(initial)

    def write(self, key: str, value: Any) -> None:
        with self._lock:
            previous = self.snapshot()
            super().write(key, value)
            temporary = self._path.parent / f".{self._path.stem}.{uuid4().hex}.tmp"
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps({
                    "schema_version": 1,
                    "session_id": self._path.stem,
                    "values": self.snapshot(),
                }, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                temporary.replace(self._path)
            except Exception:
                self._values = previous
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

def agent_resolver(registry: AgentRegistry) -> Callable[[str, Any], Agent | None]:
    """Build delegate agents on demand, sharing the caller's session memory."""
    return lambda name, memory=None: registry.build(name, memory=memory)


def _config_dependencies(configs: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Map config agent names to their in-config subagent dependencies."""
    dependencies: dict[str, list[str]] = {}
    for config in configs:
        name = config["name"]
        deps: list[str] = []
        for node in config.get("nodes", []):
            if not isinstance(node, Mapping) or node.get("type") != "subagent":
                continue
            params = node.get("params")
            target = params.get("agent") if isinstance(params, Mapping) else None
            if isinstance(target, str) and target.strip():
                deps.append(target)
        dependencies[name] = deps
    return dependencies


def _reject_dependency_cycles(dependencies: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = stack[stack.index(name):] + [name]
            raise ValueError("agent dependency cycle: " + " -> ".join(cycle))
        visiting.add(name)
        stack.append(name)
        for dep in dependencies.get(name, []):
            if dep in dependencies:
                visit(dep)
        stack.pop()
        visiting.discard(name)
        done.add(name)

    for name in dependencies:
        visit(name)


def load_agents_from_config(
    path: str | Path,
    *,
    runtime: Runtime,
    tools: Any = None,
    model: Any = None,
    audit: Any = None,
    pending_invocations: Any = None,
    approval_decisions: Any = None,
    agent_resolver: Callable[[str, Any], Agent | None] | None = None,
) -> list[Agent]:
    """Build agents from a JSON config file (single config or ``agents`` list)."""
    configs = _read_agent_configs(path)
    if tools is None:
        tools = default_tools()
    return [
        build_agent_from_config(
            config, runtime=runtime, tools=tools, model=model,
            agent_resolver=agent_resolver,
            audit=audit,
            pending_invocations=pending_invocations,
            approval_decisions=approval_decisions,
        )
        for config in configs
    ]


def register_agents_from_config(
    path: str | Path,
    registry: AgentRegistry,
    *,
    runtime: Runtime,
    tools: Any = None,
    model: Any = None,
    audit: Any = None,
    pending_invocations: Any = None,
    approval_decisions: Any = None,
) -> list[str]:
    """Register config agents as factories so sessions can bind memory.

    Rejects subagent dependency cycles among the loaded configs at startup.
    """
    configs = _read_agent_configs(path)
    _reject_dependency_cycles(_config_dependencies(configs))
    if tools is None:
        tools = default_tools()
    resolver = agent_resolver(registry)
    for config in configs:
        config = dict(config)
        registry.register_factory(
            config["name"],
            lambda memory=None, config=config: build_agent_from_config(
                config, runtime=runtime, tools=tools, memory=memory,
                model=model, agent_resolver=resolver,
                audit=audit,
                pending_invocations=pending_invocations,
                approval_decisions=approval_decisions,
            ),
        )
    return [config["name"] for config in configs]


def _read_agent_configs(path: str | Path) -> list[Mapping[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and isinstance(payload.get("agents"), list):
        return payload["agents"]
    if isinstance(payload, Mapping):
        return [payload]
    raise ValueError("agent config file must be an object or {'agents': [...]}")



def default_tools() -> Any:
    from .tools import Tool, ToolRegistry

    tools = ToolRegistry()
    tools.register(
        Tool("text_stats", lambda args: {"chars": len(args["task"]), "words": len(args["task"].split())})
    )
    return tools

