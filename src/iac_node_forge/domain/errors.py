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


class MissingCredentialError(DomainError):
    """Credentials hold no value of the requested name, or no value at all."""


class NotSingleCredentialError(DomainError):
    """A single value was requested from credentials that hold several."""


class OperationStateError(DomainError):
    """An operation's state and end time disagree, or an ended one was changed."""


class OperationTextTooLongError(DomainError):
    """The detail, consequence or provider reference of an operation exceeds its
    bound."""


class ManagedServerEndError(DomainError):
    """A managed-server record has only one of its end time and ending operation."""


class BootstrapHintTooLongError(DomainError):
    """The bootstrap hint of a desired configuration exceeds its bound."""


class DuplicateValueNameError(DomainError):
    """Two provider values of one description carry the same name."""


class InvalidValueRuleError(DomainError):
    """A value rule contradicts itself, such as a minimum above its maximum."""
