from __future__ import annotations

from collections.abc import MutableSequence
from typing import Generic, TypeVar

from .._js import JSQueueProducerLike
from .._utils import maybe_await

T = TypeVar("T")


class QueueProducer(Generic[T]):
    _binding_kind = "queue"

    def __init__(self, raw: JSQueueProducerLike | MutableSequence[T]) -> None:
        self._raw = raw

    @classmethod
    def wrap(cls, raw: JSQueueProducerLike | MutableSequence[T]) -> "QueueProducer[T]":
        if isinstance(raw, QueueProducer):
            return raw
        return cls(raw)

    @property
    def raw(self) -> JSQueueProducerLike | MutableSequence[T]:
        return self._raw

    @property
    def binding_kind(self) -> str:
        return self._binding_kind

    async def send(self, message: T) -> None:
        if isinstance(self._raw, MutableSequence):
            self._raw.append(message)
            return
        await maybe_await(self._raw.send(message))

    async def send_batch(self, messages: list[T]) -> None:
        if not isinstance(self._raw, MutableSequence):
            await maybe_await(self._raw.sendBatch(messages))
            return
        for message in messages:
            await self.send(message)
