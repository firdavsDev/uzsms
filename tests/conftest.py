def pytest_sessionfinish(session, exitstatus):
    """Treat "no tests collected" as success.

    Task 1 stands up the harness before any tests exist (``uzsms`` itself
    is only created in Task 2), so an empty collection is the expected,
    healthy state rather than a failure.
    """
    if exitstatus == 5:  # pytest.ExitCode.NO_TESTS_COLLECTED
        session.exitstatus = 0
