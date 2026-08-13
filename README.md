# `official/compress`

Official opt-in compression package for Toka. Package version `0.1.0` is a
release candidate and has not yet been published. This document describes API
profile v1.2.

## Migration status

This repository is undergoing standalone qualification and is not yet the
canonical package source. Until qualification, release, and locked registry
consumer replay are complete, the authoritative source remains
[`tokalang/toka/official/compress`](https://github.com/tokalang/toka/tree/main/official/compress).

Cutover will be one-way. The compiler repository copy will be removed after a
successful standalone release; this repository will not become a long-lived
mirror or submodule.

## API

`official/compress` provides streaming Gzip, Zlib, and Zstd encoding and
decoding. Its optional `official/compress/http` module provides an explicit HTTP
`Content-Encoding` policy; it does not alter the HTTP core, act as an archive
library, or become a framework.

```toka
import official/compress::{Decoder, Encoder}

auto encoder# = Encoder::gzip(-1).unwrap()
auto first = encoder#.write(cede input_chunk).unwrap()
auto trailer = encoder#.finish().unwrap()

auto decoder# = Decoder::zstd(64 * 1024 * 1024).unwrap()
auto plain = decoder#.write(cede compressed_chunk).unwrap()
auto final_plain = decoder#.finish().unwrap()
```

Each `write` consumes one owned `Bytes` chunk and returns only the bytes
produced by that step. `finish` must be called once to flush an encoder or
validate a decoder trailer; it closes the handle on both success and failure.
The decoder requires an explicit total output limit, so compressed input never
silently expands into an unbounded allocation. Zstd decoding additionally
enforces a fixed 128 MiB maximum window-memory ceiling.

The package uses system zlib and libzstd 1.4.0 or newer only in its private C
boundaries. Its public API exposes no native handle or raw pointer. `toka build`
compiles the declared bridges and links both libraries automatically for a
locked `official/compress` consumer; base Toka programs do not acquire either
native dependency.

The base streaming module deliberately excludes automatic HTTP handling,
stream adapters, concatenated frames, raw DEFLATE, archive containers, Brotli,
and automatic retry or recovery after a malformed compressed stream.

## Optional HTTP policy

```toka
import official/compress/http::{GzipRequestLimits, decode_gzip_request_body,
                                encode_response_for_request}

auto response = encode_response_for_request(
    cede response,
    request.headers.get("accept-encoding"),
    -1
).unwrap()

auto limits = GzipRequestLimits::new(8 * 1024 * 1024, 32).unwrap()
auto request = decode_gzip_request_body(cede gzip_request, limits).unwrap()
```

The policy negotiates `gzip` or `identity` from `Accept-Encoding`, including
`q` values and `*`. A gzip response is finished before it gets its
`Content-Encoding`, `Vary`, and `Content-Length` headers. Request decompression
is never automatic: callers explicitly invoke it with both decoded-byte and
compression-ratio limits. Malformed, truncated, oversized, or over-expanded
gzip fails closed.

The base `stdx/net/http` package remains independent of zlib and libzstd.
Import this module only in applications that choose the compression policy.

## Qualification

The required qualification toolchain is the published Toka `v1.0.0-rc.4` SDK.
Install zlib, libzstd 1.4.0 or newer, pkg-config, and Clang, then provide either
an installed SDK explicitly:

```sh
TOKA=/path/to/bin/toka \
TOKAC=/path/to/bin/tokac \
TOKA_LIB=/path/to/lib \
python3 tests/qualify_package.py
```

or a Toka source checkout whose `build/bin/toka`, `build/bin/tokac`, and
`lib/sys/toka_rt.o` have already been built:

```sh
TOKA_ROOT=/path/to/toka python3 tests/qualify_package.py
```

Qualification builds and runs the native streaming and HTTP suites, exercises
a locked local consumer and offline replay, and proves that an ordinary HTTP
consumer does not directly link zlib or libzstd.

## Migration provenance

Package history was imported with `git subtree split` from
[`tokalang/toka`](https://github.com/tokalang/toka) at source snapshot
`07d86771cc5b28d73f75e8ab560284315a904685`, original path
`official/compress`. The last source commit affecting that path before the
snapshot was `0eb95497662c6588f439f5279ee5c8f5a3333ae1`; the split history tip is
`2d380d33c3ce828ecefa11043666acb8224ce52b`.

The standalone scaffold is retained as the first parent of the import merge;
the package's extracted Toka history is retained as the second parent.

## License

Apache License 2.0. See [LICENSE](LICENSE). System zlib and libzstd remain
subject to their own licenses; this repository does not vendor their source.
