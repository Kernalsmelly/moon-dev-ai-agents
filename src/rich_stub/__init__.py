try:
	# delegate to the real 'rich' package installed in the environment
	import importlib
	_real = importlib.import_module('rich')
	for _name in dir(_real):
		if not _name.startswith('_'):
			try:
				globals()[_name] = getattr(_real, _name)
			except Exception:
				pass
except Exception:
	# If the real package isn't available yet, leave small shims to avoid fatal import errors
	class Console:
		def print(self, *args, **kwargs):
			try:
				print(' '.join(str(a) for a in args))
			except Exception:
				print(args)

	class Panel:
		def __init__(self, text, *args, **kwargs):
			self.text = text
		def __str__(self):
			return str(self.text)
