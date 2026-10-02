import codecs
from collections.abc import Mapping
from dataclasses import dataclass

from privacy_gateway.domain.redaction import PREFIX, TOKEN, Binding, PrivacyError


def _limit(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("INVALID_STREAM_LIMIT")
    return value


def _utf8_size(text: str, remaining: int) -> int:
    if len(text) > remaining:
        raise PrivacyError("RESPONSE_LIMIT")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise PrivacyError("INVALID_UTF8") from None
    if size > remaining:
        raise PrivacyError("RESPONSE_LIMIT")
    return size


@dataclass(frozen=True)
class SSEEvent:
    event: str
    data: str


class SSEDecoder:
    def __init__(
        self,
        *,
        max_wire_bytes: int = 1_048_576,
        max_frame_bytes: int = 65_536,
        max_events: int = 4096,
    ) -> None:
        self._max_wire = _limit(max_wire_bytes)
        self._max_frame = _limit(max_frame_bytes)
        self._max_events = _limit(max_events)
        self._decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        self._closed = False
        self._after_cr = False
        self._line: list[str] = []
        self._data: list[str] = []
        self._event = ""
        self._fields_pending = False
        self._frame_bytes = 0
        self.wire_bytes = 0
        self.event_count = 0

    def _check_open(self) -> None:
        if self._closed:
            raise PrivacyError("STREAM_CLOSED")

    def cancel(self) -> None:
        self._closed = True
        self._decoder.reset()
        self._line.clear()
        self._data.clear()
        self._event = ""
        self._fields_pending = False
        self._frame_bytes = 0
        self._after_cr = False

    def _end_line(self) -> SSEEvent | None:
        line = "".join(self._line)
        self._line.clear()
        if not line:
            result = None
            if self._data:
                if self.event_count >= self._max_events:
                    raise PrivacyError("RESPONSE_LIMIT")
                result = SSEEvent(self._event or "message", "\n".join(self._data))
                self.event_count += 1
            self._data.clear()
            self._event = ""
            self._fields_pending = False
            self._frame_bytes = 0
            return result
        if line.startswith(":"):
            return None
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        else:
            raise PrivacyError("INVALID_SSE")
        self._fields_pending = True
        return None

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        self._check_open()
        try:
            if not isinstance(chunk, bytes):
                raise PrivacyError("INVALID_SSE")
            if len(chunk) > self._max_wire - self.wire_bytes:
                raise PrivacyError("RESPONSE_LIMIT")
            self.wire_bytes += len(chunk)
            text = self._decoder.decode(chunk, final=False)
            result = []
            for char in text:
                if self._after_cr:
                    self._after_cr = False
                    if char == "\n":
                        continue
                if char in "\r\n":
                    self._after_cr = char == "\r"
                    event = self._end_line()
                    if event is not None:
                        result.append(event)
                    continue
                self._frame_bytes += len(char.encode("utf-8"))
                if self._frame_bytes > self._max_frame:
                    raise PrivacyError("RESPONSE_LIMIT")
                self._line.append(char)
            return result
        except UnicodeError:
            self.cancel()
            raise PrivacyError("INVALID_UTF8") from None
        except PrivacyError:
            self.cancel()
            raise

    def finish(self) -> None:
        self._check_open()
        try:
            self._decoder.decode(b"", final=True)
            if self._decoder.getstate()[0]:
                raise PrivacyError("INVALID_UTF8")
            if self._line or self._fields_pending:
                raise PrivacyError("INCOMPLETE_SSE")
        except UnicodeError:
            raise PrivacyError("INVALID_UTF8") from None
        finally:
            self.cancel()


class IncrementalRestorer:
    def __init__(
        self,
        manifest: Mapping[str, Binding],
        *,
        max_source_bytes: int = 200_000,
        max_output_bytes: int = 200_000,
    ) -> None:
        self._max_source = _limit(max_source_bytes)
        self._max_output = _limit(max_output_bytes)
        self._manifest = dict(manifest)
        self._pending = ""
        self._closed = False
        self.source_bytes = 0
        self.output_bytes = 0

    def _check_open(self) -> None:
        if self._closed:
            raise PrivacyError("STREAM_CLOSED")

    def cancel(self) -> None:
        self._closed = True
        self._pending = ""
        self._manifest.clear()

    def _append(self, output: list[str], text: str) -> None:
        self.output_bytes += _utf8_size(text, self._max_output - self.output_bytes)
        output.append(text)

    def _token_char(self, char: str, output: list[str]) -> None:
        position = len(self._pending)
        head = "[[PG_v1_"
        if position < len(head):
            valid = char == head[position]
        elif position < 40:
            valid = char in "0123456789abcdef"
        else:
            valid = char == "]"
        if not valid:
            raise PrivacyError("MALFORMED_TOKEN")
        self._pending += char
        if len(self._pending) == 42:
            if not TOKEN.fullmatch(self._pending):
                raise PrivacyError("MALFORMED_TOKEN")
            binding = self._manifest.get(self._pending)
            if binding is None:
                raise PrivacyError("UNKNOWN_TOKEN")
            if binding.token != self._pending:
                raise PrivacyError("INVALID_BINDING")
            self._append(
                output, binding.value if binding.restore_allowed else "[bí mật không phục hồi]"
            )
            self._pending = ""

    def feed(self, source: str) -> str:
        self._check_open()
        try:
            if not isinstance(source, str):
                raise PrivacyError("INVALID_STREAM_TEXT")
            self.source_bytes += _utf8_size(source, self._max_source - self.source_bytes)
            output: list[str] = []
            for char in source:
                if self._pending.startswith(PREFIX):
                    self._token_char(char, output)
                    continue
                self._pending += char
                while self._pending and not PREFIX.startswith(self._pending):
                    self._append(output, self._pending[0])
                    self._pending = self._pending[1:]
            return "".join(output)
        except PrivacyError:
            self.cancel()
            raise

    def finish(self) -> str:
        self._check_open()
        try:
            if self._pending.startswith(PREFIX):
                raise PrivacyError("MALFORMED_TOKEN")
            output: list[str] = []
            self._append(output, self._pending)
            return "".join(output)
        finally:
            self.cancel()
