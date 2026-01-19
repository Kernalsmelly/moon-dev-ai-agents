#!/usr/bin/env python3
import asyncio
import os
import sys

# Ensure the venv site-packages comes before project root to prefer installed packages
venv_site = os.path.join(os.getcwd(), '.venv', 'lib')
found = None
if os.path.isdir(venv_site):
    # find the pythonX.Y directory under .venv/lib
    for name in os.listdir(venv_site):
        if name.startswith('python'):
            candidate = os.path.join(venv_site, name, 'site-packages')
            if os.path.isdir(candidate):
                found = candidate
                break
if found:
    sys.path.insert(0, found)

sys.path.insert(0, os.getcwd())

from tests import integration_exit_flow as itf


async def _run_all():
    await itf.test_send_not_called_when_unprofitable(None)
    await itf.test_send_called_when_profitable(None)


if __name__ == '__main__':
    asyncio.run(_run_all())
