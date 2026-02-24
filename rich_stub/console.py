class Console:
    def __init__(self, *args, **kwargs):
        pass

    def print(self, *args, **kwargs):
        try:
            out = ' '.join(str(a) for a in args)
        except Exception:
            out = str(args)
        print(out)
