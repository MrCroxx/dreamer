"""Read complete, validated operations from an unfinished JSON object."""

import json

from .schema import MAX_OPERATIONS, Operation


class PlanStream:
    def __init__(self):
        self.text = ""
        self.position = 0
        self.state = "object"
        self.key = None
        self.seen = set()
        self.decoder = json.JSONDecoder()

    def feed(self, fragment: str) -> list[Operation]:
        self.text += fragment
        if len(self.text) > 500_000:
            raise ValueError("CAD response exceeds the size limit")
        output = []
        while True:
            while self.position < len(self.text) and self.text[self.position].isspace():
                self.position += 1
            if self.position == len(self.text) or self.state == "done":
                return output
            char = self.text[self.position]
            if self.state == "object":
                if char != "{":
                    raise ValueError("Expected a JSON CAD object")
                self.position += 1
                self.state = "key"
            elif self.state in {"key", "value", "operation"}:
                if self.state == "operation" and char == "]":
                    self.position += 1
                    self.state = "done"
                    continue
                if self.state == "value" and self.key == "operations":
                    if char != "[":
                        raise ValueError("Expected an operations array")
                    self.position += 1
                    self.state = "operation"
                    continue
                try:
                    value, end = self.decoder.raw_decode(self.text, self.position)
                except json.JSONDecodeError:
                    return output
                if self.state == "key":
                    if not isinstance(value, str):
                        raise ValueError("Expected a JSON object key")
                    self.key, self.state = value, "colon"
                elif self.state == "operation":
                    op = Operation.model_validate(value)
                    if (
                        len(self.seen) >= MAX_OPERATIONS
                        or op.id in self.seen
                        or any(ref not in self.seen for ref in op.inputs)
                    ):
                        raise ValueError("Invalid streamed operation graph")
                    self.seen.add(op.id)
                    output.append(op)
                    self.state = "operation_separator"
                else:
                    self.state = "separator"
                self.position = end
            elif self.state == "colon":
                if char != ":":
                    raise ValueError("Expected a JSON colon")
                self.position += 1
                self.state = "value"
            else:
                if char in "}]":
                    self.state = "done"
                elif char == ",":
                    self.position += 1
                    self.state = (
                        "operation" if self.state == "operation_separator" else "key"
                    )
                else:
                    raise ValueError("Expected a JSON separator")
