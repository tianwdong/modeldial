#!/bin/zsh

set -u

kit_dir="${0:A:h}"
app_path="$kit_dir/modeldial-candidate.app"
app_executable="$app_path/Contents/MacOS/modeldial"
backend_root="$app_path/Contents/Resources/Backend"
data_dir="$HOME/Library/Application Support/modeldial"
timestamp="$(/bin/date '+%Y%m%d-%H%M%S')"
diagnostic_name="ModelDial-First-Run-Diagnostics-${USER}-${timestamp}"
output_dir="/Users/Shared/$diagnostic_name"

show_error() {
    /usr/bin/osascript -e "display alert \"ModelDial 测试日志\" message \"$1\" as critical" >/dev/null 2>&1
    print -r -- "$1"
}

copy_state() {
    local phase="$1"
    local phase_dir="$output_dir/$phase"
    /bin/mkdir -m 0755 "$phase_dir"

    local file_name
    for file_name in \
        config.json \
        history.jsonl \
        history.run_metadata.json \
        active_run.json \
        active_run.control.json
    do
        if [[ -f "$data_dir/$file_name" ]]; then
            /bin/cp "$data_dir/$file_name" "$phase_dir/$file_name"
        fi
    done

    if [[ -d "$data_dir/runs" ]]; then
        /usr/bin/ditto "$data_dir/runs" "$phase_dir/runs"
    fi
    if [[ -d "$data_dir/Logs" ]]; then
        /usr/bin/ditto "$data_dir/Logs" "$phase_dir/Logs"
    fi
}

if [[ ! -x "$app_executable" ]]; then
    show_error "测试 App 不完整，请重新解压整个 ZIP 后再试。"
    exit 1
fi

if /usr/bin/pgrep -u "$UID" -x modeldial >/dev/null 2>&1; then
    show_error "请先退出当前用户正在运行的所有 ModelDial，再重新双击本文件。"
    exit 1
fi

if ! /bin/mkdir -m 0755 "$output_dir"; then
    show_error "无法在 /Users/Shared 创建日志目录。"
    exit 1
fi

{
    print -r -- "started_at=$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
    print -r -- "user=$USER"
    print -r -- "macos=$(/usr/bin/sw_vers -productVersion)"
    print -r -- "architecture=$(/usr/bin/uname -m)"
    print -r -- "app_build=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$app_path/Contents/Info.plist")"
    print -r -- "reference_feed=$(/usr/libexec/PlistBuddy -c 'Print :ModelDialReferenceSnapshotURL' "$app_path/Contents/Info.plist")"
} > "$output_dir/system.txt"

copy_state before

print -r -- "ModelDial 测试日志已开启。"
print -r -- "请在 App 中随意测试快速扫描、全量扫描和结果来源切换。"
print -r -- "完成后请从 App 的电源菜单退出，日志 ZIP 将自动写入 /Users/Shared。"

app_status=0
/usr/bin/env MODELDIAL_DEBUG_LOG=1 "$app_executable" \
    > "$output_dir/app.stdout.log" \
    2> "$output_dir/app.stderr.log" || app_status=$?

copy_state after

if [[ -x "$backend_root/Runtime/modeldial-backend" && -f "$data_dir/config.json" ]]; then
    /usr/bin/env \
        MODELDIAL_BACKEND_ROOT="$backend_root" \
        MODELDIAL_DATA_DIR="$data_dir" \
        "$backend_root/Runtime/modeldial-backend" snapshot \
        --config-path "$data_dir/config.json" \
        --history-path "$data_dir/history.jsonl" \
        --active-run-path "$data_dir/active_run.json" \
        > "$output_dir/after/snapshot.json" \
        2> "$output_dir/after/snapshot.stderr.log" || true
fi

{
    print -r -- "finished_at=$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
    print -r -- "app_exit_status=$app_status"
    print -r -- "keychain_values_exported=false"
    print -r -- "raw_session_content_exported=false"
} > "$output_dir/result.txt"

/bin/chmod -R a+rX "$output_dir"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent \
    "$output_dir" "$output_dir.zip"
/bin/chmod 0644 "$output_dir.zip"

print -r -- "日志包已生成：$output_dir.zip"
/usr/bin/open -R "$output_dir.zip"
