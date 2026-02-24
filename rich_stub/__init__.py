"""Lightweight local shim for `rich` used in tests/CI when the real
`rich` package is not being used. This module prefers exposing the
real `rich` if available, otherwise falls back to minimal Console,
Panel, and Table implementations so imports like `from rich.table
import Table` succeed in tests.
"""
try:
	import importlib
	_real = importlib.import_module('rich')
	# If the imported 'rich' resolves to the real external package,
	# copy its public attributes into this shim so callers get full
	# functionality. If it resolves to this local package (same
	# __file__), fall through and expose local submodules instead.
	_real_file = getattr(_real, '__file__', '')
	if _real_file and _real_file != __file__:
		for _name in dir(_real):
			if not _name.startswith('_'):
				try:
					globals()[_name] = getattr(_real, _name)
				except Exception:
					pass
	else:
		# Import local lightweight submodules so `rich.table` is
		# available when tests import it directly.
		from .console import Console
		from .panel import Panel
		from .table import Table
except Exception:
	# fallback lightweight shims
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

	class Table:
		def __init__(self, title=None):
			self.title = title
			self._columns = []
			self._rows = []

		def add_column(self, name, justify=None):
			self._columns.append((name, justify or 'left'))

		def add_row(self, *cells):
			self._rows.append(list(cells))

		def __str__(self):
			cols = [c[0] for c in self._columns]
			widths = [len(c) for c in cols]
			for row in self._rows:
				for i, cell in enumerate(row):
					s = str(cell)
					if i >= len(widths):
						widths.append(len(s))
					else:
						widths[i] = max(widths[i], len(s))

			parts = []
			if self.title:
				parts.append(self.title)
			if cols:
				header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
				parts.append(header)
				parts.append("-" * len(header))

			for row in self._rows:
				line_parts = []
				for i, cell in enumerate(row):
					line_parts.append(str(cell).ljust(widths[i]))
				parts.append(" | ".join(line_parts))

			return "\n".join(parts)
