class XUIError(Exception):
    """Base error for the 3x-ui integration."""


class XUITransportError(XUIError):
    pass


class XUIHTTPError(XUIError):
    pass


class XUIResponseError(XUIError):
    pass


class XUIAuthError(XUIError):
    pass


class XUIOperationError(XUIError):
    pass
