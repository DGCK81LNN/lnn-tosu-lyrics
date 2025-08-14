import hashlib
import json
import os
import re
from sanic import Sanic, response
from sanic.log import logger, error_logger
from sanic.handlers import ContentRangeHandler

app = Sanic("TosuLyrics")

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
def parse_lyric_file(filepath):
    l = []
    offset = 0
    time_scale = 1
    artist = None
    title = None
    crlf = False
    credits = []
    betamapsets = set()
    with open(filepath, "r", encoding="utf-8") as f:
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
            elif match := re.match(r"^\[ar:(.*)\]", line):
                artist = match.group(1)
            elif match := re.match(r"^\[ti:(.*)\]", line):
                title = match.group(1)
            if line.endswith("\r\n"):
                crlf = True
    return {
        "lyrics": l,
        "offset": offset,
        "time_scale": time_scale,
        "artist": artist,
        "title": title,
        "crlf": crlf,
        "credits": credits,
        "betamapsets": betamapsets,
    }

def get_full_lyrics(mp3_sha256, data={}):
    file_present = os.path.isfile(f"lyrics/{mp3_sha256}.lrc") and os.path.getsize(f"lyrics/{mp3_sha256}.lrc") > 0
    if file_present:
        mtime = os.path.getmtime(f"lyrics/{mp3_sha256}.lrc")
        if mp3_sha256 in mtime_dict and mp3_sha256 in full_lyrics_cache and mtime_dict[mp3_sha256] != mtime:
            logger.info(f"reload {mp3_sha256}")
            del full_lyrics_cache[mp3_sha256]
        mtime_dict[mp3_sha256] = mtime
    if mp3_sha256 in full_lyrics_cache:
        return full_lyrics_cache[mp3_sha256]
    if not file_present:
        return None

    parsed = parse_lyric_file(f"lyrics/{mp3_sha256}.lrc")
    l = parsed["lyrics"]
    has_artist = parsed["artist"] is not None
    has_title = parsed["title"] is not None
    crlf = parsed["crlf"]
    credits = parsed["credits"]
    betamapsets = parsed["betamapsets"]

    prepend_artist_and_title = not has_artist and not has_title and "artist" in data and "title" in data
    prepend_beatmapset = "beatmapset" in data and data["beatmapset"] not in betamapsets
    if prepend_artist_and_title or prepend_beatmapset:
        eol = "\r\n" if crlf else "\n"
        try:
            with open(f"lyrics/{mp3_sha256}.lrc", "r+", encoding="utf-8") as f:
                b = f.read()
                f.seek(0)
                if prepend_beatmapset:
                    logger.info(f"prepending mapsetid {data['beatmapset']} to {mp3_sha256}")
                    f.write(f"[mapsetid:{data['beatmapset']}]{eol}")
                if prepend_artist_and_title:
                    logger.info(f"prepending artist and title to {mp3_sha256}")
                    artist = data["artist"]
                    title = data["title"]
                    f.write(f"[ar:{artist}]{eol}[ti:{title}]{eol}")
                f.write(b)
            mtime_dict[mp3_sha256] = os.path.getmtime(f"lyrics/{mp3_sha256}.lrc")

        except Exception:
            error_logger.warning("Error prepending metadata", exc_info=True)

    full_lyrics_cache[mp3_sha256] = l
    credits_cache[mp3_sha256] = credits
    return l

