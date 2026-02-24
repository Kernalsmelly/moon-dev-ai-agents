class Panel:
    def __init__(self, text, *args, **kwargs):
        self.text = text
        self.style = kwargs.get('style')

    def __str__(self):
        return str(self.text)

    def __repr__(self):
        return f"Panel({self.text!r})"
