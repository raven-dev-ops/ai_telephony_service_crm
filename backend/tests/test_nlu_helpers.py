from app.services.nlu import parse_address, parse_name


def test_parse_name_handles_leading_phrases():
    assert parse_name("my name is Jane Doe") == "Jane Doe"
    assert parse_name("This is John") == "John"


def test_parse_name_short_phrase_fallback():
    # Reasonably short phrase with a space is treated as a name.
    assert parse_name("Jane Caller") == "Jane Caller"


def test_parse_address_detects_street_like_input():
    addr = "123 Main St, Merriam KS"
    assert parse_address(addr) == addr


def test_parse_address_rejects_non_address_text():
    assert parse_address("kitchen faucet is leaking") is None


def test_parse_address_accepts_zip_only_style_input():
    addr = "Near 12345"
    assert parse_address(addr) == "Near 12345"


def test_parse_address_normalizes_whitespace_and_commas():
    addr = "  456   Oak Avenue , Overland Park KS 66210 "
    assert parse_address(addr) == "456 Oak Avenue, Overland Park KS 66210"
