from apis.session_cookie import decode_session_cookie, encode_session_cookie


def test_roundtrip_desk_token_with_at():
    raw = "Portafolio111@"
    encoded = encode_session_cookie(raw)
    assert encoded.startswith("m1.")
    assert "@" not in encoded
    assert '"' not in encoded
    assert decode_session_cookie(encoded) == raw
    assert decode_session_cookie(f'"{encoded}"') == raw


def test_legacy_raw_and_quoted_still_decode():
    assert decode_session_cookie("desk-secret") == "desk-secret"
    assert decode_session_cookie('"Portafolio111@"') == "Portafolio111@"
    assert decode_session_cookie(None) is None
    assert decode_session_cookie("") is None
