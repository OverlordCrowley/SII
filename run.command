#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir/crypto-project-assistant"

if [ -x "$project_dir/.venv/bin/python" ]; then
    python_bin="$project_dir/.venv/bin/python"
else
    python_bin=python3
fi

"$python_bin" -m project_assistant "$@" || {
    status=$?
    printf '\nНе удалось запустить приложение. Проверьте сообщение выше.\nНажмите Enter, чтобы закрыть окно.'
    read -r answer || true
    exit "$status"
}
