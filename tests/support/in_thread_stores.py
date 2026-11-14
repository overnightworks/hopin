from webauth.rate_limit import SingleProcessRateLimitBackend

from hopin.stores.shared import SharedStores


def in_thread_stores(*, answers: bool = True) -> SharedStores:
    """The shared stores of a test that runs one app in its own thread: the library's single-process limiter."""
    return SharedStores(rate_limits=SingleProcessRateLimitBackend(), answers=lambda: answers)
