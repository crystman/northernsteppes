def pytest_addoption(parser):
    parser.addoption(
        "--require-zola",
        action="store_true",
        default=False,
        help="Fail instead of skipping when zola is unavailable (for CI).",
    )
