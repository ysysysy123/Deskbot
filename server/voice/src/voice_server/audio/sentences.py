class SentenceBuffer:
    """Accumulates streaming text until terminal sentence boundaries arrive."""

    _TERMINATORS = frozenset("。！？.!?\n")

    def __init__(self, max_chars: int = 100, *, first_clause_chars: int = 0) -> None:
        self._tail = ""
        self._max_chars = max_chars
        self._first_clause_chars = first_clause_chars
        self._emitted = False

    def feed(self, text: str) -> list[str]:
        self._tail += text
        sentences: list[str] = []
        while self._tail:
            window = self._tail[:self._max_chars]
            boundary = 0
            for index, char in enumerate(window):
                # A decimal may straddle two model deltas ("23." + "5度").
                if char == "." and index and window[index - 1].isdigit():
                    if index + 1 == len(self._tail):
                        break
                    if self._tail[index + 1].isdigit():
                        continue
                short_clause = (
                    not self._emitted and self._first_clause_chars > 0
                    and index + 1 >= self._first_clause_chars and char in "，,；;：:"
                )
                if char in self._TERMINATORS or short_clause:
                    boundary = index + 1
                    break
            if not boundary:
                if len(self._tail) < self._max_chars:
                    break
                boundary = max(window.rfind(mark) for mark in "，,；;：: ") + 1
                boundary = boundary if boundary and boundary >= self._max_chars // 2 else self._max_chars
            sentence = self._tail[:boundary]
            if sentence.strip():
                sentences.append(sentence)
                self._emitted = True
            self._tail = self._tail[boundary:]
        return sentences

    def flush(self) -> list[str]:
        tail, self._tail = self._tail, ""
        return [tail] if tail.strip() else []
