"""Tests for price gap detection."""

import pandas as pd

from agents.technical.gaps import detect_gaps


def test_detect_gap_up_unfilled():
    df = pd.DataFrame({
        "Open": [100, 100, 105],
        "High": [101, 101, 108],
        "Low": [99, 99, 104.5],
        "Close": [100, 100, 107],
        "Volume": [1e6, 1e6, 1e6],
    }, index=pd.date_range("2025-01-01", periods=3, freq="D"))

    gaps = detect_gaps(df, timeframe="1D", min_gap_pct=0.1)
    assert len(gaps) == 1
    assert gaps[0].gap_type == "gap_up"
    assert gaps[0].filled is False
    assert gaps[0].gap_bottom == 100
    assert gaps[0].fill_target == 100


def test_detect_gap_gets_filled():
    df = pd.DataFrame({
        "Open": [100, 105, 106],
        "High": [101, 108, 107],
        "Low": [99, 104.5, 99],
        "Close": [100, 107, 105],
        "Volume": [1e6, 1e6, 1e6],
    }, index=pd.date_range("2025-01-01", periods=3, freq="D"))

    gaps = detect_gaps(df, timeframe="1D", min_gap_pct=0.1)
    assert len(gaps) == 1
    assert gaps[0].filled is True


def test_detect_gap_down():
    df = pd.DataFrame({
        "Open": [100, 100, 94],
        "High": [101, 101, 95.5],
        "Low": [99, 99, 93],
        "Close": [100, 100, 94.5],
        "Volume": [1e6, 1e6, 1e6],
    }, index=pd.date_range("2025-01-01", periods=3, freq="D"))

    gaps = detect_gaps(df, timeframe="1D", min_gap_pct=0.1)
    assert len(gaps) == 1
    assert gaps[0].gap_type == "gap_down"
    assert gaps[0].fill_target == 100
