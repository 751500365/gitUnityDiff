#!/usr/bin/env bash
# Double-click in Finder: runs the diff in Terminal.app with default range.
# First run: right-click -> Open (macOS may block unsigned scripts), or: chmod +x
#A：新增
#M：修改
#D：删除
#R：重命名
#C：复制
#T：类型变更
#U：未合并  
#./quick_git_unity_assets_diff.sh 2026-01-01 2026-04-15
#./quick_git_unity_assets_diff.sh 2026-01-01 2026-04-15 using/develop
#--csv ~/Desktop/unity_assets_diff.csv

cd "$(dirname "$0")"
exec ./quick_git_unity_assets_diff.sh "$@"
