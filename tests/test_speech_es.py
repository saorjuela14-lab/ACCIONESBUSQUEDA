"""Spanish TTS number rewriting."""

from utils.speech_es import decimal_to_es, percent_to_es, rewrite_for_speech


def test_percent_growth():
    assert percent_to_es(14) == "crecimiento del catorce por ciento"
    assert "crecimiento del catorce por ciento" in rewrite_for_speech("Rendimiento +14%")


def test_percent_decline():
    assert percent_to_es(-0.5) == "decrecimiento del cero punto cinco por ciento"
    out = rewrite_for_speech("Hoy -0.5%")
    assert "decrecimiento del cero punto cinco por ciento" in out


def test_percent_words_form():
    out = rewrite_for_speech("Rendimiento +2.9 por ciento.")
    assert "crecimiento del dos punto nueve por ciento" in out
    assert "+2.9" not in out


def test_neutral_percent():
    assert percent_to_es(14, signed=False) == "catorce por ciento"
    assert "catorce por ciento" in rewrite_for_speech("confianza 14%")


def test_decimal():
    assert decimal_to_es(0.5) == "cero punto cinco"
    assert decimal_to_es(2.9) == "dos punto nueve"


def test_money():
    out = rewrite_for_speech("Capital $21.68")
    assert "veintiún dólares con sesenta y ocho centavos" in out
    assert "$" not in out


def test_money_thousands_us():
    out = rewrite_for_speech("Capital $1,234.56")
    assert "mil doscientos treinta y cuatro dólares" in out
    assert "cincuenta y seis centavos" in out
    assert "$" not in out
    assert ".56" not in out


def test_money_thousands_eu():
    out = rewrite_for_speech("Saldo $1.234,56")
    assert "mil doscientos treinta y cuatro dólares" in out
    assert "cincuenta y seis centavos" in out
