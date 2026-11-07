"""Qwen через Ollama: только белый список команд, без выполнения произвольного кода."""
import json, re, requests

SYSTEM = """Ты — помощник видеомонтажёра в редакторе MishaClip Studio. Управляй ТОЛЬКО этими инструментами:
add_clip(start,end) — добавить фрагмент активного видео на таймлайн (секунды исходника)
split_at(time) — разрезать на таймлайне в секунду time (без аргумента — в позиции курсора)
trim_start(index,seconds) — обрезать начало клипа (index с 1; без index — выбранный)
trim_end(index,seconds) — обрезать конец клипа
delete_clip(index) — удалить клип
join_clips(index) — склеить клип со следующим, если они идут подряд из одного файла
move_clip(index,direction) — direction: -1 влево, 1 вправо
set_vertical(on) — формат 9:16 (true/false)
set_export(height,fps,bitrate) — настройки экспорта, bitrate в Мбит/с
export() — запустить экспорт
save() — сохранить проект
find_moments(count,topic) — найти интересные моменты по расшифровке речи и добавить клипы
Отвечай СТРОГО JSON: {"say":"короткий ответ по-русски","actions":[{"tool":"имя","args":{...}}]}
Если действий не нужно — actions пустой. Время в секундах числом. Не придумывай других инструментов."""

def parse_time(s):
    t = 0.0
    for p in str(s).replace(",", ".").split(":"): t = t * 60 + float(p)
    return t

def fmt(t):
    t = int(t); return f"{t//3600:02d}:{t%3600//60:02d}:{t%60:02d}"

def rule_parse(text):
    """Запасной разбор простых команд без нейросети."""
    t = text.lower(); acts = []
    m = re.search(r"добавь\s+с\s+([\d:]+)\s+по\s+([\d:]+)", t)
    if m: acts.append({"tool": "add_clip", "args": {"start": parse_time(m[1]), "end": parse_time(m[2])}})
    if "раздели" in t or "разрежь" in t: acts.append({"tool": "split_at", "args": {}})
    m = re.search(r"обрежь\s+начало\s+на\s+(\d+)", t)
    if m: acts.append({"tool": "trim_start", "args": {"seconds": int(m[1])}})
    m = re.search(r"обрежь\s+конец\s+на\s+(\d+)", t)
    if m: acts.append({"tool": "trim_end", "args": {"seconds": int(m[1])}})
    if "склей" in t: acts.append({"tool": "join_clips", "args": {}})
    if "вертикал" in t or "tiktok" in t or "тикток" in t: acts.append({"tool": "set_vertical", "args": {"on": True}})
    if "экспорт" in t:
        m = re.search(r"(\d{3,4})p\s*(\d{2,3})?", t)
        if m: acts.append({"tool": "set_export", "args": {"height": int(m[1]), "fps": int(m[2] or 30)}})
        acts.append({"tool": "export", "args": {}})
    if "сохрани" in t: acts.append({"tool": "save", "args": {}})
    m = re.search(r"найди\D*(\d+)?", t)
    if m and ("момент" in t or "смешн" in t): acts.append({"tool": "find_moments", "args": {"count": int(m[1] or 5), "topic": "смешные и яркие"}})
    return {"say": "Выполняю (без нейросети)." if acts else "Не понял команду.", "actions": acts}

def _chat(cfg, messages, fmt_json=True, timeout=300):
    body = {"model": cfg["model"], "stream": False, "options": {"temperature": 0.2, "num_ctx": 4096}, "messages": messages}
    if fmt_json: body["format"] = "json"
    r = requests.post(cfg["ollama"] + "/api/chat", json=body, timeout=timeout); r.raise_for_status()
    return json.loads(r.json()["message"]["content"])

def ask_qwen(cfg, state, text):
    try:
        data = _chat(cfg, [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": f"Состояние проекта: {json.dumps(state, ensure_ascii=False)}\nЗапрос: {text}"}])
        data.setdefault("actions", []); data.setdefault("say", ""); return data
    except Exception as e:
        out = rule_parse(text); out["say"] += f" (Qwen недоступна: {e})"; return out

def transcribe(cfg, path, progress=None):
    from faster_whisper import WhisperModel
    model = WhisperModel(cfg.get("whisper_model", "tiny"), device="cpu", compute_type="int8")
    segs, info = model.transcribe(path, language="ru", vad_filter=True)
    out = []
    for s in segs:
        out.append((s.start, s.end, s.text.strip()))
        if progress and info.duration: progress(min(0.99, s.end / info.duration), "Расшифровка речи…")
    return out

def pick_moments(cfg, segments, count, topic, progress=None):
    chunks, cur, size = [], [], 0
    for s in segments:   # частями, чтобы не забивать память
        line = f"[{s[0]:.0f}-{s[1]:.0f}] {s[2]}"; cur.append(line); size += len(line)
        if size > 3500: chunks.append("\n".join(cur)); cur, size = [], 0
    if cur: chunks.append("\n".join(cur))
    found = []
    for k, ch in enumerate(chunks):
        if progress: progress(k / max(1, len(chunks)), "Qwen ищет моменты…")
        prompt = (f"Расшифровка стрима. Найди до 3 самых интересных моментов ({topic}), каждый 15–60 секунд. "
                  'Ответ JSON: {"moments":[{"start":сек,"end":сек,"score":1-10,"reason":"..."}]}\n\n' + ch)
        try: found += _chat(cfg, [{"role": "user", "content": prompt}]).get("moments", [])
        except Exception: pass
    ok = [m for m in found if isinstance(m.get("start"), (int, float)) and isinstance(m.get("end"), (int, float)) and m["end"] > m["start"]]
    ok.sort(key=lambda m: -m.get("score", 0)); return ok[:count]
