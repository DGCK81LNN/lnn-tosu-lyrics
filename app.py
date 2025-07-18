import asyncio
import hashlib
import json
import os
import re
import sys
import traceback
import websockets

json_encoder = json.JSONEncoder(
    ensure_ascii=False,
    check_circular=False,
    allow_nan=False,
    separators=(",", ":"),
)
songs_path = r"D:\Program Files (x86)\osu!\Songs"

sha256_cache = {}
def get_mp3_sha256(mp3):
    if re.match(r"^[0-9a-f]{64}$", mp3):
        return mp3
    if mp3 in sha256_cache:
        return sha256_cache[mp3]
    mp3_path = os.path.join(songs_path, mp3)
    if not os.path.exists(mp3_path):
        return os.path.basename(mp3)
    with open(mp3_path, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()
    sha256_cache[mp3] = sha256
    return sha256

mtime_dict = {}
full_lyrics_cache = {}
credits_cache = {}
def get_full_lyrics(mp3_sha256, data={}):
    file_present = os.path.isfile(f"lyrics/{mp3_sha256}.lrc") and os.path.getsize(f"lyrics/{mp3_sha256}.lrc") > 0
    if file_present:
        mtime = os.path.getmtime(f"lyrics/{mp3_sha256}.lrc")
        if mp3_sha256 in mtime_dict and mp3_sha256 in full_lyrics_cache and mtime_dict[mp3_sha256] != mtime:
            print(f"reload {mp3_sha256}")
            del full_lyrics_cache[mp3_sha256]
        mtime_dict[mp3_sha256] = mtime
    if mp3_sha256 in full_lyrics_cache:
        return full_lyrics_cache[mp3_sha256]
    if not file_present:
        return None
    l = []
    offset = 0
    time_scale = 1
    has_artist = False
    has_title = False
    crlf = False
    credits = []
    betamapsets = set()
    with open(f"lyrics/{mp3_sha256}.lrc", "r", encoding="utf-8") as f:
        for line in f:
            if match := re.match(r"^\[(\d\d):(\d\d)(?:\.(\d{1,3}))?\]", line):
                ms = match.group(3).ljust(3, "0")
                time = int(match.group(1)) * 60_000 + int(match.group(2)) * 1000 + int(ms)
                l.append(((time - offset) / time_scale, line[match.end():].strip()))
            elif match := re.match(r"^\[offset:(-?[1-9]\d*)\]", line):
                offset = int(match.group(1))
            elif match := re.match(r"^\[time_scale:([1-9]\d*(?:\.\d+)?)\]", line):
                time_scale = float(match.group(1))
            elif match := re.match(r"^\[mapsetid:([1-9]\d*)\]", line):
                betamapsets.add(int(match.group(1)))
            elif match := re.match(r"^\[by:(.*)\]", line):
                credits.append(f"歌词制作：{match.group(1)}")
            elif match := re.match(r"^\[tr:(.*)\]", line):
                credits.append(f"翻译：{match.group(1)}")
            elif match := re.match(r"^\[credit:(.*)\]", line):
                credits.append(match.group(1))
            elif re.match(r"^\[ar:.*\]", line):
                has_artist = True
            elif re.match(r"^\[ti:.*\]", line):
                has_title = True
            if line.endswith("\r\n"):
                crlf = True

    prepend_artist_and_title = not has_artist and not has_title and "artist" in data and "title" in data
    prepend_beatmapset = "beatmapset" in data and data["beatmapset"] not in betamapsets
    if prepend_artist_and_title or prepend_beatmapset:
        eol = "\r\n" if crlf else "\n"
        try:
            with open(f"lyrics/{mp3_sha256}.lrc", "r+", encoding="utf-8") as f:
                b = f.read()
                f.seek(0)
                if prepend_beatmapset:
                    print(f"prepending mapsetid {data['beatmapset']} to {mp3_sha256}")
                    f.write(f"[mapsetid:{data['beatmapset']}]{eol}")
                if prepend_artist_and_title:
                    print(f"prepending artist and title to {mp3_sha256}")
                    artist = data["artist"]
                    title = data["title"]
                    f.write(f"[ar:{artist}]{eol}[ti:{title}]{eol}")
                f.write(b)
            mtime_dict[mp3_sha256] = os.path.getmtime(f"lyrics/{mp3_sha256}.lrc")
        except:
            traceback.print_exc()

    full_lyrics_cache[mp3_sha256] = l
    credits_cache[mp3_sha256] = credits
    return l

async def handle_message(websocket):
    prev_mp3_sha256 = None
    prev_full_lyrics = None
    prev_latest = None
    shown_credits = False
    async for message in websocket:
        try:
            data = json.loads(message)
            if not isinstance(data, dict) or "mp3" not in data: continue
            time = data.get("time", 0)
            mp3_sha256 = get_mp3_sha256(data["mp3"])
            full_lyrics = get_full_lyrics(mp3_sha256, data)
            just_switched_song = prev_mp3_sha256 != mp3_sha256
            if full_lyrics is None:
                if just_switched_song:
                    print("No lyrics found for mp3", mp3_sha256)
                prev_mp3_sha256 = mp3_sha256
                continue
            ready_to_show_credits = full_lyrics and time * 1000 >= full_lyrics[0][0] - 3000
            if just_switched_song or not ready_to_show_credits:
                shown_credits = False
            if ready_to_show_credits and not shown_credits:
                credits = credits_cache.get(mp3_sha256)
                if credits: await websocket.send(json_encoder.encode({"credits": credits}))
                shown_credits = True
            latest = max(t for t, *_ in [(0,)] + full_lyrics if t < time * 1000)
            if (
                not just_switched_song
                and prev_full_lyrics is full_lyrics
                and prev_latest == latest
            ): continue
            prev_mp3_sha256 = mp3_sha256
            prev_full_lyrics = full_lyrics
            prev_latest = latest
            print(mp3_sha256, time, len(full_lyrics), latest)
            lyrics = [l for t, l in full_lyrics if t == latest]
            await websocket.send(json_encoder.encode({"lyrics": lyrics}))
        except Exception:
            traceback.print_exc()

port = 20577

async def main():
    server = await websockets.serve(handle_message, "localhost", port)
    print(f"listening on localhost:{port}")
    await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        if sys.stdin.isatty():
            print("KeyboardInterrupt", file=sys.stderr)
