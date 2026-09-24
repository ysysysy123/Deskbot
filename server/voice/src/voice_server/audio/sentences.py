class SentenceBuffer:
    """Accumulates streaming text until terminal sentence boundaries arrive."""

    _TERMINATORS = frozenset("。！？.!?\n")

    def __init__(self, max_chars: int = 100) -> None:
        self._tail = ""
        self._max_chars = max_chars

    def feed(self, text: str) -> list[str]:
        self._tail += text
        sentences: list[str] = []
        while self._tail:
            window = self._tail[:self._max_chars]
            boundary = next((index + 1 for index, char in enumerate(window) if char in self._TERMINATORS), 0)
            if not boundary:
                if len(self._tail) < self._max_chars:
                    break
                boundary = max(window.rfind(mark) for mark in "，,；;：: ") + 1
                boundary = boundary if boundary and boundary >= self._max_chars // 2 else self._max_chars
            sentence = self._tail[:boundary]
            if sentence.strip():
                sentences.append(sentence)
            self._tail = self._tail[boundary:]
        return sentences

    def flush(self) -> list[str]:
        tail, self._tail = self._tail, ""
        return [tail] if tail.strip() else []
