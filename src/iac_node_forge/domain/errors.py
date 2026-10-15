"""Errors raised by the domain when an invariant is violated."""


class DomainError(Exception):
    """Base class of every error the domain raises."""


class EmptyValueError(DomainError):
    """A required text value is empty or blank."""


class MissingReasonError(DomainError):
    """A failed access result carries no reason."""


class NaiveTimestampError(DomainError):
    """A timestamp has no timezone."""


class CredentialsNotPersistableError(DomainError):
    """Credentials were asked to be copied, pickled or otherwise persisted."""
