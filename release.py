"""Prepare and control the source release of Strata's Korean output policy."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from patch_ko_controller import patch_controller
from patch_strata_utf8_request import generate_request_source, verify_source
from utf8_policy_table import compile_table


HERE = Path(__file__).resolve().parent
TESTED_UPSTREAM = "09b603886925994d0d4461857b01321ef3aa431c"
COMMIT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
STOP = {248044: "<|endoftext|>", 248046: "<|im_end|>"}
PIECES_SHA = "06e3e9fe66f303225951f9bef073285be4de55098b1f51d640f0e37b9dd24e67"
TABLE_SHA = "51dd36c2b7e6758f93b3b99837e0c3ceeea41642cabf7208d327ab855b8d4ad6"
PATCHES = {"src/core/verify.cpp": verify_source, "src/program/generate.cpp": generate_request_source,
           "serve/server.py": patch_controller}
UNSUPPORTED = {"--batch", "--batch-mtp", "--slots", "--parallel", "--mtp-prob", "--mtp-coupled", "--eos-ids"}
STATE_ENV = "STRATA_KO_POLICY_STATE"
TABLE_ENV = "STRATA_KO_UTF8_TABLE"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    write_private(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_private(path, data):
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            pending = Path(stream.name)
            stream.write(data)
        pending.replace(path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def set_policy(runtime, policy):
    write_private(runtime / "policy-state", (policy + "\n").encode("ascii"))


def checked_source_members(members):
    for member in members:
        if not (member.isfile() or member.isdir()):
            raise tarfile.FilterError(f"unexpected archive entry type: {member.name!r}")
    return members


def export_source(checkout, target):
    git = ["git", "--no-replace-objects"]
    head = subprocess.check_output([*git, "rev-parse", "--verify", "HEAD^{commit}"], cwd=checkout, text=True).strip()
    if not COMMIT_ID.fullmatch(head):
        raise ValueError("could not resolve a Strata source commit")
    target.mkdir()
    with tempfile.TemporaryFile() as stream:
        subprocess.run([*git, "archive", "--format=tar", head], cwd=checkout, stdout=stream, check=True)
        stream.seek(0)
        with tarfile.open(fileobj=stream) as source:
            members = checked_source_members(source.getmembers())
            source.extractall(target, members=members, filter="data")
    return head


def apply_patches(source):
    for name, patch in PATCHES.items():
        target = source / name
        try:
            patched = patch(target)
        except (FileNotFoundError, ValueError, SyntaxError) as exc:
            raise ValueError(f"Strata patch is incompatible with {name}: {exc}") from exc
        target.write_text(patched, encoding="utf-8", newline="\n")
        if target.suffix == ".cpp":
            shutil.copyfile(HERE / "ko_utf8_policy.hpp", target.parent / "ko_utf8_policy.hpp")
    shutil.copyfile(HERE / "ko_serving_policy.py", source / "serve/ko_serving_policy.py")


def compile_pack_table(source, pack):
    sys.path.insert(0, str(source / "tools"))
    try:
        from strata_tokenizer import Tokenizer
    finally:
        sys.path.pop(0)

    folder = pack / "tokenizer"
    vocab = read_json(folder / "vocab.json")
    kinds = read_json(folder / "token_type.json")
    config = read_json(folder / "tokenizer.json")
    if len(vocab) != 248320 or set(vocab.values()) != set(range(len(vocab))):
        raise ValueError("expected contiguous Qwen3.8 Flash Next tokenizer IDs")
    tokens = [None] * len(vocab)
    for text, index in vocab.items():
        tokens[index] = text
    if any(tokens[index] != text for index, text in STOP.items()):
        raise ValueError("the pack has unexpected EOS tokens")
    # token_bytes uses the vocabulary and byte map, independently of BPE merge ordering.
    if not (folder / "merges.txt").is_file():
        raise ValueError("the Strata tokenizer folder is missing merges.txt")
    tokenizer = Tokenizer(tokens, [], kinds, config.get("pre") or "qwen35", config.get("special_ids") or None)
    payload, meta = compile_table(tokenizer, set(STOP))
    if meta["pieces_sha256"] != PIECES_SHA or meta["table_sha256"] != TABLE_SHA:
        raise ValueError("the pack vocabulary or its token types differ from the audited tokenizer")
    return payload, meta


def prepare(args):
    output = args.runtime.resolve()
    if output.exists():
        raise ValueError(f"runtime directory already exists: {output}")
    pack = args.pack.resolve(strict=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ko-prepare-", dir=output.parent) as tmp:
        staged = Path(tmp) / "runtime"
        source = staged / "source"
        staged.mkdir(mode=0o700)
        commit = export_source(args.strata.resolve(strict=True), source)
        try:
            apply_patches(source)
        except ValueError as exc:
            raise ValueError(f"{exc} (checkout {commit[:12]}; tested baseline {TESTED_UPSTREAM[:12]})") from exc
        payload, meta = compile_pack_table(source, pack)
        (staged / "utf8-policy.bin").write_bytes(payload)
        write_json(staged / "runtime.json", {"upstream": commit, "pack": str(pack), "tokenizer": meta})
        set_policy(staged, "off")
        staged.rename(output)
    if commit != TESTED_UPSTREAM:
        print(f"Strata {commit[:12]}: source patch applied. Build and run the smoke checks before use.", file=sys.stderr)
    print(output)


def prepared(runtime):
    meta = read_json(runtime / "runtime.json")
    commit = meta["upstream"]
    if not isinstance(commit, str) or not COMMIT_ID.fullmatch(commit) or meta["tokenizer"]["table_sha256"] != TABLE_SHA:
        raise ValueError("runtime commit or policy table fingerprint is invalid")
    return Path(meta["pack"])


def profile_with_engine(config, pack, engine, working_dir):
    if not isinstance(config, dict) or not isinstance(config.get("args"), list) or not config.get("exe"):
        raise ValueError("expected a Strata server JSON with exe and args")
    if not all(isinstance(value, str) for value in config["args"]):
        raise ValueError("Strata's engine args must be strings")
    options = [re.split(r"[=\s]", value, maxsplit=1)[0] for value in config["args"] if value.startswith("--")]
    incompatible = UNSUPPORTED.intersection(options)
    if incompatible or config.get("split_serve") or (config.get("replicas") or 1) != 1 or (config.get("parallel") or 1) != 1:
        raise ValueError(f"profile is outside the verified single-request engine path: {sorted(incompatible)}")
    if TABLE_ENV in (config.get("env") or {}):
        raise ValueError(f"remove {TABLE_ENV} from the engine profile before using the release launcher")
    native = config["args"]
    if options.count("--pack") != 1 or "--pack" not in native or native[-1] == "--pack":
        raise ValueError("expected one --pack followed by its path as separate args entries")
    cwd = (working_dir / (config.get("cwd") or ".")).resolve()
    if (cwd / native[native.index("--pack") + 1]).resolve() != pack:
        raise ValueError("the profile's engine pack must match prepare --pack")
    tokenizer = pack / "tokenizer"
    if config.get("tokenizer") and (working_dir / config["tokenizer"]).resolve() != tokenizer.resolve():
        raise ValueError("the controller tokenizer must be the same tokenizer as the engine pack")
    return {**config, "exe": str(engine), "tokenizer": str(tokenizer)}


def configure(args):
    pack = prepared(args.runtime)
    engine = args.runtime / "build/strata"
    if not engine.is_file() or not os.access(engine, os.X_OK):
        raise ValueError("build the strata target in RUNTIME/build first")
    working_dir = args.working_dir.resolve(strict=True)
    config = profile_with_engine(read_json(args.profile), pack, engine, working_dir)
    # Store only the paths here. The original JSON stays in place, including any API-key setting.
    write_json(args.runtime / "launch.json", {"profile": str(args.profile.resolve(strict=True)),
                                             "working_dir": str(working_dir), "port": args.port})
    print(f"Prepared launch on 127.0.0.1:{args.port} using {config['exe']}")


def serve(args):
    pack = prepared(args.runtime)
    table = args.runtime / "utf8-policy.bin"
    if digest(table) != TABLE_SHA:
        raise ValueError("the runtime policy table changed; prepare a fresh runtime")
    launch = read_json(args.runtime / "launch.json")
    cwd = Path(launch["working_dir"])
    config = profile_with_engine(read_json(Path(launch["profile"])), pack, args.runtime / "build/strata", cwd)
    # Keep the rewritten profile private and temporary even when it contains a key.
    with tempfile.TemporaryDirectory(prefix="qwen38-ko-profile-") as tmp:
        profile = Path(tmp) / "serving.json"
        write_json(profile, config)
        env = {**os.environ, STATE_ENV: str(args.runtime / "policy-state"), TABLE_ENV: str(table)}
        server = args.runtime / "source/serve/server.py"
        command = [sys.executable, str(server), "--engine", "strata", "--config", str(profile),
                   "--host", "127.0.0.1", "--port", str(launch["port"])]
        if args.offline:
            command.append("--offline")
        return subprocess.call(command, cwd=cwd, env=env)


def policy(args):
    prepared(args.runtime)
    if args.action != "status":
        set_policy(args.runtime, "auto" if args.action == "enable" else "off")
    print((args.runtime / "policy-state").read_text(encoding="ascii").strip())


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("prepare", "configure", "serve", "enable", "disable", "status"):
        command = commands.add_parser(name)
        command.add_argument("--runtime", type=Path, required=True)
    commands.choices["prepare"].add_argument("--strata", type=Path, required=True,
                                             help="Strata Git checkout to export and patch at its current HEAD")
    commands.choices["prepare"].add_argument("--pack", type=Path, required=True)
    commands.choices["serve"].add_argument("--offline", action="store_true",
                                           help="use Strata's local-only runtime mode")
    configure_parser = commands.choices["configure"]
    configure_parser.add_argument("--profile", type=Path, required=True)
    configure_parser.add_argument("--working-dir", type=Path, default=Path.cwd())
    configure_parser.add_argument("--port", type=int, default=18095)
    args = parser.parse_args()
    args.runtime = args.runtime.resolve()
    if args.action == "configure" and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


if __name__ == "__main__":
    args = parse_args()
    action = {"prepare": prepare, "configure": configure, "serve": serve}.get(args.action, policy)
    try:
        sys.exit(action(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except (ImportError, KeyError, OSError, ValueError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        sys.exit(f"qwen38-ko: {exc}")
