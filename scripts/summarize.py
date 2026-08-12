"""用 Claude 把一場法說會的材料整理成「未來方向」條列。

## 為什麼只能靠新聞標題

法說會的價值在**前瞻指引**，但實測手上三種材料只有一種真的帶前瞻資訊：

  * 官方簡報 PDF —— 0 / 9740 場拿得到（MOPS 的簡報欄全是 `href="#"` 的 JS 表單送出）
  * MOPS 擇要訊息 —— 9740 場全有，但中位數 35 字，幾乎都是「公司營運簡介、
    ESG、公司未來展望」這種議程標題，沒有實質內容
  * 新聞標題 —— 覆蓋少，但資訊密度高。例如
    「聯發科法說2》估Q3營收年增7％至12％上看1598億元 第2代AI ASIC預計2028年初量產」
    標題本身就是指引

所以這支腳本的輸入是「MOPS 擇要訊息 ＋ 該場的新聞標題」，沒有別的。
**不抓、也不轉載任何新聞內文**，跟專案其他部分一致。

## 防幻覺

光在 prompt 裡說「不要編數字」不夠 —— 那是請求，不是保證。所以模型回來之後
還要過 `verify_outlook()` 這一關（純函式、不連網、有離線測試）：

  * `src` 對不到任何一則新聞 → 丟掉這條
  * 條目裡出現的數字，只要有一個在輸入原文裡找不到 → 丟掉這條
  * 全部條目都被丟光 → 這一場不產生摘要（前端就整張卡不顯示）

## 成本

輸入有指紋（fingerprint），內容沒變就不重打 API，所以日常執行幾乎不花錢，
只有新場次或新掛上的報導會觸發。另外有 `--max-calls` 硬上限，程式有 bug 也燒不掉錢。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys

from common import log, now_taipei, warn

MODEL = "claude-opus-5"
MAX_TOKENS = 1500

MIN_NEWS = 2            # 少於這個數量的標題撐不出摘要，不浪費 API 呼叫
WINDOW_MONTHS = 18      # 只摘要這麼近的場次
MAX_BULLETS = 5
MAX_BULLET_CHARS = 60
MAX_CALLS_DEFAULT = 200

TAGS = ["下季展望", "全年目標", "資本支出", "新產品", "營運策略", "風險"]

SCHEMA = {
    "type": "object",
    "properties": {
        "outlook": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "tag": {"type": "string", "enum": TAGS},
                    "src": {"type": "integer"},
                },
                "required": ["text", "tag", "src"],
                "additionalProperties": False,
            },
        },
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["outlook", "confidence"],
    "additionalProperties": False,
}

SYSTEM = """你是台股法說會資料的整理助理。使用者會給你某一場法人說明會的官方擇要訊息，
以及該場前後的新聞**標題**（只有標題，沒有內文）。

你的工作：從這些材料裡抽出這家公司對「未來」的說法，寫成條列。

嚴格規則：
1. 只能根據提供的材料。材料裡沒有的事情，一個字都不能寫。
2. **絕對不可以寫出材料中沒有出現過的數字** —— 包含營收、成長率、金額、年份、季別。
   不確定就不要寫數字，用文字描述即可。四捨五入或改寫數字也不行。
3. 每一條都必須標 src，代表它來自第幾則標題（從 0 開始）。對應不到某一則標題的就不要寫。
4. 只寫**前瞻**內容：展望、目標、計畫、時程、預估、產能規劃、風險提示。
   純粹描述已經發生的財報數字（上一季賺多少）不要寫。
5. 寫不出來就少寫幾條，甚至一條都不寫。寧缺勿濫。
6. 每條 40 字以內，繁體中文，直述句。不要「公司表示」「法說會中指出」這類贅詞。
7. 不做任何投資建議，不評論股價，不寫「值得關注」「後市可期」這類話。

