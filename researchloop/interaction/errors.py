class Problem(Exception):
    def __init__(self, code, status=422, *, revision=None, field_errors=None):
        self.code = code
        self.status = status
        self.revision = revision
        self.field_errors = field_errors
        super().__init__(code)
