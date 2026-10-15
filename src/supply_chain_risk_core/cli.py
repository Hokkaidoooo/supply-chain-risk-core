"""研究流程命令行与可复核的结果目录。"""
import argparse
from importlib.metadata import version
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from ._console import configure_utf8_output
from .credit.workflow import WorkflowOptions
from .reporting import write_reports
from .reproduction import RECOMPUTE_POLICY, compare_recomputed_analysis
from .study import analyze_study
from .validation import canonical_bytes, exact_keys, file_digest, fingerprint, load_json

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = 1


def code_fingerprint():
    root = Path(__file__).resolve().parent
    return fingerprint({path.relative_to(root).as_posix(): file_digest(path)
                        for path in sorted(root.rglob("*.py"))})


def example_document():
    resource = files("supply_chain_risk_core").joinpath("data", "example.json")
    from .submission_contract import _json_object_without_duplicates
    return json.loads(resource.read_text(encoding="utf-8"), object_pairs_hook=_json_object_without_duplicates)


def _manifest(directory, report, options, office):
    return {
        "schema_version": MANIFEST_SCHEMA, "status": "complete", "package_version": version("supply-chain-risk-core"),
        "code_fingerprint": code_fingerprint(), "input_fingerprint": report["input_fingerprint"],
        "analysis_fingerprint": fingerprint(report), "options": options.__dict__, "office": office,
        "dependencies": {name: version(name) for name in ("numpy", "pandas", "networkx")},
        "files": [{"path": path.name, "sha256": file_digest(path), "bytes": path.stat().st_size}
                  for path in sorted(directory.iterdir()) if path.is_file()],
    }


def run_to_directory(document, output, *, options=WorkflowOptions(), office=True, exact_recompute=False):
    report = analyze_study(document, options)
    requested = Path(output).absolute()
    if requested.exists() or requested.is_symlink():
        raise FileExistsError(f"输出目录已存在，须选择新目录：{requested}")
    requested.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".risk-study-", dir=requested.parent) as temporary:
        stage = Path(temporary)
        (stage / "input.json").write_bytes(canonical_bytes(document) + b"\n")
        write_reports(report, stage, office=office)
        manifest = _manifest(stage, report, options, office)
        (stage / MANIFEST_NAME).write_bytes(canonical_bytes(manifest) + b"\n")
        staged_verification = verify_directory(stage, exact_recompute=exact_recompute)
        # mkdir 原子占用新名字；清单最后发布，中断目录不会被 verify 当作完成。
        requested.mkdir(exist_ok=False)
        for path in sorted(stage.iterdir(), key=lambda item: (item.name == MANIFEST_NAME, item.name)):
            with (requested / path.name).open("xb") as destination:
                destination.write(path.read_bytes())
    verification = verify_directory(requested, exact_recompute=exact_recompute)
    observations = {canonical_bytes(item): item for checked in (staged_verification, verification)
                    for item in checked["numerical_differences"]}
    return {**verification, "numerical_differences": list(observations.values()),
            "output": str(requested), "companies": len(report["credit"]["companies"]),
            "chains": len(report["chains"])}


def verify_directory(directory, *, exact_recompute=False):
    root = Path(directory).resolve(strict=True)
    manifest = load_json(root / MANIFEST_NAME)
    exact_keys(manifest, {"schema_version", "status", "package_version", "code_fingerprint", "input_fingerprint",
                          "analysis_fingerprint", "options", "office", "dependencies", "files"}, "manifest")
    if manifest["schema_version"] != MANIFEST_SCHEMA or manifest["status"] != "complete":
        raise ValueError("结果清单未完成或版本不支持")
    if manifest["code_fingerprint"] != code_fingerprint():
        raise ValueError("结果绑定的源码与当前安装包不同")
    names = set()
    for entry in manifest["files"]:
        exact_keys(entry, {"path", "sha256", "bytes"}, "manifest.files")
        name = entry["path"]
        if not isinstance(name, str) or name != PurePosixPath(name).name or "\\" in name or ":" in name or name in {".", "..", MANIFEST_NAME}:
            raise ValueError("清单成员必须是目录内的普通文件名")
        if name in names:
            raise ValueError("清单包含重复成员")
        names.add(name)
        path = root / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            raise ValueError(f"清单成员不存在或越界：{name}")
        if path.stat().st_size != entry["bytes"] or file_digest(path) != entry["sha256"]:
            raise ValueError(f"结果文件发生变化：{name}")
    if {path.name for path in root.iterdir()} != names | {MANIFEST_NAME}:
        raise ValueError("结果目录的实际成员与清单不同")
    if not {"input.json", "analysis.json", "report.md", "report.html"} <= names:
        raise ValueError("结果目录缺少必需文件")
    document = load_json(root / "input.json")
    if fingerprint(document) != manifest["input_fingerprint"]:
        raise ValueError("输入内容指纹不一致")
    stored = load_json(root / "analysis.json")
    if fingerprint(stored) != manifest["analysis_fingerprint"]:
        raise ValueError("保存的分析内容指纹不一致")
    report = analyze_study(document, WorkflowOptions(**manifest["options"]))
    differences = compare_recomputed_analysis(stored, report, exact_recompute=exact_recompute)
    return {"status": "verified", "files": len(names), "recomputed": True,
            "recompute_policy": "exact" if exact_recompute else RECOMPUTE_POLICY,
            "numerical_differences": differences}


def _options(args):
    return WorkflowOptions(review_on_missing=not args.no_review_on_missing,
                           include_policy_prior=args.include_policy_prior, advice_policy=args.advice_policy)


def main(argv=None):
    configure_utf8_output()
    parser = argparse.ArgumentParser(description="产业链风险、授信模型与阶段二报告")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "demo"):
        command = commands.add_parser(name)
        if name == "run":
            command.add_argument("--input", required=True, type=Path)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--without-office", action="store_true", help="只输出 JSON、Markdown、HTML 和 SVG")
        command.add_argument("--exact-recompute", action="store_true", help="复算逐位比较，关闭矩阵条件数的机器精度容差")
        command.add_argument("--no-review-on-missing", action="store_true", help="保留缺失清单，关闭缺失触发的复核标记")
        command.add_argument("--include-policy-prior", action="store_true", help="显式纳入历史行业政策先验")
        command.add_argument("--advice-policy", choices=("business_complete", "exact_match"), default="business_complete")
    example = commands.add_parser("example", help="导出可编辑的合成输入")
    example.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify", help="核对清单并真实重算")
    verify.add_argument("directory", type=Path)
    verify.add_argument("--exact-recompute", action="store_true", help="复算逐位比较，关闭矩阵条件数的机器精度容差")
    args = parser.parse_args(argv)
    if args.command == "example":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(canonical_bytes(example_document()) + b"\n")
        result = {"input": str(args.output), "data_kind": "synthetic_demonstration"}
    elif args.command == "verify":
        result = verify_directory(args.directory, exact_recompute=args.exact_recompute)
    else:
        document = example_document() if args.command == "demo" else load_json(args.input)
        result = run_to_directory(document, args.output, options=_options(args), office=not args.without_office,
                                  exact_recompute=args.exact_recompute)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
