class SentenceBuffer:
    """Accumulates streaming text until terminal sentence boundaries arrive."""

    _TERMINATORS = frozenset("。！？.!?\n")

    def __init__(self) -> None:
        self._tail = ""

    def feed(self, text: str) -> list[str]:
        self._tail += text
        sentences: list[str] = []
        start = 0
        for index, character in enumerate(self._tail):
            if character in self._TERMINATORS:
                sentence = self._tail[start : index + 1]
                if sentence.strip():
                    sentences.append(sentence)
                start = index + 1
        self._tail = self._tail[start:]
        return sentences

    def flush(self) -> list[str]:
        tail, self._tail = self._tail, ""
        return [tail] if tail.strip() else []
