"""Leaked tool-call markup — built from the real corrupted ADANIPORTS journal row."""

from aaitrade.textclean import clean_model_text, looks_corrupted

# Verbatim from trade_journal id=13 in the live DB
LEAKED = (
    'Target ₹1756 is 3% away, historically hit 50% of the time in ~2 days.",\n'
    '<parameter name="stop_loss_price">1451.74 | WHY NOW: ADANIPORTS has dipped 8.3%.'
)


class TestCleanModelText:
    def test_strips_leaked_parameter_tag(self):
        out = clean_model_text(LEAKED)
        assert "<parameter" not in out
        assert "stop_loss_price" not in out or ">" not in out
        # the real content survives
        assert "Target ₹1756" in out
        assert "WHY NOW" in out

    def test_detects_corruption(self):
        assert looks_corrupted(LEAKED) is True
        assert looks_corrupted("A clean thesis about port volumes.") is False

    def test_clean_text_passes_through_unchanged(self):
        clean = "ADANIPORTS oscillates in a 1650-1859 band; entry near the floor."
        assert clean_model_text(clean) == clean

    def test_handles_none_and_empty(self):
        assert clean_model_text(None) == ""
        assert clean_model_text("") == ""

    def test_strips_invoke_and_function_tags(self):
        raw = '<invoke name="execute_trade">Buy the dip</invoke>'
        out = clean_model_text(raw)
        assert "invoke" not in out.lower()
        assert "Buy the dip" in out

    def test_respects_max_len(self):
        out = clean_model_text("x" * 500, max_len=100)
        assert len(out) <= 101
