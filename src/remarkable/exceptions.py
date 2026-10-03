class RemarkableError(Exception):
    """Base class for all remarkable errors."""


class NotRegisteredError(RemarkableError):
    """Raised when an operation needs a device token but none is stored yet."""


class RegistrationError(RemarkableError):
    """Raised when exchanging a one-time pairing code for a device token fails."""


class TokenRefreshError(RemarkableError):
    """Raised when exchanging a device token for a user token fails."""


class SyncProtocolError(RemarkableError):
    """Raised when the cloud responds in a way this client doesn't understand.

    reMarkable's sync protocol is unofficial and undocumented, and has changed
    shape before. If you hit this, the cloud API has likely drifted from what
    this library expects.
    """


class GenerationConflictError(SyncProtocolError):
    """Raised when another client changed the cloud state while we were editing it."""


class DocumentNotFoundError(RemarkableError):
    """Raised when a requested path doesn't resolve to a usable document or folder."""


class UnsupportedFormatError(RemarkableError):
    """Raised when a requested download format doesn't apply to the document's kind."""
