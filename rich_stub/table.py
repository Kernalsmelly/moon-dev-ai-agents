class Table:
    def __init__(self, title=None):
        self.title = title
        self._columns = []  # list of (name, justify)
        self._rows = []

    def add_column(self, name, justify=None):
        self._columns.append((name, justify or "left"))

    def add_row(self, *cells):
        # simple append; allow fewer cells than columns
        self._rows.append(list(cells))

    def __str__(self):
        # simple textual rendering for the lightweight shim used in tests
        cols = [c[0] for c in self._columns]
        # compute column widths
        widths = [len(c) for c in cols]
        for row in self._rows:
            for i, cell in enumerate(row):
                s = str(cell)
                if i >= len(widths):
                    widths.append(len(s))
                else:
                    widths[i] = max(widths[i], len(s))

        # header
        parts = []
        if self.title:
            parts.append(self.title)
        if cols:
            header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
            parts.append(header)
            parts.append("-" * len(header))

        # rows
        for row in self._rows:
            line_parts = []
            for i, cell in enumerate(row):
                line_parts.append(str(cell).ljust(widths[i]))
            parts.append(" | ".join(line_parts))

        return "\n".join(parts)
