import os, subprocess, tempfile, shutil, hashlib
NOWIN = 0x08000000 if os.name == "nt" else 0

def tool(cfg, n):
    d = cfg.get("ffmpeg_dir")
    p = os.path.join(d, n + (".exe" if os.name == "nt" else "")) if d else None
    return p if p and os.path.exists(p) else n

def probe(cfg, path):
    r = subprocess.run([tool(cfg, "ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, creationflags=NOWIN)
    return float(r.stdout.strip())

def proxy_path(data_dir, src):
    h = hashlib.md5((src + str(os.path.getmtime(src))).encode()).hexdigest()[:12]
    os.makedirs(os.path.join(data_dir, "proxies"), exist_ok=True)
    return os.path.join(data_dir, "proxies", h + ".mp4")

def make_proxy(cfg, src, dst):
    """Лёгкая копия 480p для плавного предпросмотра на слабом ПК."""
    tmp = dst + ".tmp.mp4"
    r = subprocess.run([tool(cfg, "ffmpeg"), "-y", "-i", src, "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "30", "-g", "15", "-c:a", "aac", "-b:a", "96k", tmp], capture_output=True, text=True, creationflags=NOWIN)
    if r.returncode: raise RuntimeError(r.stderr[-300:])
    os.replace(tmp, dst); return dst

def run(cfg, p, out, cb):
    """Экспорт таймлайна в MP4. cb(доля, сообщение)."""
    if not p.clips: raise RuntimeError("Таймлайн пуст")
    tmp = tempfile.mkdtemp(); parts = []; n = len(p.clips)
    encs = ([["h264_nvenc", "-preset", "p4"]] if cfg.get("nvenc") else []) + [["libx264", "-preset", "veryfast"]]
    H = p.height
    try:
        for i, c in enumerate(p.clips):
            part = os.path.join(tmp, f"p{i}.mp4"); parts.append(part)
            if p.vertical: vf = f"crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9),scale={H}:-2,fps={p.fps}"
            else: vf = f"scale=-2:{H},fps={p.fps}"
            err = ""
            for enc in encs:
                cmd = [tool(cfg, "ffmpeg"), "-y", "-ss", str(c.start), "-i", c.path, "-t", str(c.dur), "-vf", vf,
                       "-c:v", *enc, "-b:v", f"{p.bitrate}M", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", part]
                r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN)
                if r.returncode == 0: break
                err = r.stderr[-400:]
            else: raise RuntimeError("FFmpeg: " + err)
            cb((i + 1) / (n + 1), f"Экспорт {i+1}/{n}")
        if n == 1: shutil.move(parts[0], out)
        else:
            lst = os.path.join(tmp, "list.txt")
            open(lst, "w", encoding="utf-8").write("".join("file '%s'\n" % q.replace("\\", "/") for q in parts))
            r = subprocess.run([tool(cfg, "ffmpeg"), "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out],
                               capture_output=True, text=True, creationflags=NOWIN)
            if r.returncode: raise RuntimeError("Склейка: " + r.stderr[-400:])
        cb(1.0, "Готово"); return out
    finally: shutil.rmtree(tmp, ignore_errors=True)
