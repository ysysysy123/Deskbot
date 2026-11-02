from voice_server.audio.sentences import SentenceBuffer


def test_emits_only_complete_sentences_until_flush():
    """Would fail if fragments before terminal punctuation were emitted prematurely."""
    buffer = SentenceBuffer()

    assert buffer.feed("你好，世界。下一") == ["你好，世界。"]
    assert buffer.feed("句！尾巴") == ["下一句！"]
    assert buffer.flush() == ["尾巴"]


def test_preserves_english_terminal_punctuation_and_newline_boundaries():
    """Would fail if stream boundaries lost English punctuation or newlines."""
    buffer = SentenceBuffer()

    assert buffer.feed("One. Two? Three!\nFour") == ["One.", " Two?", " Three!"]
    assert buffer.flush() == ["Four"]


def test_ignores_blank_completed_and_flushed_output():
    """Would fail if whitespace-only segments reached downstream TTS."""
    buffer = SentenceBuffer()

    assert buffer.feed("  \n") == []
    assert buffer.flush() == []


def test_long_unpunctuated_text_is_emitted_before_end_of_stream():
    buffer = SentenceBuffer(max_chars=10)
    assert buffer.feed("abcdefghijtail") == ["abcdefghij"]
    assert buffer.flush() == ["tail"]


def test_length_limit_also_applies_when_one_delta_contains_a_long_sentence():
    buffer = SentenceBuffer(max_chars=10)
    assert buffer.feed("abcdefghijklmnop。") == ["abcdefghij", "klmnop。"]


def test_decimal_is_kept_intact_across_stream_deltas():
    buffer = SentenceBuffer()
    assert buffer.feed("温度是23.") == []
    assert buffer.feed("5度，版本是3.14。") == ["温度是23.5度，版本是3.14。"]
    assert buffer.feed("Count 3.") == []
    assert buffer.feed(" Next.") == ["Count 3.", " Next."]


def test_only_first_clause_uses_short_comma_boundary():
    buffer = SentenceBuffer(first_clause_chars=6)
    assert buffer.feed("好的，我先给你讲一个例子，后面继续，") == ["好的，我先给你讲一个例子，"]
    assert buffer.feed("最后总结。") == ["后面继续，最后总结。"]
