class SagentError(Exception):
    """An error whose message is safe and useful to show to the user."""


class ConfigError(SagentError):
    pass


class IntegrationError(SagentError):
    pass
