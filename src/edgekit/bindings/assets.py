from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from .._js import JSFetcherLike, JSRequest, JSRequestInfo
from .._utils import maybe_await
from ..core.errors import RuntimeCapabilityError
from ..ffi.proxy import JSProxyLike
from ..webapi.request import Request
from ..webapi.response import Response

type StaticAssetValue = Response | bytes | str


class StaticAssets:
    _binding_kind = "assets"

    def __init__(
        self,
        raw: JSProxyLike | None = None,
        *,
        local_assets: Mapping[str, StaticAssetValue] | None = None,
    ) -> None:
        self._raw = raw
        self._local_assets = local_assets

    @classmethod
    def wrap(cls, raw: JSFetcherLike | Mapping[str, StaticAssetValue] | "StaticAssets") -> "StaticAssets":
        if isinstance(raw, StaticAssets):
            return raw
        if isinstance(raw, Mapping):
            return cls(local_assets=raw)
        return cls(cast(JSProxyLike, raw))

    @property
    def raw(self) -> JSProxyLike:
        if self._raw is None:
            raise RuntimeCapabilityError("StaticAssets.raw is only available for a JavaScript-backed binding")
        return self._raw

    @property
    def binding_kind(self) -> str:
        return self._binding_kind

    async def fetch(self, request: Request | JSRequest | str) -> Response:
        if self._local_assets is not None:
            return _fetch_static_asset_from_mapping(self._local_assets, Request.wrap(_coerce_request_input(request)))
        raw_response = await maybe_await(cast(JSFetcherLike, self.raw).fetch(_coerce_request_info(request)))
        return Response.wrap(raw_response)


def _coerce_request_info(request: Request | JSRequest | str) -> JSRequestInfo:
    return Request.wrap(_coerce_request_input(request)).raw


def _coerce_request_input(request: Request | JSRequest | str) -> Request | JSRequest:
    if isinstance(request, str):
        return Request(method="GET", url=_coerce_asset_url(request))
    return request


def _coerce_asset_url(value: str) -> str:
    if "://" in value:
        return value
    if value.startswith("/"):
        return f"https://assets.invalid{value}"
    return f"https://assets.invalid/{value}"


def _fetch_static_asset_from_mapping(
    assets: Mapping[str, StaticAssetValue],
    request: Request,
) -> Response:
    asset = assets.get(request.url.pathname)
    if asset is None:
        asset = assets.get(request.url.pathname.removeprefix("/"))
    if asset is None:
        return Response(status=404)
    if isinstance(asset, Response):
        return asset
    if isinstance(asset, bytes):
        return Response.bytes(asset)
    return Response.text(asset)
