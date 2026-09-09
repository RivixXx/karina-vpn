class ClientServiceError(Exception):
    """Base error for client business operations."""


class ClientNotFoundError(ClientServiceError):
    pass


class ClientAlreadyExistsError(ClientServiceError):
    pass


class ValidationError(ClientServiceError):
    pass


class SubscriptionIssueError(ClientServiceError):
    pass


class ReconciliationRequiredError(ClientServiceError):
    pass
