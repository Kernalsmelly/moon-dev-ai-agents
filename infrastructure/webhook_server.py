from __future__ import annotations

import os
import asyncio
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

from rich.console import Console

from src.adapters.agents.whale_signal_handler import prefetch_quotes
from src.brain import MarketBrain

console = Console()

# Module-level tracked tasks for webhook-spawned work (tests can inspect/cleanup)
_bg_tasks: set[asyncio.Task] = set()

def _create_tracked_task(coro_or_task):
    try:
        if isinstance(coro_or_task, asyncio.Task):
            task = coro_or_task
        else:
            task = asyncio.create_task(coro_or_task)
    except Exception:
        task = asyncio.create_task(coro_or_task)
    try:
        _bg_tasks.add(task)
    except Exception:
        pass

    def _on_done(t: asyncio.Task):
        try:
            _bg_tasks.discard(t)
        except Exception:
            pass

    try:
        task.add_done_callback(_on_done)
    except Exception:
        pass
    return task

# Ensure the local AsyncClient shim delegates through MarketBrain if possible
try:
    MarketBrain.register_global_shield()
except Exception:
    pass

app = FastAPI(title="MoonDev Webhook Ear")

# Shared secret header required for incoming webhooks (set in .env for local dev)
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')
MARKET_API_URL = os.getenv('MARKET_DATA_API_URL', '')


@app.post('/webhook/helius')
async def helius_webhook(request: Request, x_webhook_secret: str | None = Header(None)) -> JSONResponse:
    # Basic secret validation
    if WEBHOOK_SECRET:
        if not x_webhook_secret or x_webhook_secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail='invalid webhook secret')

    payload = await request.json()
    # Fire-and-forget handler to minimize HTTP latency
    try:
        # instantiate a MarketBrain bound to the current RPC and hand off the
        # payload via MarketBrain.process_signal so the brain's JitoManager and
        # telemetry are used. We spawn as a task to keep the HTTP response fast.
        mb = MarketBrain(rpc=os.getenv('RPC_URL'))
        # Prefer to use the brain's tracked task helper so spawned work is
        # registered and can be canceled by tests. Fall back to create_task.
        try:
            if hasattr(mb, '_create_tracked_task'):
                mb._create_tracked_task(mb.process_signal(payload))
            elif hasattr(mb, '_create_task'):
                mb._create_task(mb.process_signal(payload))
            else:
                _create_tracked_task(mb.process_signal(payload))
        except Exception:
            try:
                _create_tracked_task(mb.process_signal(payload))
            except Exception:
                pass
    except Exception as e:
        console.print(f'Webhook handler spawn error: {e}', style='red')
        raise HTTPException(status_code=500, detail='handler error')

    return JSONResponse({'ok': True})


@app.post('/webhook/prefetch')
async def webhook_prefetch(request: Request, x_webhook_secret: str | None = Header(None)) -> JSONResponse:
    if WEBHOOK_SECRET:
        if not x_webhook_secret or x_webhook_secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail='invalid webhook secret')
    body = await request.json()
    mints = body.get('mints') or []
    if not isinstance(mints, list) or not mints:
        raise HTTPException(status_code=400, detail='mints required')
    # spawn prefetch task
    try:
        # best-effort spawn; prefer handler-level tracked helper when available
        try:
            import src.adapters.agents.whale_signal_handler as wsh
            if hasattr(wsh, '_create_tracked_task'):
                wsh._create_tracked_task(prefetch_quotes(mints))
            else:
                asyncio.create_task(prefetch_quotes(mints))
        except Exception:
            asyncio.create_task(prefetch_quotes(mints))
    except Exception as e:
        console.print(f'Prefetch spawn error: {e}', style='red')
        raise HTTPException(status_code=500, detail='prefetch failed')
    return JSONResponse({'ok': True, 'mints': mints})


def run(host: str = '0.0.0.0', port: int = 8000):
    """Helper to run the webhook locally with uvicorn."""
    console.print(f'Listening for Helius webhooks on http://{host}:{port}/webhook/helius', style='green')
    uvicorn.run('infrastructure.webhook_server:app', host=host, port=port, log_level='info')


if __name__ == '__main__':
    run()
