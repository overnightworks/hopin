from typing import TYPE_CHECKING

from webauth.single_process_stores import SingleProcessStores

from hopin.auth.sign_in import LoggingAuditSink

if TYPE_CHECKING:
    from webauth.ports import AuditSink


def single_process_stores(audit: AuditSink | None = None) -> SingleProcessStores:
    """The stores of production's wiring; a test that needs another audit sink passes it."""
    return SingleProcessStores(audit=LoggingAuditSink() if audit is None else audit)