confidence：材料充足且明確 = high；只能勉強拼出方向 = medium；材料太薄 = low。"""


# ---------------------------------------------------------------- 純函式區
# 以下都不連網、不依賴 anthropic 套件，scripts/test_parsers.py 會直接測。

def fingerprint(code: str, date: str, summary: str, news: list[dict]) -> str:
    """輸入指紋。標題有增減或改動就會變，用來決定要不要重打 API。"""
    material = [code, date, summary or ""]
    for it in news or []:
        material.append((it.get("title") or "") + "|" + (it.get("date") or ""))
    blob = "\n".join(material).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


_FULLWIDTH = {ord("０") + i: ord("0") + i for i in range(10)}
_FULLWIDTH[ord("％")] = ord("%")
_FULLWIDTH[ord("．")] = ord(".")
_FULLWIDTH[ord("，")] = ord(",")


def normalize(text: str) -> str:
    """全形數字→半形，去掉千分位逗號與空白。

    這樣「１,５９８ 億元」跟「1598億元」才會對得上。
    """
    t = (text or "").translate(_FULLWIDTH)
    return re.sub(r"[\s,]", "", t)


_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    """抓出文字裡所有數字（已正規化）。"""
    return _NUM_RE.findall(normalize(text))


def source_text(summary: str, news: list[dict]) -> str:
    """把這場的全部輸入原文串起來，當作數字比對的依據。"""
    parts = [summary or ""]
    for it in news or []:
        parts.append(it.get("title") or "")
        parts.append(it.get("date") or "")
    return "\n".join(parts)


def verify_outlook(outlook: list, news: list[dict], summary: str) -> list[dict]:
    """逐條驗證模型輸出，過不了的直接丟掉。

    這是整個功能唯一的防線，不能只靠 prompt。回傳留下來的條目。
    """
    src_pool = normalize(source_text(summary, news))
    n_news = len(news or [])
    kept: list[dict] = []

    for item in outlook or []:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        if not text or len(text) > MAX_BULLET_CHARS:
            continue

        src = item.get("src")
        if not isinstance(src, int) or isinstance(src, bool) or not (0 <= src < n_news):
            continue

        # 數字必須在輸入原文裡找得到。用「子字串」而不是完全相等，
        # 是為了容許 7.53% 被寫成 7.5%；但 1598 被寫成 1600 就會被擋下來。
        if any(num not in src_pool for num in numbers_in(text)):
            continue

        tag = item.get("tag")
        kept.append({
            "text": text,
            "tag": tag if tag in TAGS else "營運策略",
            "src": src,
        })
        if len(kept) >= MAX_BULLETS:
            break

    return kept


def needs_summary(call: dict, cache: dict, code: str, *, today: dt.date | None = None) -> bool:
    """這一場要不要（重新）呼叫 API。"""
    news = call.get("news") or []
    if len(news) < MIN_NEWS:
        return False

    date = call.get("date") or ""
    try:
        d = dt.date.fromisoformat(date)
    except ValueError:
        return False
    today = today or now_taipei().date()
    if (today - d).days > WINDOW_MONTHS * 31:
        return False

    old = cache.get(code + "|" + date)
    if old and old.get("fp") == fingerprint(code, date, call.get("summary", ""), news):
        return False
    return True


def build_prompt(name: str, code: str, call: dict) -> str:
    news = call.get("news") or []
    lines = [
        "公司：%s（%s）" % (name or code, code),
        "法說會日期：%s" % (call.get("date") or "不詳"),
        "官方擇要訊息：%s" % (call.get("summary") or "（無）"),
        "",
        "新聞標題：",
    ]
    for i, it in enumerate(news):
        lines.append("[%d] %s %s — %s" % (
            i, it.get("date") or "", it.get("source") or "", it.get("title") or ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- API 區

def _client():
    """建 Anthropic client；沒有 key 或沒裝套件就回 None，讓 build 照常跑完。"""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        warn("沒有設定 ANTHROPIC_API_KEY → 跳過「未來方向」摘要，"
             "既有的 data/summaries.json 保持不動")
        return None
    try:
        import anthropic
    except ImportError:
        warn("沒有安裝 anthropic 套件 → 跳過摘要（pip install -r scripts/requirements.txt）")
        return None
    return anthropic.Anthropic(api_key=key)


def _ask(client, name: str, code: str, call: dict) -> tuple[str, dict | None]:
    """打一次 API。回傳 (狀態, 結果)，狀態是三種之一：

      "ok"    —— 有通過驗證的條目，結果放在第二個值
      "empty" —— 呼叫成功，但材料撐不出東西（模型說 low、或條目全被驗證擋下）
      "error" —— 呼叫本身失敗（401、逾時、額度用完…）

    區分 empty 與 error 很重要：**只有 empty 可以寫進快取**。
    error 若被當成 empty 記下指紋，那一場之後就永遠不會重試了 ——
    2026-08-12 的第一次正式執行就是這樣，API key 無效導致 200 場全部
    以 401 失敗，卻全被記成「材料不足」寫進快取。
    """
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            messages=[{"role": "user", "content": build_prompt(name, code, call)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
    except Exception as e:                     # noqa: BLE001 — 任何失敗都只是略過這一場
        warn("%s %s 摘要失敗：%s: %s" % (code, call.get("date"), type(e).__name__, e))
        return "error", {"err": type(e).__name__}

    if data.get("confidence") == "low":
        return "empty", None
    kept = verify_outlook(data.get("outlook"), call.get("news") or [], call.get("summary", ""))
    if not kept:
        return "empty", None
    return "ok", {"outlook": kept, "confidence": data.get("confidence") or "medium"}


def _fatal(exc_name: str) -> bool:
    """這種錯誤重試幾次都一樣，應該整批中止而不是把配額打完。"""
    return exc_name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError")


def summarize(
    calls: dict[str, list[dict]],
    names: dict[str, str],
    cache: dict,
    *,
    codes: list[str] | None = None,
    max_calls: int = MAX_CALLS_DEFAULT,
) -> dict:
    """對需要的場次產生摘要，就地更新並回傳 cache。

    cache 只增不減 —— 來源某次抓漏了不該讓已經花錢買到的摘要消失。
    """
    todo: list[tuple[str, dict]] = []
    scope = codes if codes is not None else list(calls.keys())
    for code in scope:
        for call in calls.get(code) or []:
            if needs_summary(call, cache, code):
                todo.append((code, call))

    if not todo:
        log("🔭 未來方向摘要：沒有需要更新的場次（指紋都沒變）")
        return cache

    if len(todo) > max_calls:
        warn("需要摘要的場次有 %d 場，超過上限 %d，這次先做最新的 %d 場"
             % (len(todo), max_calls, max_calls))
        todo.sort(key=lambda x: x[1].get("date") or "", reverse=True)
        todo = todo[:max_calls]

    client = _client()
    if client is None:
        return cache

    log("🔭 未來方向摘要：%d 場要處理（model=%s）" % (len(todo), MODEL))
    ok = empty = failed = 0
    stamp = now_taipei().date().isoformat()

    for i, (code, call) in enumerate(todo):
        date = call.get("date") or ""
        status, result = _ask(client, names.get(code, ""), code, call)
        key = code + "|" + date
        fp = fingerprint(code, date, call.get("summary", ""), call.get("news") or [])

        if status == "error":
            failed += 1
            # ★ 刻意不寫進快取 —— 寫了就等於記下「這批材料試過了」，
            #   之後永遠不會重試。失敗的場次下次執行會自動再試一次。
            if _fatal((result or {}).get("err", "")):
                warn("這是設定層級的錯誤（%s），重試沒有意義，"
                     "剩下 %d 場全部中止。請確認 ANTHROPIC_API_KEY 的**值**"
                     "是不是真的 API key（sk-ant- 開頭）。"
                     % (result.get("err"), len(todo) - i - 1))
                break
            continue

        if status == "empty":
            empty += 1
            # 材料真的撐不出東西 —— 這個可以記，免得同一批標題天天重打
            cache[key] = {"fp": fp, "model": MODEL, "at": stamp,
                          "outlook": [], "confidence": "low"}
            continue

        cache[key] = {
            "fp": fp,
            "model": MODEL,
            "at": stamp,
            "outlook": result["outlook"],
            "confidence": result["confidence"],
        }
        ok += 1

    log("🔭 完成：%d 場有摘要、%d 場材料不足或沒通過驗證、%d 場呼叫失敗（下次會重試）"
        % (ok, empty, failed))
    return cache


def to_browser(calls: dict[str, list[dict]], cache: dict) -> dict:
    """把 cache 轉成前端要的 {代號: {日期: {...}}}，只輸出目前還存在的場次。"""
    out: dict[str, dict] = {}
    for code, lst in calls.items():
        for call in lst:
            entry = cache.get(code + "|" + (call.get("date") or ""))
            if entry and entry.get("outlook"):
                out.setdefault(code, {})[call["date"]] = {
                    "outlook": entry["outlook"],
                    "confidence": entry.get("confidence") or "medium",
                }
    return out


def probe_key() -> None:
    """檢查 key 設定，並實際打一次最小的請求確認它是有效的。

    不印任何 key 的內容 —— 只印長度與「開頭是不是 sk-ant-」。
    （順帶一提：如果 log 裡這段的欄位名稱被 GitHub 遮成 ***，
    代表 secret 的**值**剛好等於那個字串，也就是把名稱貼到值的欄位去了。
    2026-08-12 第一次執行就是這樣，200 場全部 401。）
    """
    log("=== PROBE: 摘要設定 ===")
    log("  model = %s" % MODEL)

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        warn("  金鑰未設定（這一步的 workflow env 有帶 secret，所以是真的沒設）")
        return
    log("  金鑰長度 = %d，sk-ant- 開頭 = %s" % (len(key), key.startswith("sk-ant-")))
    if not key.startswith("sk-ant-"):
        warn("  ⚠️ 值看起來不像 API key。請確認貼進 secret 的是金鑰本身，不是名稱。")

    try:
        import anthropic
        log("  anthropic 套件 = %s" % getattr(anthropic, "__version__", "?"))
    except ImportError:
        warn("  anthropic 套件 = 未安裝")
        return

    # 實際打一次 1 token 的請求，這是唯一能確定 key 有效的方法（成本 ≈ 0）
    try:
        anthropic.Anthropic(api_key=key).messages.create(
            model=MODEL, max_tokens=1, messages=[{"role": "user", "content": "hi"}])
        log("  ✅ 實際呼叫成功，金鑰有效")
    except Exception as e:                     # noqa: BLE001
        warn("  ❌ 實際呼叫失敗：%s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe_key()
    else:
        log("這支腳本由 scripts/build.py 呼叫。單獨執行請加 --probe。")
