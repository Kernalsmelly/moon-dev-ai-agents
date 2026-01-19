try:
	import importlib
	_real = importlib.import_module('solana')
	for _name in dir(_real):
		if not _name.startswith('_'):
			try:
				globals()[_name] = getattr(_real, _name)
			except Exception:
				pass
except Exception:
	# minimal fallbacks to avoid immediate import break while setting up venv
	class AsyncClient:
		def __init__(self, *args, **kwargs):
			pass
		async def __aenter__(self):
			return self
		async def __aexit__(self, exc_type, exc, tb):
			return False
		async def send_raw_transaction(self, raw):
			return {'result': 'FAKE_SIG'}
