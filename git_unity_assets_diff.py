#!/usr/bin/env python3
"""
Compare Unity project asset paths between two git refs (summary + per-extension stats).

Usage (from git repo root):
  python3 01_Project/Tools/GitUnityAssetsDiff/git_unity_assets_diff.py <older_ref> <newer_ref>
  python3 .../git_unity_assets_diff.py --since-date 2024-01-01 --until-date 2024-01-31 --date-branch main
  python3 .../git_unity_assets_diff.py --since-date 2025-03-01 --until-date 2025-03-15 --non-code

Notes:
  - Interprets git diff as: changes to transform tree at older_ref into newer_ref (same as `git diff older_ref newer_ref`).
  - Line deltas (--numstat) are meaningful for text/YAML; binary files show as "-" placeholders.
  - *.meta paths are excluded by default; pass --include-meta to list them.
  - By default writes CSV to Desktop (auto filename); --csv PATH overrides; --no-csv skips. UTF-8 BOM for Excel.
  - Compares filtered paths to git diff --name-only and to numstat; stderr WARNING on mismatch. --list-all prints full paths in terminal.
  - Git "A" = path missing in older tree (includes new path after rename when using --no-renames); not strictly "brand-new asset".
  - Use --non-code to ignore script/shader-like files (.cs, .shader, .compute, etc., including *.cs.meta).
  - Per-file disk column: Git blob size at newer_ref for A/M, at older_ref for D (object DB bytes; LFS may be pointer size).
  - Date mode: base tree = last commit before --since-date; target tree = last commit before day after --until-date (end inclusive).
    Git --before=... uses committer date (see gitrevisions(7)); use ISO datetime with offset if you need timezone control.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath

# Default CSV: write under ~/Desktop (or ~/桌面) with timestamp unless --no-csv or --csv PATH.
_DESKTOP_CSV_PREFIX = "GitUnityAssetsDiff"


def run_git(repo: str, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        sys.stderr.write(p.stderr or p.stdout or "git failed\n")
        sys.exit(p.returncode)
    return p.stdout


def find_repo(start: str | None) -> str:
    candidates: list[str] = []
    if start:
        candidates.append(start)
    candidates.append(os.getcwd())
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        candidates.append(here)
        here = os.path.dirname(here)
    seen: set[str] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        p = subprocess.run(
            ["git", "-C", c, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if p.returncode == 0:
            return p.stdout.strip()
    sys.stderr.write("Not inside a git repository. Pass --repo <path>.\n")
    sys.exit(1)


def desktop_dir() -> Path:
    home = Path.home()
    for name in ("Desktop", "桌面"):
        d = home / name
        if d.is_dir():
            return d
    return home


def default_desktop_csv_path() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(desktop_dir() / f"{_DESKTOP_CSV_PREFIX}_{stamp}.csv")


def normalize_roots(repo: str, roots: list[str]) -> list[str]:
    out: list[str] = []
    for r in roots:
        rp = r.replace("\\", "/").strip("/")
        if not rp:
            continue
        full = os.path.join(repo, rp)
        if not os.path.isdir(full):
            sys.stderr.write(f"Warning: path not found in repo, skipping: {rp}\n")
            continue
        out.append(rp)
    return out


def ext_key(path: str) -> str:
    p = PurePosixPath(path)
    suf = "".join(p.suffixes)
    if not suf:
        return "(no extension)"
    return suf.lower()


def _parse_numstat_field(s: str) -> int | None:
    if s == "-":
        return None
    return int(s)


# Paths are Unity repo paths (forward slashes). For *.meta, the "asset" path is without trailing .meta.
_CODE_LIKE_SUFFIXES: tuple[str, ...] = (
    ".cs",
    ".shader",
    ".compute",
    ".cginc",
    ".hlsl",
    ".glsl",
    ".glslinc",
    ".raytrace",
    ".uss",  # UI Toolkit stylesheet
)


def _path_without_meta(path: str) -> str:
    p = path.replace("\\", "/")
    if p.lower().endswith(".meta"):
        return p[:-5]
    return p


def is_code_like_path(path: str) -> bool:
    base = _path_without_meta(path).lower()
    return any(base.endswith(s) for s in _CODE_LIKE_SUFFIXES)


def keep_path_for_report(path: str, include_meta: bool, non_code: bool) -> bool:
    if (not include_meta) and path.endswith(".meta"):
        return False
    if non_code and is_code_like_path(path):
        return False
    return True


def git_diff_filtered_paths(
    repo: str,
    older_ref: str,
    newer_ref: str,
    path_args: list[str],
    include_meta: bool,
    non_code: bool,
) -> list[str]:
    raw = run_git(repo, "diff", "--name-only", older_ref, newer_ref, *path_args)
    out: list[str] = []
    for line in raw.splitlines():
        p = line.strip().replace("\\", "/")
        if not p:
            continue
        if not keep_path_for_report(p, include_meta, non_code):
            continue
        out.append(p)
    return out


# Git name-status first column; --no-renames hides R/C in most cases.
_STATUS_LABEL_ZH: dict[str, str] = {
    "A": "新增",
    "M": "修改",
    "D": "删除",
    "R": "重命名",
    "C": "复制",
    "T": "类型变更",
    "U": "未合并",
}


def status_zh(code: str) -> str:
    return _STATUS_LABEL_ZH.get(code, code)


def git_status_letter(raw: str) -> str:
    """Normalize git diff --name-status first column to one letter (e.g. R094 -> R, A -> A)."""
    s = raw.strip()
    if not s:
        return "?"
    return s[0]


def sort_status_keys(keys: list[str]) -> list[str]:
    order = {"A": 0, "M": 1, "D": 2, "R": 3, "C": 4, "T": 5, "U": 6}
    return sorted(keys, key=lambda x: (order.get(x, 99), x))


def _status_sort_rank(code: str) -> int:
    order = {"A": 0, "M": 1, "D": 2, "R": 3, "C": 4, "T": 5, "U": 6}
    return order.get(code, 99)


def format_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    if n < 1024:
        return f"{n} B"
    x = float(n)
    for u in ("KB", "MB", "GB", "TB"):
        x /= 1024.0
        if x < 1024.0 or u == "TB":
            return f"{x:.1f} {u}"
    return f"{n} B"


def parse_ls_tree_l_line(line: str) -> tuple[str, int] | None:
    line = line.rstrip("\n")
    if "\t" not in line:
        return None
    meta, path = line.split("\t", 1)
    path = path.strip().replace("\\", "/")
    parts = meta.split()
    if len(parts) < 4 or parts[1] != "blob":
        return None
    try:
        size = int(parts[3])
    except ValueError:
        return None
    return path, size


def blob_sizes_under_roots(repo: str, ref: str, roots: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    if not roots:
        return out
    raw = run_git(repo, "ls-tree", "-r", "-l", ref, "--", *roots)
    for line in raw.splitlines():
        if not line.strip():
            continue
        got = parse_ls_tree_l_line(line)
        if got is None:
            continue
        p, sz = got
        out[p] = sz
    return out


def resolve_blob_size(
    status: str,
    path: str,
    sizes_old: dict[str, int],
    sizes_new: dict[str, int],
) -> int | None:
    if status == "D":
        return sizes_old.get(path)
    return sizes_new.get(path)


def rev_list_last_before(repo: str, branch: str, before_spec: str) -> str:
    p = subprocess.run(
        ["git", "-C", repo, "rev-list", "-1", f"--before={before_spec}", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0:
        sys.stderr.write(p.stderr or "git rev-list failed\n")
        sys.exit(p.returncode)
    sha = p.stdout.strip()
    if not sha:
        sys.stderr.write(
            f"No commit found with commit time before '{before_spec}' on '{branch}'.\n"
        )
        sys.exit(1)
    return sha


def write_diff_csv(
    out_path: str,
    files_status: list[tuple[str, str]],
    line_info: dict[str, tuple[int | None, int | None]],
    sizes_old: dict[str, int],
    sizes_new: dict[str, int],
    older_ref: str,
    newer_ref: str,
    date_range_label: str | None,
) -> None:
    rows = sorted(files_status, key=lambda x: (_status_sort_rank(x[0]), x[1]))
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "git_status",
                "status_zh",
                "extension",
                "path",
                "lines_added",
                "lines_deleted",
                "disk_bytes",
                "disk_human",
                "older_ref",
                "newer_ref",
                "date_range",
            ]
        )
        for status, path in rows:
            a, d = line_info.get(path, (None, None))
            sz = resolve_blob_size(status, path, sizes_old, sizes_new)
            w.writerow(
                [
                    status,
                    status_zh(status),
                    ext_key(path),
                    path,
                    "" if a is None else a,
                    "" if d is None else d,
                    "" if sz is None else sz,
                    format_bytes(sz),
                    older_ref,
                    newer_ref,
                    "" if date_range_label is None else date_range_label,
                ]
            )


def commit_summary_line(repo: str, rev: str) -> str:
    p = subprocess.run(
        ["git", "-C", repo, "log", "-1", "--format=%h %ci %s", rev],
        capture_output=True,
        text=True,
        check=False,
    )
    if p.returncode != 0 or not p.stdout.strip():
        return rev
    return p.stdout.strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Unity asset tree diff summary between two git refs.")
    ap.add_argument(
        "older_ref",
        nargs="?",
        default=None,
        help="Older commit/branch/tag (base); omit when using --since-date/--until-date",
    )
    ap.add_argument(
        "newer_ref",
        nargs="?",
        default=None,
        help="Newer commit/branch/tag (target); omit when using --since-date/--until-date",
    )
    ap.add_argument(
        "--repo",
        default=None,
        help="Git repository root (default: auto-detect from cwd or script location)",
    )
    ap.add_argument(
        "--roots",
        nargs="+",
        default=["01_Project/Assets"],
        help="Path prefixes under repo to include (posix-style, relative to repo root)",
    )
    ap.add_argument(
        "--include-meta",
        action="store_true",
        help="Include *.meta paths (default: excluded from all summaries)",
    )
    ap.add_argument(
        "--no-meta",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    ap.add_argument(
        "--non-code",
        action="store_true",
        dest="non_code",
        help="Exclude code-like files: .cs, .shader, .compute, .cginc, .hlsl, .uss, etc. (and their .meta)",
    )
    ap.add_argument(
        "--list-limit",
        type=int,
        default=40,
        help="Max paths to print per status bucket (0 = no terminal lists; CSV still has all)",
    )
    ap.add_argument(
        "--list-all",
        action="store_true",
        help="Print every changed file path in terminal (ignores --list-limit)",
    )
    ap.add_argument(
        "--csv",
        metavar="PATH",
        default=None,
        help="Write CSV to this path instead of Desktop default (UTF-8 BOM; all rows, not list-limited)",
    )
    ap.add_argument(
        "--no-csv",
        action="store_true",
        help="Do not write CSV (default is Desktop auto-named file)",
    )
    ap.add_argument(
        "--since-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Calendar range start (inclusive). With --until-date, resolves commits via git rev-list --before.",
    )
    ap.add_argument(
        "--until-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Calendar range end (inclusive). Target snapshot = last commit before the next calendar day.",
    )
    ap.add_argument(
        "--date-branch",
        default="HEAD",
        help="Branch or ref to walk with rev-list in date mode (default: HEAD)",
    )
    args = ap.parse_args()

    if args.no_csv and args.csv is not None:
        ap.error("Use either --no-csv or --csv PATH, not both.")

    if args.no_csv:
        csv_out: str | None = None
    elif args.csv is not None:
        csv_out = args.csv
    else:
        csv_out = default_desktop_csv_path()

    repo = find_repo(args.repo)

    date_mode = args.since_date is not None or args.until_date is not None
    if date_mode:
        if not args.since_date or not args.until_date:
            ap.error("Date mode requires both --since-date and --until-date (YYYY-MM-DD).")
        if args.older_ref is not None or args.newer_ref is not None:
            ap.error("Do not pass older_ref/newer_ref positionals together with --since-date/--until-date.")
        try:
            d0 = date.fromisoformat(args.since_date.strip())
            d1 = date.fromisoformat(args.until_date.strip())
        except ValueError as e:
            ap.error(f"Invalid date, use YYYY-MM-DD: {e}")
        if d1 < d0:
            ap.error("--until-date must be on or after --since-date.")
        branch = args.date_branch.strip() or "HEAD"
        older_ref = rev_list_last_before(repo, branch, args.since_date.strip())
        until_next = (d1 + timedelta(days=1)).isoformat()
        newer_ref = rev_list_last_before(repo, branch, until_next)
        date_range_label = f"{args.since_date.strip()} .. {args.until_date.strip()}"
    else:
        if not args.older_ref or not args.newer_ref:
            ap.error("Provide older_ref and newer_ref, or use --since-date and --until-date.")
        older_ref = args.older_ref
        newer_ref = args.newer_ref
        date_range_label = None

    roots = normalize_roots(repo, list(args.roots))
    if not roots:
        sys.stderr.write("No valid --roots paths.\n")
        sys.exit(1)

    sizes_old = blob_sizes_under_roots(repo, older_ref, roots)
    sizes_new = blob_sizes_under_roots(repo, newer_ref, roots)

    path_args = ["--", *roots]
    name_status = run_git(
        repo,
        "diff",
        "--name-status",
        "--no-renames",
        older_ref,
        newer_ref,
        *path_args,
    )

    numstat = run_git(
        repo,
        "diff",
        "--numstat",
        older_ref,
        newer_ref,
        *path_args,
    )

    # Parse name-status: with --no-renames, each line is STATUS\tPATH (two columns).
    files_status: list[tuple[str, str]] = []
    name_status_malformed = 0
    name_status_extra_tab = 0
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            name_status_malformed += 1
            continue
        raw_status = parts[0].strip()
        status = git_status_letter(raw_status)
        path = parts[1].strip().replace("\\", "/")
        if "\t" in path:
            name_status_extra_tab += 1
        if not keep_path_for_report(path, args.include_meta, args.non_code):
            continue
        files_status.append((status, path))

    # numstat: added\tdel\tpath (path may contain tabs; join remaining fields)
    line_info: dict[str, tuple[int | None, int | None]] = {}
    numstat_malformed = 0
    for line in numstat.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            numstat_malformed += 1
            continue
        add_s, del_s = parts[0], parts[1]
        path = "\t".join(parts[2:]).strip().replace("\\", "/")
        if not keep_path_for_report(path, args.include_meta, args.non_code):
            continue
        line_info[path] = (_parse_numstat_field(add_s), _parse_numstat_field(del_s))

    # Cross-check: same filtered path multiset as `git diff --name-only` (authoritative list).
    name_only_filtered = git_diff_filtered_paths(
        repo, older_ref, newer_ref, path_args, args.include_meta, args.non_code
    )
    cnt_status = Counter(p for _, p in files_status)
    cnt_only = Counter(name_only_filtered)
    if cnt_status != cnt_only:
        only_s = set(cnt_only.elements()) - set(cnt_status.elements())
        only_ns = set(cnt_status.elements()) - set(cnt_only.elements())
        sys.stderr.write(
            "WARNING: filtered path set mismatch between --name-status and --name-only.\n"
        )
        if only_s:
            sample = sorted(only_s)[:20]
            more = f" (+{len(only_s) - 20} more)" if len(only_s) > 20 else ""
            sys.stderr.write(f"  Only in name-only ({len(only_s)}): {sample!r}{more}\n")
        if only_ns:
            sample = sorted(only_ns)[:20]
            more = f" (+{len(only_ns) - 20} more)" if len(only_ns) > 20 else ""
            sys.stderr.write(f"  Only in name-status ({len(only_ns)}): {sample!r}{more}\n")

    paths_from_status = {p for _, p in files_status}
    paths_from_num = set(line_info.keys())
    if paths_from_status != paths_from_num:
        miss_num = paths_from_status - paths_from_num
        extra_num = paths_from_num - paths_from_status
        if miss_num or extra_num:
            sys.stderr.write(
                "WARNING: path set mismatch between --name-status and --numstat "
                f"(missing numstat: {len(miss_num)}, extra numstat: {len(extra_num)}).\n"
            )

    if name_status_malformed:
        sys.stderr.write(
            f"WARNING: skipped {name_status_malformed} non-tabular name-status line(s).\n"
        )
    if name_status_extra_tab:
        sys.stderr.write(
            f"WARNING: {name_status_extra_tab} name-status path field contains TAB "
            "(split may be wrong; avoid tab characters in file names).\n"
        )
    if numstat_malformed:
        sys.stderr.write(f"WARNING: skipped {numstat_malformed} malformed numstat line(s).\n")

    status_counts: dict[str, int] = defaultdict(int)
    status_bytes: dict[str, int] = defaultdict(int)
    ext_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ext_bytes: dict[str, int] = defaultdict(int)
    ext_bytes_added: dict[str, int] = defaultdict(int)
    buckets: dict[str, list[str]] = defaultdict(list)

    for status, path in files_status:
        status_counts[status] += 1
        ek = ext_key(path)
        ext_counts[ek][status] += 1
        buckets[status].append(path)
        sz = resolve_blob_size(status, path, sizes_old, sizes_new)
        if sz is not None:
            status_bytes[status] += sz
            ext_bytes[ek] += sz
            if status == "A":
                ext_bytes_added[ek] += sz

    total_add_lines = 0
    total_del_lines = 0
    binary_or_mixed = 0
    for path, (a, d) in line_info.items():
        if a is None or d is None:
            binary_or_mixed += 1
            continue
        total_add_lines += a
        total_del_lines += d

    print(f"Repo: {repo}")
    if date_range_label is not None:
        print(f"Date range: {date_range_label}  (rev-list on {args.date_branch})")
        print(f"  older: {commit_summary_line(repo, older_ref)}")
        print(f"  newer: {commit_summary_line(repo, newer_ref)}")
    print(f"Diff: {older_ref} .. {newer_ref}")
    print(f"Roots: {', '.join(roots)}")
    if args.include_meta:
        print("Filter: including *.meta")
    if args.non_code:
        print(f"Filter: non-code only (excludes: {', '.join(_CODE_LIKE_SUFFIXES)})")
    print("Disk: Git blob size (A/M = newer ref, D = older ref).")
    print("By extension: disk~ = all changed files; 新增disk~ = added (A) only at newer ref.")
    print(
        "Note: Git 标记「A」= 在旧快照(older)中不存在该路径；"
        "重命名/移动在 --no-renames 下会表现为「旧路径 D + 新路径 A」，新路径会计入「新增」而非「重命名」。"
    )
    print()
    print(f"Files changed (under roots): {len(files_status)}")
    for k in sort_status_keys(list(status_counts.keys())):
        sb = status_bytes.get(k, 0)
        print(f"  [{status_zh(k)}] {k}: {status_counts[k]}  (disk {format_bytes(sb)})")
    print()
    print("Line stats (text/YAML only; '-' in numstat counts as binary/non-text for that file):")
    print(f"  Sum +lines: {total_add_lines}")
    print(f"  Sum -lines: {total_del_lines}")
    print(f"  Files with no line stats (binary or empty diff): {binary_or_mixed}")
    print()

    # Per-extension table
    print("By extension (file counts):")
    for ek in sorted(ext_counts.keys(), key=lambda x: (-sum(ext_counts[x].values()), x)):
        row = ext_counts[ek]
        parts = [f"{status_zh(s)}({s}):{row[s]}" for s in sort_status_keys(list(row.keys()))]
        eb = ext_bytes.get(ek, 0)
        eba = ext_bytes_added.get(ek, 0)
        print(
            f"  {ek:24}  total={sum(row.values()):5}  disk~{format_bytes(eb)}  "
            f"新增disk~{format_bytes(eba)}  "
            + "  ".join(parts)
        )
    print()

    limit = max(0, args.list_limit)
    if args.list_all:
        limit = 10**9
    if limit > 0 and files_status:
        for status in sort_status_keys(list(buckets.keys())):
            paths = sorted(buckets[status])[:limit]
            tag = status_zh(status)
            n_show = min(len(buckets[status]), limit)
            n_all = len(buckets[status])
            print(f"[{tag}] {status}  ({n_show} of {n_all} files):")
            for p in paths:
                a, d = line_info.get(p, (None, None))
                sz = resolve_blob_size(status, p, sizes_old, sizes_new)
                szs = format_bytes(sz)
                if a is not None and d is not None:
                    print(f"  [{tag}] {p}  (+{a} -{d})  disk {szs}")
                else:
                    print(f"  [{tag}] {p}  disk {szs}")
            print()

    if len(files_status) > 0:
        if args.list_all:
            print("Note: printed full terminal list (--list-all).")
        elif args.list_limit == 0:
            if csv_out:
                print("Note: terminal file lists skipped (--list-limit 0); CSV has the full list.")
            else:
                print(
                    "Note: terminal file lists skipped (--list-limit 0); "
                    "use --list-all or default CSV output to see every path."
                )
        elif not args.list_all and any(len(buckets[s]) > args.list_limit for s in buckets):
            if csv_out:
                print(
                    f"Note: terminal lists capped at --list-limit {args.list_limit} per status; "
                    f"CSV has all {len(files_status)} path(s)."
                )
            else:
                print(
                    f"Note: terminal lists capped at --list-limit {args.list_limit} per status; "
                    "use --list-all for full paths in terminal."
                )

    if csv_out:
        try:
            write_diff_csv(
                csv_out,
                files_status,
                line_info,
                sizes_old,
                sizes_new,
                older_ref,
                newer_ref,
                date_range_label,
            )
        except OSError as e:
            sys.stderr.write(f"Cannot write CSV {csv_out!r}: {e}\n")
            sys.exit(1)
        print(f"CSV written: {csv_out}  ({len(files_status)} rows)")


if __name__ == "__main__":
    main()
