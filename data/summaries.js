/**
 * 法說會「未來方向」摘要  —  window.SUMMARIES
 * ⚠️ 這個檔案由 scripts/build.py 自動產生，請不要手動編輯。
 *
 * 這些條目是 Claude 依據該場次的**新聞標題與 MOPS 擇要訊息**整理出來的，
 * 不是公司原文，也不是任何投資建議。每一條都帶 src，指回它引用的那一則報導。
 *
 * 產生後有經過程式驗證：條目裡出現的數字，一定要在輸入原文中找得到，
 * 否則整條丟掉（見 scripts/summarize.py 的 verify_outlook）。
 *
 * 目前是空的 —— 要等 repo 設好 ANTHROPIC_API_KEY secret、Action 跑過一次才會有內容。
 * 沒有內容時前端不會顯示「未來方向」那張卡，其餘功能完全不受影響。
 */
window.SUMMARIES = {"updated":"","items":{}};
