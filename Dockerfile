FROM ubuntu:22.04

# 必要最低限のパッケージと、GitHub Actionsランナーが依存するライブラリをインストール
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
  curl \
  git \
  jq \
  sudo \
  libicu-dev \
  build-essential \
  && rm -rf /var/lib/apt/lists/*

# ランナー用の一般ユーザーを作成（rootでの実行は非推奨のため）
RUN useradd -m runner && usermod -aG sudo runner && echo "%sudo ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
USER runner
WORKDIR /home/runner

# GitHub Actionsランナーのダウンロード（バージョンは適宜最新にしてください）
ARG RUNNER_VERSION="2.316.1"
RUN curl -o actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz -L \
  https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz \
  && tar xzf ./actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz \
  && rm actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz

# 起動スクリプト（entrypoint.sh）をコピー
COPY --chown=runner:runner entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]