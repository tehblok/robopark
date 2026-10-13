"""Operator interface for optional AGX acceleration experiments."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from dataclasses import asdict
from pathlib import Path

from . import edge, media_backend, operations, registry, server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("/var/lib/robopark/accelerator"))
    commands = parser.add_subparsers(dest="action", required=True)
    status = commands.add_parser("status")
    status.add_argument("--trtexec", type=Path)
    register = commands.add_parser("register")
    register.add_argument("--id", required=True)
    register.add_argument("--kind", required=True, choices=("checkpoint", "adapter", "onnx"))
    register.add_argument("--path", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--id", required=True)
    build = commands.add_parser("build")
    build.add_argument("--id", required=True)
    build.add_argument(
        "--shapes", type=Path, required=True, help='JSON object, e.g. {"image":[1,3,224,224]}'
    )
    build.add_argument("--backend", choices=("gpu", "dla0", "dla1"), default="gpu")
    build.add_argument("--allow-gpu-fallback", action="store_true")
    build.add_argument("--trtexec", type=Path)
    infer = commands.add_parser("infer")
    infer.add_argument("--engine-id", required=True)
    infer.add_argument("--input", action="append", required=True, help="TENSOR=/absolute/input.bin")
    infer.add_argument("--output", type=Path, required=True)
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--backend", choices=("gpu", "dla0", "dla1"), required=True)
    smoke.add_argument("--trtexec", type=Path)
    thumb = commands.add_parser("thumbnail")
    thumb.add_argument("--input", type=Path, required=True)
    thumb.add_argument("--output", type=Path, required=True)
    thumb.add_argument("--width", type=int, default=640)
    thumb.add_argument("--height", type=int, default=480)
    thumb.add_argument("--backend", choices=("auto", "hardware", "software"), default="auto")
    serve = commands.add_parser("serve")
    serve.add_argument("--socket", type=Path, default=Path("/run/robopark-accel/accelerator.sock"))
    profile = commands.add_parser("edge-configure")
    profile.add_argument("--id", required=True)
    profile.add_argument("--tool-parser", default="auto")
    commands.add_parser("edge-serve")
    commands.add_parser("edge-check")
    args = parser.parse_args(argv)
    try:
        if args.action != "status" and os.geteuid() == 0:
            raise ValueError("run_as_robopark_accel_not_root")
        if args.action == "status":
            result = operations.status(args.state, trtexec=args.trtexec)
        elif args.action == "register":
            result = registry.register(args.state, args.id, args.kind, args.path)
        elif args.action == "verify":
            result = registry.verify(args.state, args.id)
        elif args.action == "build":
            result = operations.build(
                args.state,
                args.id,
                registry.read_json(args.shapes),
                args.backend,
                args.allow_gpu_fallback,
                args.trtexec,
            )
        elif args.action == "infer":
            inputs = {}
            for item in args.input:
                name, separator, path = item.partition("=")
                if not separator or not name or name in inputs or not Path(path).is_absolute():
                    raise ValueError("input_argument_invalid")
                inputs[name] = Path(path)
            result = operations.infer(args.state, args.engine_id, inputs, args.output)
        elif args.action == "smoke":
            result = operations.smoke(args.state, args.backend, trtexec=args.trtexec)
        elif args.action == "thumbnail":
            result = asdict(
                media_backend.thumbnail(
                    args.input, args.output, args.width, args.height, backend=args.backend
                )
            )
        elif args.action == "serve":
            server.serve(args.state, args.socket)
            return 0
        elif args.action == "edge-configure":
            result = registry.configure_edge(args.state, args.id, parser=args.tool_parser)
            key = args.state / "edge-api-key"
            try:
                fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                edge.api_key(args.state)
            else:
                with os.fdopen(fd, "w") as stream:
                    stream.write(secrets.token_hex(32) + "\n")
        elif args.action == "edge-serve":
            edge.serve(args.state)
            return 0
        else:
            result = edge.check(args.state)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError) as error:
        # Stable diagnostics only; no raw paths, model content, URLs or secrets.
        reason = (
            str(error)
            if isinstance(error, (ValueError, RuntimeError))
            else "dependency_or_artifact_unavailable"
        )
        if not reason or len(reason) > 100 or not all(c.isalnum() or c in "_:-." for c in reason):
            reason = "operation_failed"
        print(json.dumps({"state": "blocked", "reason": reason}))
        return 2
