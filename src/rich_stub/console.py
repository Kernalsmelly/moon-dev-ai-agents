class Console:
    def __init__(self, *args, **kwargs):
        pass

    def print(self, *args, **kwargs):
        # Simple stand-in for rich.Console.print
        # Join positional args into a single string for readability
        try:
            out = ' '.join(str(a) for a in args)
        except Exception:
            out = str(args)
        print(out)
