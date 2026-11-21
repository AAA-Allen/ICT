"""Deterministic, sequential HTTP baseline; no external model or package service.

python -m examples.resource_baseline --rounds 200
"""

import argparse
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
import math
from pathlib import Path
import platform
import sys
import threading
from time import monotonic, perf_counter, sleep
import tracemalloc
from uuid import uuid4

from agent_core import AgentBuilder, NodeCallPolicy, NodeResult, Tool, ToolRegistry
from agent_core.server import create_server
import agent_core.streaming as streaming
from .workflow_acceptance import source_revision


# Fixed before executing the workload. Timing and RSS are observations, not gates.
SPEC = {"version": "m12-baseline-v1", "chunks_per_run": 128, "sample_every": 25,
        "cache": {"max_history": 32, "max_runs": 8, "max_event_bytes": 128,
                  "max_run_bytes": 1024, "max_total_bytes": 8192, "retention_seconds": 1},
        "gates": {"max_extra_threads_during_run": 8, "max_files_per_run": 5,
                  "fixed_file_allowance": 4, "max_bytes_per_run": 100_000,
                  "fixed_byte_allowance": 65_536}}


def resident_bytes():
    """Current working set on Windows / resident pages on Linux; unknown otherwise."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in
                    ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.ws
        elif sys.platform.startswith("linux"):
            import os
            return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        pass
    return None


class BaselineModel:
    def __init__(self):
        self.calls = 0

    def complete_stream(self, messages, on_delta):
        self.calls += 1
        parts = [f"token-{index:03d}-你好" for index in range(SPEC['chunks_per_run'])]
        for part in parts:
            on_delta(part)
        return {"content": ''.join(parts), "model": "synthetic-baseline"}


def storage_size(state):
    files = [path for path in state.rglob('*') if path.is_file()]
    return {"files": len(files), "bytes": sum(path.stat().st_size for path in files)}


def run_baseline(output: Path, *, rounds=200):
    if isinstance(rounds, bool) or not isinstance(rounds, int) or not 1 <= rounds <= 10_000:
        raise ValueError('rounds must be 1..10000')
    directory = output.resolve() / f'baseline-{uuid4().hex}'
    directory.mkdir(parents=True)
    report = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
              "source": source_revision(), "python": platform.python_version(), "platform": platform.platform(),
              "spec": SPEC, "rounds": rounds, "profile": "BASELINE" if rounds >= 200 else "SMOKE",
              "overall": "FAIL", "model": "synthetic-baseline", "external_model_calls": 0,
              "checks": {}, "samples": [], "limits": "sequential local HTTP; not a throughput or process RSS guarantee"}
    (directory / 'spec.json').write_text(json.dumps(SPEC, indent=2), encoding='utf-8')
    previous_bus = streaming._BUS
    server = worker = None
    serving = False
    tracing_owned = not tracemalloc.is_tracing()
    if tracing_owned:
        tracemalloc.start()
    bus = streaming.TokenBus(**SPEC['cache'])
    before_threads = threading.active_count()
    started = perf_counter()
    timings = []
    tool_calls = 0
    completed = 0
    attempts = 0
    model = BaselineModel()
    max_cache = {"runs": 0, "events": 0, "payload_bytes": 0}
    max_threads = before_threads
    state = directory / 'state'

    def check(name, value):
        report['checks'][name] = report['checks'].get(name, True) and bool(value)

    def sample(label):
        current, peak = tracemalloc.get_traced_memory()
        report['samples'].append({"label": label, "cache": bus.stats(), "threads": threading.active_count(),
                                  "traced_current_bytes": current, "traced_peak_bytes": peak,
                                  "resident_bytes": resident_bytes(), "storage": storage_size(state)})

    def request(path, payload=None, headers=None):
        connection = HTTPConnection('127.0.0.1', server.server_address[1], timeout=15)
        try:
            body = None if payload is None else json.dumps(payload)
            connection.request('GET' if body is None else 'POST', path, body,
                               headers or ({'Content-Type': 'application/json'} if body else {}))
            response = connection.getresponse()
            data = response.read()
            if response.status != 200:
                raise RuntimeError('baseline HTTP request failed')
            return data
        finally:
            connection.close()

    def counted_tool(args):
        nonlocal tool_calls
        tool_calls += 1
        return {'chars': len(args['text'])}

    try:
        streaming._BUS = bus  # Isolated CLI process; tests restore this binding in finally.
        server = create_server(port=0, store_dir=state / 'runs', model=model)
        tools = ToolRegistry()
        tools.register(Tool('stats', counted_tool, read_only=True))

        def ask(ctx):
            reply = ctx.calls.model([{'role': 'user', 'content': 'fixed baseline input'}])
            return NodeResult.success({'text': reply['content']})

        def stats(ctx):
            return NodeResult.success({'stats': dict(ctx.calls.tool('stats', {'text': ctx.state['text']}))})

        agent = (AgentBuilder('resource-baseline', model=model, tools=tools, audit=server.harness_audit)
                 .add_node('ask', ask, calls=NodeCallPolicy())
                 .add_node('stats', stats, calls=NodeCallPolicy(allowed_tools=frozenset({'stats'})))
                 .set_entry('ask').add_edge('ask', 'stats').build(runtime=server.runtime))
        server.registry.register(agent)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        serving = True
        sample('before')
        for index in range(rounds):
            start = perf_counter()
            attempts += 1
            payload = json.loads(request('/api/runs', {'agent': 'resource-baseline', 'input': {},
                                                       'run_id': f'baseline-{index:05d}'}))
            timings.append((perf_counter() - start) * 1000)
            completed += payload['run']['status'] == 'COMPLETED'
            cache = bus.stats()
            for name in max_cache:
                max_cache[name] = max(max_cache[name], cache[name])
            max_threads = max(max_threads, threading.active_count())
            check('cache_run_limit', cache['runs'] <= SPEC['cache']['max_runs'])
            check('cache_event_limit', cache['events'] <= SPEC['cache']['max_runs'] * SPEC['cache']['max_history'])
            check('cache_byte_limit', cache['payload_bytes'] <= SPEC['cache']['max_total_bytes'])
            check('runtime_releases_active_stream', cache['active_runs'] == 0)
            if (index + 1) % SPEC['sample_every'] == 0 or index + 1 == rounds:
                sample(index + 1)

        # Late subscribers must see an explicit gap, followed by the retained window.
        path = f'/api/runs/baseline-{rounds - 1:05d}/events/stream'
        frames = []
        for block in request(path).decode().split('\n\n'):
            fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line)
            if 'data' in fields:
                fields['data'] = json.loads(fields['data'])
                frames.append(fields)
        tokens = [frame for frame in frames if frame['event'] == 'token']
        check('late_subscription_gap', any(f['event'] == 'gap' for f in frames))
        check('retained_window', len(tokens) == SPEC['cache']['max_history'])
        if tokens:
            replay = request(path, headers={'Last-Event-ID': tokens[-1]['id']}).decode()
            check('reconnect_no_duplicate_tokens', 'event: token\n' not in replay and 'event: done\n' in replay)
        else:
            check('reconnect_no_duplicate_tokens', False)
        check('completed_runs', completed == rounds)
        check('actual_calls', model.calls == rounds and tool_calls == rounds)
        check('thread_bound', max_threads <= before_threads + SPEC['gates']['max_extra_threads_during_run'])
        size = storage_size(state)
        check('file_count_bound', size['files'] <= rounds * SPEC['gates']['max_files_per_run'] + SPEC['gates']['fixed_file_allowance'])
        check('file_bytes_bound', size['bytes'] <= rounds * SPEC['gates']['max_bytes_per_run'] + SPEC['gates']['fixed_byte_allowance'])
        deadline = monotonic() + SPEC['cache']['retention_seconds'] + 2
        while bus.stats()['runs'] and monotonic() < deadline:
            sleep(0.02)
        check('ttl_reclaims_finished', bus.stats()['runs'] == 0 and bus.stats()['payload_bytes'] == 0)
        sample('after_expiry')
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        check('execution_completed', False)
    finally:
        if server is not None:
            if serving:
                server.shutdown()
                worker.join(timeout=5)
            server.server_close()
        deadline = monotonic() + 2
        while threading.active_count() > before_threads and monotonic() < deadline:
            sleep(0.02)
        check('threads_return_to_initial', threading.active_count() <= before_threads)
        sample('after_shutdown')
        report['observations'] = {"post_attempts": attempts, "completed_runs": completed,
                                  "model_calls": model.calls, "tool_calls": tool_calls,
                                  "max_cache": max_cache, "max_threads": max_threads, "initial_threads": before_threads}
        streaming._BUS = previous_bus
        if tracing_owned:
            tracemalloc.stop()
    ordered = sorted(timings)
    report['timing'] = {"elapsed_seconds": perf_counter() - started, "request_samples": len(timings),
                        "p50_ms": ordered[math.ceil(len(ordered) * .50) - 1] if ordered else None,
                        "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None,
                        "max_ms": max(ordered) if ordered else None,
                        "scope": "POST run latency includes execution and persistence; excludes sampling and SSE probes"}
    report['memory_scope'] = 'tracemalloc enabled; resident_bytes is current OS working set/RSS, null when unavailable'
    report['overall'] = 'PASS' if report['checks'] and all(report['checks'].values()) else 'FAIL'
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return directory, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=200)
    parser.add_argument('--output', type=Path, default=Path('outputs/resource-baseline'))
    args = parser.parse_args(argv)
    if not 1 <= args.rounds <= 10_000:
        parser.error('rounds must be 1..10000')
    directory, report = run_baseline(args.output, rounds=args.rounds)
    print(json.dumps({'result': report['overall'], 'profile': report['profile'], 'report': str(directory / 'report.json')}))
    return 0 if report['overall'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
