#!/bin/bash

# 環境変数のチェック
if [ -z "${REPO_URL}" ] || [ -z "${RUNNER_TOKEN}" ]; then
  echo "エラー: REPO_URL と RUNNER_TOKEN を環境変数に指定してください。"
  exit 1
fi

# ランナーの設定
./config.sh --url "${REPO_URL}" --token "${RUNNER_TOKEN}" --name "docker-runner-$(hostname)" --unattended --replace

# コンテナ停止時にランナーの登録を解除する処理（トラップ）
cleanup() {
    echo "ランナーの登録を解除しています..."
    ./config.sh remove --token "${RUNNER_TOKEN}"
}
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

# ランナーの起動
./run.sh &
wait $!