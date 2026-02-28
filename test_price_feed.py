def test_price_feed_candle_storage():
    # create an instance and manually append fake candles
    from signals.price_feed import PriceFeed

    pf = PriceFeed(max_candles=5)
    # simulate historical loader behaviour by appending directly
    pf.prices["btc"].append({"high": 100, "low": 90, "close": 95})
    pf.prices["btc"].append({"high": 102, "low": 91, "close": 98})
    pf.volumes["btc"].append(10)
    pf.volumes["btc"].append(12)
    pf.latest["btc"] = 98

    signals = pf.get_signals("btc")
    # should have both prices list (closings) and candles list
    assert "prices" in signals
    assert "candles" in signals
    assert signals["prices"] == [95, 98]
    assert isinstance(signals["candles"][0], dict)
    assert signals["latest"] == 98
    assert "stale" in signals
