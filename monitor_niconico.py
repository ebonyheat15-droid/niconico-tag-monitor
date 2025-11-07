#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NicoNico Tag Monitor (Discord-first + 必須タグチェック)
- 指定動画のタグを取得し、
  1) 前回あって今回ないタグ（削除）を検知
  2) 必須タグ(REQUIRED_TAGS)が一つでも無ければ検知
- Discord Webhook（推奨）と/または Microsoft Teams に通知
- GitHub Actions/cron からの30分おき実行を想定

環境変数（Secrets 推奨）:
  DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/....."   (推奨)
  TEAMS_WEBHOOK_URL="https://..."                                 (任意)
  VIDEOS="sm9,sm12345678"                                         (監視動画ID カンマ区切り)
  REQUIRED_TAGS="タグA,タグB"                                     (必須タグ名 カンマ区切り)
  USER_AGENT="..."                                                (任意)
"""
import os
import json
import time
import argparse
import logging
from pathlib import Path
from typing import Dict, Set, Tuple, List

import requests
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_HEADERS = {"User-Agent": os.getenv("USER_AGENT", "Mozilla/5.0 (compatible; NicoTagMonitor/1.2)")}

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", help="Comma-separated video IDs (e.g., sm9,sm12345). Defaults to VIDEOS env.")
    ap.add_argument("--state", default="state.json", help="Path to persistent state JSON file.")
    return ap.parse_args()

def load_state(path: Path) -> Dict[str, Dict]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}

def save_state(path: Path, state: Dict):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def parse_required_tags() -> Set[str]:
    """REQUIRED_TAGS をカンマ区切りで読み取り、前後空白を除去した集合を返す。空なら空集合。"""
    raw = os.getenv("REQUIRED_TAGS", "")
    if not raw.strip():
        return set()
    return {t.strip() for t in raw.split(",") if t.strip()}

def fetch_tags(video_id: str) -> Tuple[Set[str], Dict]:
    """
    現在のタグ集合とメタ情報を返す。
    複数の手段でタグ抽出（JSON-LD / meta keywords / 画面上の候補）
    """
    url = f"https://www.nicovideo.jp/watch/{video_id}"
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tags: Set[str] = set()
    metadata: Dict = {"url": url, "title": None}

    # タイトル
    title_el = soup.find("meta", property="og:title") or soup.find("title")
    if title_el:
        metadata["title"] = title_el.get("content") if hasattr(title_el, "get") and title_el.get("content") else title_el.text.strip()

    # Strategy 1: JSON-LD keywords
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
            if isinstance(data, dict) and "keywords" in data:
                kw = data["keywords"]
                if isinstance(kw, list):
                    tags.update([str(x).strip() for x in kw if str(x).strip()])
                elif isinstance(kw, str):
                    tags.update([t.strip() for t in kw.split(",") if t.strip()])
        except Exception:
            pass

    # Strategy 2: meta keywords
    if not tags:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and meta_kw.get("content"):
            tags.update([t.strip() for t in meta_kw.get("content").split(",") if t.strip()])

    # Strategy 3: visible tag links (fallback)
    if not tags:
        candidates = soup.select('a[data-tag], li a[href*="/tag/"], span.TagContainer-tag')
        for a in candidates:
            txt = (a.get("data-tag") or a.get_text() or "").strip()
            if txt:
                tags.add(txt)

    tags = {t.strip() for t in tags if t.strip()}
    return tags, metadata

def notify_discord(message: str) -> bool:
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    try:
        r = requests.post(url, json={"content": message}, timeout=15)
        return 200 <= r.status_code < 300
    except Exception:
        return False

def notify_teams(message: str) -> bool:
    url = os.getenv("TEAMS_WEBHOOK_URL")
    if not url:
        return False
    try:
        r = requests.post(url, json={"text": message}, timeout=15)
        return 200 <= r.status_code < 300
    except Exception:
        return False

def format_deleted_message(video_id: str, meta: Dict, removed: Set[str], now_tags: Set[str]) -> str:
    title = meta.get("title") or video_id
    url = meta.get("url")
    lines = [
        f"【タグ削除検知】{title} ({video_id})",
        f"{url}",
        "消されたタグ:",
        "・" + " ・".join(sorted(removed)) if removed else "（なし）",
        "",
        "現在のタグ:",
        "・" + " ・".join(sorted(now_tags)) if now_tags else "（なし）",
        "",
        f"検知時刻: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
    ]
    return "\n".join(lines)

def format_missing_required_message(video_id: str, meta: Dict, missing: Set[str], now_tags: Set[str]) -> str:
    title = meta.get("title") or video_id
    url = meta.get("url")
    lines = [
        f"【必須タグ欠落】{title} ({video_id})",
        f"{url}",
        "不足している必須タグ:",
        "・" + " ・".join(sorted(missing)),
        "",
        "現在のタグ:",
        "・" + " ・".join(sorted(now_tags)) if now_tags else "（なし）",
        "",
        f"検知時刻: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
    ]
    return "\n".join(lines)

def main():
    args = parse_args()
    videos_env = os.getenv("VIDEOS")
    required = parse_required_tags()

    # 監視対象
    if args.videos:
        video_ids = [v.strip() for v in args.videos.split(",") if v.strip()]
    elif videos_env:
        video_ids = [v.strip() for v in videos_env.split(",") if v.strip()]
    else:
        logging.error("動画IDが指定されていません。--videos か VIDEOS env を設定してください。")
        return 2

    state_path = Path(args.state)
    state = load_state(state_path)

    exit_code = 0
    for vid in video_ids:
        try:
            now_tags, meta = fetch_tags(vid)

            # --- 削除検知（従来機能、必要なければコメントアウト） ---
            prev = set(state.get(vid, {}).get("tags", []))
            removed = prev - now_tags if prev else set()
            if removed:
                msg = format_deleted_message(vid, meta, removed, now_tags)
                sent = notify_discord(msg) or notify_teams(msg)
                if not sent:
                    logging.warning("削除通知の送信に失敗（Discord/Teamsの設定を確認）")

            # --- 必須タグ検知（新機能） ---
            missing_required = set()
            if required:
                # 大文字小文字は区別しない運用にしたい場合は .lower() を使って正規化
                # ここでは「表記そのまま一致」運用（日本語タグの想定）
                missing_required = required - now_tags

                # 同じ「欠落」メッセージを毎回送らないよう、直前状態と比較して変化時だけ通知
                prev_missing = set(state.get(vid, {}).get("last_missing_required", []))
                if missing_required != prev_missing:
                    if missing_required:
                        msg = format_missing_required_message(vid, meta, missing_required, now_tags)
                        sent = notify_discord(msg) or notify_teams(msg)
                        if not sent:
                            logging.warning("必須タグ欠落の通知送信に失敗（Discord/Teamsの設定を確認）")
                    else:
                        # 復帰（全部そろった）時も知らせたい場合はここで通知を送ってもよい
                        logging.info("必須タグが復帰: %s", vid)

                # 状態を保存
                state.setdefault(vid, {})["last_missing_required"] = sorted(list(missing_required))

            # 最後に最新タグを保存（削除検知に使う）
            state.setdefault(vid, {})
            state[vid]["tags"] = sorted(list(now_tags))
            state[vid]["title"] = meta.get("title")
            state[vid]["last_checked"] = int(time.time())
            save_state(state_path, state)

            if not removed and not missing_required:
                logging.info("異常なし: %s", vid)

        except Exception:
            logging.exception("チェック失敗: %s", vid)
            exit_code = 2

    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())
