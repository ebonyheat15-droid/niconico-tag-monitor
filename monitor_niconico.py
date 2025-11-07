#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, time, argparse, logging
from pathlib import Path
from typing import Dict, Set, Tuple
import requests
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
DEFAULT_HEADERS = {"User-Agent": os.getenv("USER_AGENT", "Mozilla/5.0 (compatible; NicoTagMonitor/1.1)")}

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

def fetch_tags(video_id: str) -> Tuple[Set[str], Dict]:
    url = f"https://www.nicovideo.jp/watch/{video_id}"
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    tags: Set[str] = set()
    metadata: Dict = {"url": url, "title": None}

    title_el = soup.find("meta", property="og:title") or soup.find("title")
    if title_el:
        metadata["title"] = title_el.get("content") if hasattr(title_el, "get") and title_el.get("content") else title_el.text.strip()

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

    if not tags:
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and meta_kw.get("content"):
            tags.update([t.strip() for t in meta_kw.get("content").split(",") if t.strip()])

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

def format_diff_message(video_id: str, meta: Dict, removed: Set[str], now_tags: Set[str]) -> str:
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

def main():
    args = parse_args()
    videos_env = os.getenv("VIDEOS")
    videos_arg = args.videos
    if videos_arg:
        video_ids = [v.strip() for v in videos_arg.split(",") if v.strip()]
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
            prev = set(state.get(vid, {}).get("tags", []))
            removed = prev - now_tags if prev else set()

            state[vid] = {"tags": sorted(list(now_tags)), "title": meta.get("title"), "last_checked": int(time.time())}
            save_state(state_path, state)

            if removed:
                msg = format_diff_message(vid, meta, removed, now_tags)
                sent = False
                sent = notify_discord(msg) or sent
                sent = notify_teams(msg) or sent
                if not sent:
                    logging.warning("通知先が未設定か、送信に失敗しました。（Discord/Teamsを確認）")
            else:
                logging.info("タグ削除なし: %s", vid)

        except Exception:
            logging.exception("チェック失敗: %s", vid)
            exit_code = 2

    return exit_code

if __name__ == "__main__":
    raise SystemExit(main())
