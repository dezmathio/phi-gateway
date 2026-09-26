import pytest


@pytest.fixture(scope="session")
def scrubber():
    from app.scrub import Scrubber

    engine = Scrubber()
    engine.load()
    return engine
