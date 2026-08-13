#!/usr/bin/env python3
"""Qualify official/compress with native zlib and libzstd, lock, offline, import, and negative linkage evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile


PACKAGE = Path(__file__).resolve().parents[1]


class QualificationError(RuntimeError):
    pass


def run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if result.returncode != 0:
        raise QualificationError(
            "command failed (%d): %s\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, " ".join(argv), result.stdout, result.stderr)
        )
    return result


def find_tool(name: str, env: dict[str, str]) -> str | None:
    return shutil.which(name, path=env.get("PATH"))


def pkg_config(package: str, mode: str, env: dict[str, str]) -> list[str]:
    tool = find_tool("pkg-config", env)
    if tool is None:
        raise QualificationError("official/compress requires pkg-config for qualification")
    return shlex.split(run([tool, mode, package], cwd=PACKAGE, env=env).stdout)


def verify_zstd_version(env: dict[str, str]) -> None:
    tool = find_tool("pkg-config", env)
    if tool is None:
        raise QualificationError("official/compress requires pkg-config for qualification")
    res = subprocess.run(
        [tool, "--atleast-version=1.4.0", "libzstd"],
        cwd=PACKAGE,
        env=env,
        timeout=120,
    )
    if res.returncode != 0:
        raise QualificationError("official/compress requires libzstd >= 1.4.0 (pkg-config --atleast-version=1.4.0 libzstd failed)")


def optional_pkg_libs(package: str, env: dict[str, str]) -> list[str]:
    tool = find_tool("pkg-config", env)
    if tool is None:
        return []
    result = subprocess.run(
        [tool, "--libs", package], cwd=PACKAGE, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    return shlex.split(result.stdout) if result.returncode == 0 else []


def resolve_toolchain(env: dict[str, str]) -> tuple[Path, Path, Path, Path, Path]:
    root_is_set = "TOKA_ROOT" in env
    explicit_keys = ("TOKA", "TOKAC", "TOKA_LIB")
    explicit_set = [key for key in explicit_keys if key in env]
    if root_is_set and explicit_set:
        raise QualificationError(
            "set either TOKA_ROOT or TOKA/TOKAC/TOKA_LIB, not both"
        )
    if root_is_set:
        if not env["TOKA_ROOT"].strip():
            raise QualificationError("TOKA_ROOT must not be empty")
        root = Path(env["TOKA_ROOT"]).expanduser().resolve()
        toka = root / "build" / "bin" / "toka"
        tokac = root / "build" / "bin" / "tokac"
        library = root / "lib"
        runtime = library / "sys" / "toka_rt.o"
        build_driver = root / "tools" / "scripts" / "toka_build.py"
    else:
        if len(explicit_set) != len(explicit_keys):
            missing = ", ".join(key for key in explicit_keys if key not in env)
            raise QualificationError(
                "set TOKA_ROOT or all of TOKA/TOKAC/TOKA_LIB"
                + (" (missing: " + missing + ")" if missing else "")
            )
        empty = [key for key in explicit_keys if not env[key].strip()]
        if empty:
            raise QualificationError("toolchain variables must not be empty: " + ", ".join(empty))
        toka = Path(env["TOKA"]).expanduser().resolve()
        tokac = Path(env["TOKAC"]).expanduser().resolve()
        library = Path(env["TOKA_LIB"]).expanduser().resolve()
        runtime = library / "sys" / "toka_rt.o"
        build_driver = library / "toolchain" / "toka_build.py"

    required_files = {
        "toka": toka,
        "tokac": tokac,
        "toka_rt.o": runtime,
        "toka_build.py": build_driver,
    }
    missing_files = [name for name, path in required_files.items() if not path.is_file()]
    if not library.is_dir():
        missing_files.append("TOKA_LIB")
    if missing_files:
        raise QualificationError(
            "incomplete Toka toolchain (missing: %s)" % ", ".join(missing_files)
        )
    return toka, tokac, library, runtime, build_driver


def compiler_command(env: dict[str, str]) -> list[str]:
    configured = env.get("CC")
    if configured is not None:
        command = shlex.split(configured)
        if not command:
            raise QualificationError("CC must name a C compiler")
        resolved = find_tool(command[0], env)
        if resolved is None:
            raise QualificationError("CC compiler was not found: " + command[0])
        command[0] = resolved
        return command
    for candidate in ("clang-20", "clang"):
        resolved = find_tool(candidate, env)
        if resolved is not None:
            return [resolved]
    raise QualificationError("official/compress requires CC, clang-20, or clang")


def make_sdk(work: Path, source_library: Path, runtime: Path, build_driver: Path) -> Path:
    library = work / "sdk" / "lib"
    shutil.copytree(
        source_library,
        library,
        ignore=shutil.ignore_patterns("*.pyc", "__pycache__"),
    )
    runtime_dir = library / "sys"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(runtime, runtime_dir / "toka_rt.o")
    toolchain = library / "toolchain"
    toolchain.mkdir(parents=True, exist_ok=True)
    shutil.copy2(build_driver, toolchain / "toka_build.py")
    return library


def write_consumer(project: Path, dependency: Path) -> None:
    (project / "src").mkdir(parents=True)
    (project / "package.tk").write_text(
        "pub const PACKAGE = (\n"
        '    name = "compress_consumer",\n'
        '    version = "0.1.0",\n'
        "    dependencies = (\n"
        "        compress = %s,\n"
        "    )\n"
        ")\n" % json.dumps(str(dependency)),
        encoding="utf-8",
    )
    (project / "src" / "main.tk").write_text(
        "import official/compress::{Encoder, package_name}\n"
        "import official/compress/http::{gzip_response}\n"
        "import stdx/net/http::{HttpResponse}\n\n"
        "fn main() -> i32 {\n"
        '    if !package_name().as_str().equals("compress") { return 1 }\n'
        "    auto encoder# = Encoder::gzip(-1:i32).unwrap()\n"
        "    if encoder#.finish().is_err() { return 2 }\n"
        "    auto zstd_enc# = Encoder::zstd(-1:i32).unwrap()\n"
        "    if zstd_enc#.finish().is_err() { return 3 }\n"
        "    auto response# = HttpResponse::ok(string::from(\"body\"))\n"
        "    if gzip_response(cede response, -1:i32).is_err() { return 4 }\n"
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )
    (project / "build.tk").write_text(
        "import build::{Executable, run_build}\n\n"
        "fn main() -> i32 {\n"
        '    auto app# = Executable::make(c"compress_consumer", c"src/main.tk")\n'
        "    return run_build(app)\n"
        "}\n",
        encoding="utf-8",
    )


def write_plain_http_consumer(project: Path) -> None:
    (project / "src").mkdir(parents=True)
    (project / "package.tk").write_text(
        "pub const PACKAGE = (\n"
        '    name = "plain_http_consumer",\n'
        '    version = "0.1.0",\n'
        "    dependencies = (),\n"
        ")\n",
        encoding="utf-8",
    )
    (project / "src" / "main.tk").write_text(
        "import stdx/net/http::{HttpResponse}\n\n"
        "fn main() -> i32 {\n"
        "    auto response = HttpResponse::ok(string::from(\"body\"))\n"
        "    if response.to_string().len() == 0:usize { return 1 }\n"
        "    return 0\n"
        "}\n",
        encoding="utf-8",
    )
    (project / "build.tk").write_text(
        "import build::{Executable, run_build}\n\n"
        "fn main() -> i32 {\n"
        '    auto app# = Executable::make(c"plain_http_consumer", c"src/main.tk")\n'
        "    return run_build(app)\n"
        "}\n",
        encoding="utf-8",
    )


def link_program(compiler: list[str], runtime: Path, ir: Path, bridges: list[Path],
                 output: Path, env: dict[str, str]) -> None:
    args = [*compiler, str(ir), *[str(b) for b in bridges], str(runtime),
            "-o", str(output), *pkg_config("zlib", "--libs", env),
            *pkg_config("libzstd", "--libs", env)]
    args.extend(optional_pkg_libs("openssl", env))
    if platform.system() == "Darwin":
        sdk = run(["xcrun", "--show-sdk-path"], cwd=PACKAGE, env=env).stdout.strip()
        args.extend(["-isysroot", sdk])
    run(args, cwd=PACKAGE, env=env)


def compile_and_run(tokac: Path, compiler: list[str], runtime: Path, sdk: Path,
                    package: Path, source: Path, bridges: list[Path], output: Path,
                    env: dict[str, str]) -> None:
    ir = output.with_suffix(".ll")
    run([str(tokac), "-I", str(sdk), "-I", str(package / "lib"),
         "--emit-llvm", str(source), "-o", str(ir)], cwd=PACKAGE, env=env)
    link_program(compiler, runtime, ir, bridges, output, env)
    run([str(output)], cwd=PACKAGE, env=env)


def assert_no_compression_linkage(program: Path, env: dict[str, str]) -> None:
    if platform.system() == "Darwin":
        tool = find_tool("otool", env)
        if tool is None:
            raise QualificationError("otool is required for the macOS negative linkage check")
        res = run([tool, "-L", str(program)], cwd=PACKAGE, env=env)
        pattern = r"(?:^|/)(?:libzstd|libz)(?:\.\d+)*\.dylib(?:\s|$)"
    elif platform.system() == "Linux":
        tool = find_tool("readelf", env)
        if tool is None:
            raise QualificationError("readelf is required for the Linux negative linkage check")
        res = run([tool, "-d", str(program)], cwd=PACKAGE, env=env)
        pattern = r"\(needed\).*\[(?:libzstd|libz)\.so(?:\.[^]]+)*\]"
    else:
        raise QualificationError("negative linkage qualification supports only Linux and macOS")
    if re.search(pattern, res.stdout, flags=re.IGNORECASE | re.MULTILINE):
        raise QualificationError(
            "plain_http_consumer directly linked libz or libzstd:\n" + res.stdout
        )


def main() -> int:
    host_env = dict(os.environ)
    toka, tokac, source_library, runtime, build_driver = resolve_toolchain(host_env)
    compiler = compiler_command(host_env)
    host_env["CC"] = shlex.join(compiler)
    verify_zstd_version(host_env)

    with tempfile.TemporaryDirectory(prefix="toka-compress-package-") as temporary:
        work = Path(temporary)
        sdk = make_sdk(work, source_library, runtime, build_driver)
        sdk_runtime = sdk / "sys" / "toka_rt.o"
        base_env = dict(host_env)
        base_env.update({"TOKAC": str(tokac), "TOKA_LIB": str(sdk)})
        base_env.pop("TOKA_ROOT", None)
        base_env.pop("TOKA", None)
        dependency = work / "compress"
        shutil.copytree(PACKAGE, dependency, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))

        bridge_zlib = work / "compress_zlib.o"
        run([*compiler, "-Wall", "-Wextra", "-Werror", "-c",
             str(dependency / "native" / "compress_zlib.c"), "-o", str(bridge_zlib),
             *pkg_config("zlib", "--cflags", base_env)], cwd=PACKAGE, env=base_env)

        bridge_zstd = work / "compress_zstd.o"
        run([*compiler, "-Wall", "-Wextra", "-Werror", "-c",
             str(dependency / "native" / "compress_zstd.c"), "-o", str(bridge_zstd),
             *pkg_config("libzstd", "--cflags", base_env)], cwd=PACKAGE, env=base_env)

        bridges = [bridge_zlib, bridge_zstd]

        compile_and_run(tokac, compiler, sdk_runtime, sdk, dependency,
                        dependency / "tests" / "compress_v1.tk", bridges,
                        work / "compress_v1", base_env)
        compile_and_run(tokac, compiler, sdk_runtime, sdk, dependency,
                        dependency / "tests" / "zstd_v1.tk", bridges,
                        work / "compress_zstd_v1", base_env)
        compile_and_run(tokac, compiler, sdk_runtime, sdk, dependency,
                        dependency / "tests" / "http_v1.tk", bridges,
                        work / "compress_http_v1", base_env)

        project = work / "consumer"
        write_consumer(project, dependency)
        run([str(toka), "fetch"], cwd=project, env=base_env)
        lock = project / "package.lock"
        locked = lock.read_bytes()
        if not locked.startswith(b"toka-lock-v1\n") or b"compress" not in locked:
            raise QualificationError("compress consumer did not produce a v1 lock with compress")

        offline_env = dict(base_env)
        offline_env["TOKA_OFFLINE"] = "1"
        run([str(toka), "fetch"], cwd=project, env=offline_env)
        if lock.read_bytes() != locked:
            raise QualificationError("offline compress fetch changed package.lock")
        run([str(toka), "build"], cwd=project, env=offline_env)
        program = project / "target" / "debug" / "compress_consumer"
        if not program.is_file():
            raise QualificationError("toka build did not produce the native package consumer")
        run([str(program)], cwd=project, env=offline_env)

        plain = work / "plain_http_consumer"
        write_plain_http_consumer(plain)
        no_zlib_pkg_config = work / "no-zlib-pkg-config"
        no_zlib_pkg_config.mkdir()
        plain_env = dict(base_env)
        plain_env["PKG_CONFIG_LIBDIR"] = str(no_zlib_pkg_config)
        plain_env["PKG_CONFIG_PATH"] = ""
        run([str(toka), "build"], cwd=plain, env=plain_env)
        plain_program = plain / "target" / "debug" / "plain_http_consumer"
        if not plain_program.is_file():
            raise QualificationError("plain HTTP consumer did not build without zlib/zstd package discovery")
        run([str(plain_program)], cwd=plain, env=plain_env)
        assert_no_compression_linkage(plain_program, plain_env)

    print(json.dumps({
        "result": "pass",
        "schema": "toka.official-compress-package-v1",
        "stages": {
            "native_zlib_bridge": "pass",
            "native_zstd_bridge": "pass",
            "streaming_boundary_suite": "pass",
            "streaming_zstd_suite": "pass",
            "http_content_encoding_policy": "pass",
            "locked_local_dependency": "pass",
            "offline_lock_replay": "pass",
            "native_toka_build_run": "pass",
            "plain_http_consumer_without_zlib_zstd": "pass",
            "negative_linkage_check": "pass",
        },
        "version": 1,
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, QualificationError, subprocess.TimeoutExpired) as error:
        print("FAIL: " + str(error))
        raise SystemExit(1)
