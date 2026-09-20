#!/bin/bash
# data/ 备份：SQLite 一致性快照 + 配置/密钥/状态打包 + 保留轮转。
# 用法：./tools/backup.sh
# 环境变量：APP_DATA_DIR(源，默认 data/)、BACKUP_DIR(默认 backups/)、BACKUP_KEEP(份数，默认 14)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${APP_DATA_DIR:-$ROOT/data}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
KEEP="${BACKUP_KEEP:-14}"
TS="$(date +%Y%m%d_%H%M%S)"
STAGE="$BACKUP_DIR/.staging_$$"

mkdir -p "$BACKUP_DIR"
rm -rf "$STAGE"; mkdir -p "$STAGE"

# SQLite 一致性快照（VACUUM INTO，避免拷贝 WAL 中间态导致库不完整）
DB="$DATA_DIR/app.db"
if [ -f "$DB" ] && command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" "VACUUM INTO '$STAGE/app.db'" || { echo "DB 快照失败"; exit 1; }
elif [ -f "$DB" ]; then
  cp "$DB" "$STAGE/app.db"
fi

# 其余数据/配置/密钥/状态（存在才打包）
for f in documents.json config.json policy.json secret.key entities.json \
         oauth_state.json auth_users.json; do
  [ -f "$DATA_DIR/$f" ] && cp -p "$DATA_DIR/$f" "$STAGE/"
done

if [ -z "$(ls -A "$STAGE")" ]; then
  echo "无需备份（data/ 为空？）：$DATA_DIR"; rm -rf "$STAGE"; exit 1
fi

OUT="$BACKUP_DIR/kbai_data_$TS.tar.gz"
tar -C "$STAGE" -czf "$OUT" .
rm -rf "$STAGE"
chmod 600 "$OUT"

# 保留轮转：只留最近 $KEEP 份
ls -1t "$BACKUP_DIR"/kbai_data_*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
done

echo "备份完成: $OUT"
echo "内容: $(tar -tzf "$OUT" | tr '\n' ' ')"
echo "保留: 最近 $KEEP 份"