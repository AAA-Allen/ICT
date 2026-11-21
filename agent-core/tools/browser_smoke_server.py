"""Synthetic browser fixture, not a production app or model-quality evaluation.

Run from the checkout: python -m tools.browser_smoke_server --port 8003
All fixture data is public/synthetic. The fixed token is test-only, not a secret.
"""

import argparse
from pathlib import Path
import time

from agent_core.http_api import RuntimeAPIHandler
from agent_core.nodes import build_agent_from_config
from agent_core.server import register_agents_from_config
from examples.reference_agent import OfflineReviewModel, create_reference_server

SMOKE_TOKEN = 'browser-smoke-only'
STREAM_TEXT = '离线脚本流式演示：这不是外部模型回复。'


class OfflineAssetsHandler(RuntimeAPIHandler):
    """Block third-party resources in this fixture without changing OS networking."""

    def end_headers(self):
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "font-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'")
        super().end_headers()


class ScriptedStreamModel:
    model = 'scripted-browser-smoke'

    def __init__(self, delay=0.15):
        self.delay = delay

    def complete_stream(self, messages, on_delta=None):
        # Deliberately slow enough to reload while a stream is active.
        for character in STREAM_TEXT:
            if on_delta is not None:
                on_delta(character)
            time.sleep(self.delay)
        return {'content': STREAM_TEXT, 'usage': {}}

    def complete(self, messages):
        return {'content': STREAM_TEXT, 'usage': {}}


def create_smoke_server(workspace, *, port=8003, stream_delay=0.15):
    server = create_reference_server(workspace, model=OfflineReviewModel(), port=port,
                                     approval_token=SMOKE_TOKEN)
    try:
        server.RequestHandlerClass = OfflineAssetsHandler
        register_agents_from_config(Path(__file__).resolve().parents[1] / 'examples/recovery_agent.json',
                                    server.registry, runtime=server.runtime,
                                    audit=server.harness_audit, pending_invocations=server.pending_invocations,
                                    approval_decisions=server.approval_decisions)
        config = {'name': 'scripted-stream', 'description': 'SCRIPTED ONLY: browser SSE smoke',
                  'entry': 'mark', 'nodes': [
                    {'id': 'mark', 'type': 'template', 'params': {'updates': {
                      'model_kind': 'offline-scripted', 'model_quality': 'NOT_EVALUATED_SCRIPTED'}}},
                    {'id': 'answer', 'type': 'llm', 'params': {'prompt': '{task}', 'into': 'summary'}}],
                  'edges': [{'source': 'mark', 'target': 'answer'}]}
        server.registry.register(build_agent_from_config(config, runtime=server.runtime,
            model=ScriptedStreamModel(stream_delay), audit=server.harness_audit))
    except Exception:
        server.server_close()
        raise
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('outputs/browser-smoke'))
    parser.add_argument('--port', type=int, default=8003)
    args = parser.parse_args(argv)
    server = create_smoke_server(args.workspace, port=args.port)
    print(f'SCRIPTED ONLY browser fixture http://127.0.0.1:{server.server_address[1]}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
