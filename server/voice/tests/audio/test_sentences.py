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