@app.websocket("/ws")
async def handle_message(request, ws):
    prev_mp3_sha256 = None
    prev_full_lyrics = None
    prev_latest = None
    shown_credits = False
    async for message in ws:
        try:
            data = json.loads(message)
            if not isinstance(data, dict): continue
            if "mp3" not in data: continue
            time = data.get("time", 0)
            mp3_sha256 = get_mp3_sha256(data["mp3"])
            full_lyrics = get_full_lyrics(mp3_sha256, data)
            just_switched_song = prev_mp3_sha256 != mp3_sha256
            if full_lyrics is None:
                if just_switched_song:
                    logger.info("No lyrics found for mp3", mp3_sha256)
                prev_mp3_sha256 = mp3_sha256
                continue
            ready_to_show_credits = full_lyrics and time * 1000 >= max(0, full_lyrics[0][0] - 3000)
            if just_switched_song or not ready_to_show_credits:
                shown_credits = False
            if ready_to_show_credits and not shown_credits:
                credits = credits_cache.get(mp3_sha256)
                if credits: await ws.send(json_encoder.encode({"credits": credits}))
                shown_credits = True
            latest = max([float("-inf"), *(t for t, _ in full_lyrics if t < time * 1000)])
            if (
                not just_switched_song
                and prev_full_lyrics is full_lyrics
                and prev_latest == latest
            ): continue
            prev_mp3_sha256 = mp3_sha256
            prev_full_lyrics = full_lyrics
            prev_latest = latest
            logger.debug(f"{mp3_sha256} time={time} len={len(full_lyrics)} latest={latest}")
            lyrics = [l for t, l in full_lyrics if t == latest]
            await ws.send(json_encoder.encode({"lyrics": lyrics}))
        except Exception:
            error_logger.warning("Error handling incoming message", exc_info=True)

@app.get("/")
async def player(request):
    return await response.file("player.html")

@app.get("/mp3s")
async def list_mp3s(request):
    mp3s = []
    for filename in os.listdir("lyrics"):
        match = re.match(r"^([0-9a-f]{64})\.lrc$", filename)
        if not match:
            continue
        try:
            filepath = os.path.join("lyrics", filename)
            parsed = parse_lyric_file(filepath)
            mtime = os.path.getmtime(filepath)
        except Exception:
            error_logger.warning("Error parsing lyric file in list_mp3s", exc_info=True)
            continue
        mp3_sha256 = match.group(1)
        mp3s.append({
            "sha256": mp3_sha256,
            "artist": parsed["artist"],
            "title": parsed["title"],
            "credits": parsed["credits"],
            "betamapsets": list(parsed["betamapsets"]),
            "mtime": mtime,
        })
    mp3s.sort(key=lambda x: x["mtime"], reverse=True)
    for mp3 in mp3s:
        del mp3["mtime"]
    return response.json(mp3s)

@app.get("/mp3s/<mp3_sha256>")
async def get_mp3(request, mp3_sha256):
    if not re.match(r"^[0-9a-f]{64}$", mp3_sha256):
        return response.text("Invalid mp3 SHA256", status=400)
    mp3_path = os.path.join(realm_files_base, mp3_sha256[0], mp3_sha256[0:2], mp3_sha256)
    if not os.path.exists(mp3_path):
        return response.text("MP3 not found", status=404)

    stat = os.stat(mp3_path)

    return await response.file(
        mp3_path,
        mime_type="audio/mpeg",
        headers={
            "Accept-Ranges": "bytes",
        },
        _range=ContentRangeHandler(request, stat),
    )

@app.head("/mp3s/<mp3_sha256>")
async def head_mp3(request, mp3_sha256):
    if not re.match(r"^[0-9a-f]{64}$", mp3_sha256):
        return response.text("Invalid mp3 SHA256", status=400)
    mp3_path = os.path.join(realm_files_base, mp3_sha256[0], mp3_sha256[0:2], mp3_sha256)
    if not os.path.exists(mp3_path):
        return response.text("MP3 not found", status=404)
    return response.empty(headers={
        "Content-Length": str(os.path.getsize(mp3_path)),
        "Content-Type": "audio/mpeg",
        "Accept-Ranges": "bytes",
    })

app.static("/lib", "lib", name="lib")
app.static("/utils.js", "utils.js", name="utils_js")

port = 20577
realm_files_base = r"D:\LNNSoftware\_osulazer data\files"

if __name__ == "__main__":
    app.run("0.0.0.0", port, debug=True)
