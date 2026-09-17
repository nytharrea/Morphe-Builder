from core.retry import retry


def test_retry_success():
    counter = 0

    @retry(attempts=3, delay=0.01)
    def flaky():
        nonlocal counter
        counter += 1
        if counter < 2:
            raise ValueError("hata")
        return "tamam"

    assert flaky() == "tamam"
    assert counter == 2
