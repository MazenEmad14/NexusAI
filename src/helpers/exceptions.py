class GoogleAuthRequiredException(Exception):
    """Exception raised when a Google service requires user authentication via OAuth flow."""
    pass


class LLMOutputError(Exception):
    """The model replied, but not with usable JSON. `raw` is its last reply, for callers that can fall back."""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw
