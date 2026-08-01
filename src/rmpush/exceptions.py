class RmpushError(Exception):
    """Base class for all rmpush errors."""


# "No device token found. Call Auth.register(one_time_code) first, "
# "using a code from https://my.remarkable.com/device/browser/connect"
class NotRegisteredError(RmpushError):
    """Raised when an operation needs a device token but none is stored yet."""


class RegistrationError(RmpushError):
    """Raised when exchanging a one-time pairing code for a device token fails."""


class TokenRefreshError(RmpushError):
    """Raised when exchanging a device token for a user token fails."""


class SyncProtocolError(RmpushError):
    """Raised when the cloud responds in a way this client doesn't understand.

    reMarkable's sync protocol is unofficial and undocumented, and has changed
    shape before. If you hit this, the cloud API has likely drifted from what
    this library expects.
    """
